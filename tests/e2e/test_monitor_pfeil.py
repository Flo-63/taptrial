"""„mit Pfeil und Differenz“ folgt dem Feld – im Reiter „Zielmonitor und Ergebnisse“ und
im Assistenten (gemeinsamer Baustein): Feld abgewählt → Pfeil abgewählt und gesperrt; wieder angewählt
→ frei, mit der Vorgabe des Assistenten. Der Server speichert keinen Pfeil für ein nicht gewähltes Feld."""

import json
import sqlite3

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import TOKENS, einstellungen


@pytest.fixture
def rundkurs():
    return "eigen"


def feld(a, name):
    zeile = a.locator("#monitor-live .live-feld", has=a.get_by_text(name, exact=True))
    return zeile.locator("input[name=live_abschnitt]"), zeile.locator("input[name=live_vergleich]")


def konf(db):
    with sqlite3.connect(db) as c:
        return json.loads(
            c.execute("select monitor_json from veranstaltung where id = 1").fetchone()[0]
        )


@pytest.mark.parametrize("ort", ["reiter", "assistent"])
def test_pfeil_folgt_dem_feld(browser, server, tmp_path, ort):
    db = tmp_path / "daten" / "zeitfahren.sqlite3"
    a = browser.new_context(viewport={"width": 1366, "height": 900}).new_page()
    a.goto(f"{server.url}/admin")
    a.fill("input[name=token]", TOKENS["admin"])
    a.click("#anmelden button")
    if ort == "assistent":  # Veranstaltung steht im Assistenten bei Schritt 4 (Vorgabe: alles an)
        with sqlite3.connect(db) as c:
            c.execute(
                "update veranstaltung set einrichtung_schritt = 4, monitor_json = null where id = 1"
            )
        a.goto(f"{server.url}/admin/einrichtung/1/4")
    else:  # Reiter, noch keine Felder gewählt: alle Pfeile gesperrt
        a.click("#veranstaltungen a")
        einstellungen(a, "zielmonitor")
        expect(a.locator("#monitor-live input[name=live_vergleich]:enabled")).to_have_count(0)
    segment, pfeil = feld(a, "Kreisel→Berg")
    if ort == "reiter":
        segment.check()
    expect(pfeil).to_be_enabled()
    expect(pfeil).to_be_checked()  # Vorgabe: mehrere Durchfahrten → mit Pfeil
    segment.uncheck()
    expect(pfeil).not_to_be_checked()
    expect(pfeil).to_be_disabled()
    segment.check()
    expect(pfeil).to_be_enabled()
    expect(pfeil).to_be_checked()
    runde, runde_pfeil = feld(a, "Runde")
    runde.check()
    runde_pfeil.check()
    runde.uncheck()
    expect(runde_pfeil).to_be_disabled()
    runde_key, segment_key = runde.get_attribute("value"), segment.get_attribute("value")
    a.click("#weiter" if ort == "assistent" else "#zielmonitor-speichern")
    if ort == "assistent":
        expect(a.locator("#zusammenfassung")).to_be_visible()
    else:
        expect(a.locator(".meldung")).to_contain_text("Zielmonitor gespeichert")
    gespeichert = konf(db)
    assert gespeichert["live_vergleich"] and all(
        k in gespeichert["live_abschnitte"] for k in gespeichert["live_vergleich"]
    )
    assert (
        runde_key not in gespeichert["live_abschnitte"]
        and runde_key not in gespeichert["live_vergleich"]
    )
    assert segment_key in gespeichert["live_vergleich"]
    # erneut geöffnet: nicht gewähltes Feld hat einen gesperrten, leeren Pfeil
    a.goto(f"{server.url}/admin/einrichtung/1/4" if ort == "assistent" else a.url)
    if ort == "reiter":
        einstellungen(a, "zielmonitor")
    runde, runde_pfeil = feld(a, "Runde")
    expect(runde).not_to_be_checked()
    expect(runde_pfeil).to_be_disabled()
    expect(runde_pfeil).not_to_be_checked()
