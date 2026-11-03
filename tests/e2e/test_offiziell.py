"""S 2: Offizielles Ergebnis am Gerät: Sperre mit Erklärung, abgelehnte Einträge bleiben auf dem
Gerät, gehen nicht in Dauerschleife und nach dem Zurücksetzen von selbst durch."""

import time
import uuid

from playwright.sync_api import expect

from tests.e2e.conftest import jetzt_synchronisieren, oeffnen, offline, warte_bis


def erklaeren(server, wert):
    jetzt = int(time.time() * 1000)
    server.senden(
        [{"uuid": str(uuid.uuid4()), "art": "ERGEBNIS", "wert": wert, "notiz": "Test", "geraet": "admin",
          "erstellt_ms": jetzt}],
        rolle="admin",
    )  # fmt: skip


def starts(server):
    return [e for e in server.ereignisse() if e["art"] == "START"]


def test_offiziell_sperrt_und_zuruecksetzen_gibt_frei(server, neues_geraet):
    p = oeffnen(neues_geraet(), server, "start")
    expect(p.locator("#btn-start")).to_be_visible()
    erklaeren(server, "OFFIZIELL")
    jetzt_synchronisieren(p)
    expect(p.locator("#sperre h1")).to_have_text("Ergebnis ist offiziell")
    expect(p.locator("#sperre-text")).to_contain_text("„vorläufig“")
    expect(p.locator("#sperre-fehlt")).to_be_hidden()
    expect(p.locator("#btn-start")).to_be_hidden()
    erklaeren(server, "VORLAEUFIG")
    jetzt_synchronisieren(p)
    expect(p.locator("#sperre")).to_be_hidden()
    expect(p.locator("#btn-start")).to_be_visible()


def test_offline_erfasst_wird_abgelehnt_und_geht_nach_dem_zuruecksetzen_durch(server, neues_geraet):
    ctx = neues_geraet()
    p = oeffnen(ctx, server, "start")
    expect(p.locator("#btn-start")).to_be_visible()
    offline(ctx, True)
    erklaeren(server, "OFFIZIELL")
    p.click("#btn-start")  # offline bleibt die Erfassung möglich
    expect(p.locator("#ausstehend")).to_have_text("1 ausstehend")
    offline(ctx, False)
    jetzt_synchronisieren(p)
    expect(p.locator("#ausstehend")).to_have_text(
        "0 ausstehend · 1 abgelehnt"
    )  # keine Dauerschleife
    expect(p.locator("#abgelehnt-box")).to_be_visible()
    expect(p.locator("#abgelehnt-text")).to_have_text(
        "1 Eintrag nicht angenommen – Rennleitung informieren"
    )
    expect(p.locator("#abgelehnt-grund")).to_contain_text("„vorläufig“")
    expect(p.locator("#btn-abgelehnt-exportieren")).to_be_visible()
    assert starts(server) == []
    p.reload()  # übersteht Neuladen
    p.wait_for_function("window.geraetBereit === true")
    expect(p.locator("#abgelehnt-box")).to_be_visible()

    erklaeren(server, "VORLAEUFIG")
    jetzt_synchronisieren(p)
    warte_bis(lambda: len(starts(server)) == 1, text="Start nach dem Zurücksetzen übertragen")
    expect(p.locator("#ausstehend")).to_have_text("0 ausstehend")
    expect(p.locator("#abgelehnt-box")).to_be_hidden()


def test_fremdes_storno_wird_mit_grund_abgelehnt(server, neues_geraet):
    """Stammt ein Eintrag von einem anderen Gerät: Ablehnung mit Grund, Warteschlange läuft weiter."""
    server.starten(1)
    fremd = starts(server)[0]["uuid"]
    p = oeffnen(neues_geraet(), server, "start")
    expect(p.locator("#btn-start")).to_be_visible()
    p.evaluate(
        "(u) => window.geraet.erfasse({art: 'STORNO', bezug_uuid: u, startnummer: 1})", fremd
    )
    jetzt_synchronisieren(p)
    expect(p.locator("#abgelehnt-grund")).to_contain_text("anderen Gerät")
    p.click("#btn-start")
    warte_bis(lambda: len(starts(server)) == 2, text="nächster Start trotz Ablehnung")
