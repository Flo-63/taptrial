"""K 3: Gleiche Nachnamen auf Ziel, Posten und in der Rennleitung unterscheidbar."""

import pytest
from playwright.sync_api import expect

from app.simulation import Fahrer
from tests.e2e.conftest import jetzt_synchronisieren, oeffnen
from tests.e2e.test_ziel import kachel


@pytest.fixture
def teilnehmer_liste():
    return [
        Fahrer(1, "Anna", "Müller", "w", 1990),
        Fahrer(2, "Bernd", "Müller", "m", 1985),
        Fahrer(3, "Clara", "Schmidt", "w", 1980),
    ]


@pytest.fixture
def rundkurs():
    return "messpunkte"


def test_gleiche_nachnamen_auf_geraeten(server, neues_geraet):
    server.starten(1, 2, 3)
    z = oeffnen(neues_geraet(), server, "ziel")
    expect(kachel(z, 1).locator(".kachel-name")).to_have_text("Müller A.")
    expect(kachel(z, 2).locator(".kachel-name")).to_have_text("Müller B.")
    expect(kachel(z, 3).locator(".kachel-name")).to_have_text("Schmidt")
    kachel(z, 2).click()
    jetzt_synchronisieren(z)
    z.click("#tab-rennleitung")
    z.click("#rl-tab-letzte")
    expect(z.locator("#letzte li").first).to_contain_text("Nr. 2 Müller B.")
    p = oeffnen(neues_geraet(), server, "posten")
    expect(kachel(p, 1).locator(".kachel-name")).to_have_text("Müller A.")
    kachel(p, 1).click()
    expect(p.locator("#letzter-text")).to_contain_text("Nr. 1 Müller A.")
