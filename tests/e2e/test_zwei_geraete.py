"""Zwei Geräte an einem Messpunkt: Posten-Zähler springt nicht, Ziel-Kachel taucht nicht wieder auf."""

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import jetzt_synchronisieren, oeffnen, warte_bis
from tests.e2e.test_ziel import kachel


@pytest.fixture
def rundkurs():
    return "messpunkte"


def test_zwei_posten_am_kreisel_zaehlen_eine_durchfahrt(server, neues_geraet):
    server.starten(1, 2)
    a = oeffnen(neues_geraet(), server, "posten")
    b = oeffnen(neues_geraet(), server, "posten")
    kachel(a, 1).click()
    kachel(b, 1).click()
    jetzt_synchronisieren(a)
    jetzt_synchronisieren(b)
    warte_bis(lambda: len([e for e in server.ereignisse() if e["art"] == "DURCHFAHRT"]) == 2,
              text="zwei Durchfahrten")  # fmt: skip
    assert server.teilnehmer(1)["naechste_runde"]["1"] == 2  # nicht 3: zweiter Tipp ist Doppeltipp
    for p in (a, b):
        jetzt_synchronisieren(p)
        expect(kachel(p, 1).locator(".kachel-runde")).to_have_text("2. von 3 Durchfahrten")


def test_zwei_zielgeraete_kachel_bleibt_weg(server, neues_geraet):
    server.starten(1, 2)
    a = oeffnen(neues_geraet(), server, "ziel")
    b = oeffnen(neues_geraet(), server, "ziel")
    kachel(a, 1).click()
    kachel(b, 1).click()
    jetzt_synchronisieren(a)
    jetzt_synchronisieren(b)
    warte_bis(lambda: server.teilnehmer(1)["status"] == "KONFLIKT", text="Konflikt Nr. 1")
    assert 1 in server.stand()["im_ziel"]
    for z in (a, b):
        jetzt_synchronisieren(z)
        z.wait_for_timeout(1_500)  # Entprellung vorbei: früher erschien die Kachel hier wieder
        expect(kachel(z, 1)).to_have_count(0)
        expect(z.locator("#ziel-info")).to_contain_text("1 auf der Strecke")
