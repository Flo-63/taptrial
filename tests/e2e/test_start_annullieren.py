"""K 4: Rennleitung annulliert einen versehentlichen Start – Pflichtgrund, Warnung mit den
Tipps danach, eigene Bestätigung; zurücknehmbar. Fall vom 31.10.: Nr. 1 versehentlich gestartet,
Fehlklick am Posten."""

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import jetzt_synchronisieren, oeffnen, warte_bis
from tests.e2e.test_ziel import rennleitung


@pytest.fixture
def rundkurs():
    return "messpunkte"


def test_start_annullieren_mit_warnung_und_zuruecknehmen(server, neues_geraet):
    server.starten(1, 2)
    p = oeffnen(neues_geraet(), server, "posten")
    p.locator("#fahrer .kachel[data-nr='1']").click()
    jetzt_synchronisieren(p)
    warte_bis(lambda: any(e["art"] == "DURCHFAHRT" for e in server.ereignisse()), text="Durchfahrt")

    z = oeffnen(neues_geraet(), server, "ziel")
    rennleitung(z)
    z.click("#rl-tab-status")
    z.click("#status-auswahl .kachel[data-nr='1']")
    knopf = z.locator("#btn-status-annullieren")
    expect(knopf).to_be_visible()
    expect(knopf).to_be_disabled()  # Grund fehlt
    warnung = z.locator("#annullieren-warnung")
    expect(warnung).to_be_visible()
    expect(warnung).to_contain_text("Nr. 1 hat nach dem Start schon 1 Tipp")
    expect(z.locator("#annullieren-tipps li")).to_have_count(1)
    expect(z.locator("#annullieren-tipps li").first).to_contain_text("Kreisel")
    z.fill("#status-grund", "nicht angetreten, versehentlich gestartet")
    expect(knopf).to_be_disabled()  # eigene Bestätigung der Tipps fehlt
    z.check("#annullieren-trotzdem")
    expect(knopf).to_be_enabled()
    knopf.click()
    expect(knopf).to_have_text("Wirklich Start annullieren?")
    knopf.click()
    jetzt_synchronisieren(z)
    warte_bis(lambda: server.teilnehmer(1)["status"] == "DNS", text="Nr. 1 DNS")
    assert server.teilnehmer(1)["start_ms"] is None and server.stand()["gestartet"] == [2]
    (a,) = [e for e in server.ereignisse() if e["art"] == "START_ANNULLIERT"]
    assert a["notiz"] == "nicht angetreten, versehentlich gestartet" and a["startnummer"] == 1
    # Fahrer 2 ohne Tipps: keine Warnung
    z.click("#status-auswahl .kachel[data-nr='2']")
    expect(z.locator("#annullieren-warnung")).to_be_hidden()
    # zurücknehmen (Storno der Annullierung): Start gilt wieder
    z.click("#status-auswahl .kachel[data-nr='1']")
    zurueck = z.locator("#btn-annullierung-zurueck")
    expect(zurueck).to_be_visible()
    expect(z.locator("#btn-status-annullieren")).to_be_hidden()
    zurueck.click()
    zurueck.click()
    jetzt_synchronisieren(z)
    warte_bis(lambda: server.teilnehmer(1)["start_ms"] is not None, text="Start wieder gültig")
    assert server.teilnehmer(1)["status"] != "DNS"
