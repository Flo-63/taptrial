"""Posten-Gerät wie Start/Ziel: Statusleiste mit Uhr, Zurücksetzen, Hilfe."""

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import oeffnen
from tests.e2e.test_ziel import kachel


@pytest.fixture
def rundkurs():
    return "messpunkte"


def test_posten_seite_wie_start_und_ziel(server, neues_geraet):
    server.starten(1, 2)
    p = oeffnen(neues_geraet(), server, "posten")
    expect(p.locator("#posten-titel")).to_contain_text("Kreisel")
    expect(p.locator(".statusleiste .rolle")).to_have_text("Posten")
    expect(p.locator("#netz")).to_have_text("online")
    expect(p.locator("#uhr-pruefung")).to_contain_text("Uhr ±")
    assert p.locator("#link-hilfe").get_attribute("href") == "/hilfe/posten"
    expect(kachel(p, 1).locator(".kachel-runde")).to_have_text("1. von 3 Durchfahrten")
    p.click("#btn-zuruecksetzen")
    p.click("#btn-zuruecksetzen")
    expect(p.locator("#ende-text")).to_contain_text("zurückgesetzt")


class TestGemeinsamerMesspunkt:
    """Der Posten zeigt nur Fahrer der Strecken, die an seinem Messpunkt vorbeiführen."""

    @pytest.fixture
    def rundkurs(self):
        return "gemeinsam"

    @pytest.fixture
    def zwei_strecken(self):
        return True

    def test_kacheln_beider_strecken_mit_kennzeichen(self, server, neues_geraet):
        server.starten(1, 2, 3, 4)
        p = oeffnen(neues_geraet(), server, "posten")
        # Kreisel: Damen (ungerade Nr., 5 km, einmal) und Herren (gerade Nr., 20 km, dreimal)
        expect(kachel(p, 1)).to_be_visible()
        expect(kachel(p, 2).locator(".kachel-runde")).to_have_text("1. von 3 Durchfahrten")
        expect(kachel(p, 1).locator(".kachel-runde")).to_have_count(0)
        expect(kachel(p, 1).locator(".kachel-strecke")).to_have_text("5 km")
        expect(kachel(p, 2).locator(".kachel-strecke")).to_have_text("20 km")
        b = oeffnen(neues_geraet(), server, "posten-berg")
        expect(kachel(b, 2)).to_be_visible()
        expect(kachel(b, 1)).to_have_count(0)  # die kurze Strecke führt nicht am Berg vorbei


def test_durchfahrten_anzeige_springt_nach_vergessenem_klick_und_schalter(server, neues_geraet):
    """„1. von 2 Durchfahrten“ mit Erklärzeile; Klick am Berg vergessen, nächste Zeit
    am Kreisel da → am Berg gilt die 1. als verpasst; Schalter „Durchfahrten anzeigen“ gemerkt."""
    import uuid as _uuid

    from tests.e2e.conftest import jetzt_synchronisieren

    t0 = server.starten(1)[1]

    def durch(t_min, rolle, mp):
        server.senden([{"uuid": str(_uuid.uuid4()), "art": "DURCHFAHRT", "startnummer": 1, "wert": mp,
                        "zeit_ms": t0 + t_min * 60_000, "erstellt_ms": t0 + t_min * 60_000, "geraet": rolle}],
                      rolle=rolle)  # fmt: skip

    ctx = neues_geraet()
    berg = oeffnen(ctx, server, "posten-berg")
    expect(kachel(berg, 1).locator(".kachel-runde")).to_have_text("1. von 2 Durchfahrten")
    expect(berg.locator("#durchfahrten-hinweis")).to_have_text(
        "Unter jeder Nummer: die wievielte Durchfahrt hier als Nächstes kommt."
    )
    durch(3, "posten", "1")  # Kreisel (1. Durchfahrt)
    durch(13, "posten", "1")  # Kreisel 2. – Berg dazwischen nicht getippt
    jetzt_synchronisieren(berg)
    expect(kachel(berg, 1).locator(".kachel-runde")).to_have_text("2. von 2 Durchfahrten")
    # Schalter aus: nur die Nummer, auch nach dem Neuladen
    berg.click("#toggle-durchfahrten")
    expect(berg.locator("#toggle-durchfahrten")).to_have_attribute("aria-pressed", "false")
    expect(kachel(berg, 1).locator(".kachel-runde")).to_have_count(0)
    expect(berg.locator("#durchfahrten-hinweis")).to_be_hidden()
    berg.reload()
    berg.wait_for_function("window.geraetBereit === true")
    expect(kachel(berg, 1).locator(".kachel-runde")).to_have_count(0)
    # letzte Durchfahrt am Berg → Kachel verschwindet auch bei ausgeschaltetem Schalter
    durch(18, "posten-berg", "2")
    jetzt_synchronisieren(berg)
    expect(kachel(berg, 1)).to_have_count(0)
    # „Gerät zurücksetzen“ stellt den Standard wieder her
    berg.click("#btn-zuruecksetzen")
    berg.click("#btn-zuruecksetzen")
    expect(berg.locator("#ende-text")).to_contain_text("zurückgesetzt")
    assert (
        berg.evaluate("localStorage.getItem(window.geraet.schluessel('anzeige_durchfahrten'))")
        is None
    )


def test_fehlklick_anderswo_fahrer_bleibt_erreichbar(server, neues_geraet):
    """Ein Fehlklick am Kreisel (dritte Durchfahrt zu früh) lässt Nr. 1 am Berg als
    vorbei gelten – nach dem Rückgängig ist er dort wieder eine Kachel; mit „Alle Fahrer“ bleibt er
    auch so antippbar, und der Klick zählt."""
    import uuid as _uuid

    from tests.e2e.conftest import jetzt_synchronisieren, warte_bis

    t0 = server.starten(1)[1]

    def kreisel(t_min):
        e = {"uuid": str(_uuid.uuid4()), "art": "DURCHFAHRT", "startnummer": 1, "wert": "1",
             "zeit_ms": t0 + t_min * 60_000, "erstellt_ms": t0 + t_min * 60_000, "geraet": "posten-x"}  # fmt: skip
        server.senden([e], rolle="posten")
        return e

    def zuruecknehmen(e):
        server.senden([{"uuid": str(_uuid.uuid4()), "art": "STORNO", "bezug_uuid": e["uuid"], "startnummer": 1,
                        "erstellt_ms": e["erstellt_ms"] + 1, "geraet": "posten-x"}], rolle="posten")  # fmt: skip

    kreisel(3)
    kreisel(13)
    fehl = kreisel(14)  # Fehlklick: zählt als 3. Durchfahrt am Kreisel – am Berg alles „vorbei“
    berg = oeffnen(neues_geraet(), server, "posten-berg")
    jetzt_synchronisieren(berg)
    expect(kachel(berg, 1)).to_have_count(0)
    zuruecknehmen(fehl)
    jetzt_synchronisieren(berg)
    expect(kachel(berg, 1).locator(".kachel-runde")).to_have_text("2. von 2 Durchfahrten")
    kreisel(15)  # erneuter Fehlklick
    jetzt_synchronisieren(berg)
    expect(kachel(berg, 1)).to_have_count(0)
    berg.click("#toggle-alle")
    expect(kachel(berg, 1).locator(".kachel-vorbei")).to_have_text("gilt als vorbei")
    kachel(berg, 1).click()
    jetzt_synchronisieren(berg)
    warte_bis(lambda: any(e["art"] == "DURCHFAHRT" and e["wert"] == "2" for e in server.ereignisse()),
              text="Klick am Berg übertragen")  # fmt: skip
    berg.click("#toggle-alle")
    expect(berg.locator("#toggle-alle")).to_have_attribute("aria-pressed", "false")
