"""Ziel einer Strecke an einem Zwischen-Messpunkt: das Gerät am Berg nimmt für die 5 km (Damen,
ungerade Nr.) die Zielzeit, für die 20 km (Herren, gerade Nr.) eine Zwischenzeit."""

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import jetzt_synchronisieren, oeffnen, warte_bis
from tests.e2e.test_ziel import kachel


@pytest.fixture
def zwei_strecken():
    return True


@pytest.fixture
def rundkurs():
    return "ziel_berg"


def test_posten_am_ziel_der_kurzen_strecke(server, neues_geraet):
    server.starten(1, 2, 3, 4)
    b = oeffnen(neues_geraet(), server, "posten-berg")
    b.clock.install()
    jetzt_synchronisieren(b)
    # beide Strecken in einer Liste; 5 km mit „Ziel“, 20 km mit Durchfahrten
    for nr in (1, 2, 3, 4):
        expect(kachel(b, nr)).to_be_visible()
    expect(kachel(b, 1).locator(".kachel-ziel")).to_have_text("Ziel")
    expect(kachel(b, 1).locator(".kachel-strecke")).to_have_text("5 km")
    expect(kachel(b, 2).locator(".kachel-ziel")).to_have_count(0)
    expect(kachel(b, 2).locator(".kachel-runde")).to_have_text("1. von 2 Durchfahrten")
    expect(b.locator("#posten-info")).to_have_text("4 noch erwartet")
    # 5-km-Fahrerin: Tipp = Zielzeit, danach fertig; Doppeltipp zählt nicht
    kachel(b, 1).click()
    kachel(b, 1).click()
    expect(kachel(b, 1)).to_have_count(0, timeout=3_000)
    # 20-km-Fahrer: Zwischenzeit, weiter erwartet
    b.clock.fast_forward(120_000)
    kachel(b, 2).click()
    b.clock.fast_forward(2_000)
    expect(kachel(b, 2).locator(".kachel-runde")).to_have_text("2. von 2 Durchfahrten")
    expect(b.locator("#posten-info")).to_have_text("3 noch erwartet")
    jetzt_synchronisieren(b)
    warte_bis(lambda: server.teilnehmer(1)["ziel_ms"] is not None, text="Zielzeit Nr. 1")
    assert server.teilnehmer(1)["status"] == "OK" and server.teilnehmer(2)["ziel_ms"] is None
    assert len([e for e in server.ereignisse() if e["art"] == "DURCHFAHRT"]) == 2
    # Zielgerät: nur die 20 km; Rennleitung: Zieleinlauf vom Posten ohne „Umbuchen“
    z = oeffnen(neues_geraet(), server, "ziel")
    expect(kachel(z, 2)).to_be_visible()
    expect(kachel(z, 1)).to_have_count(0)
    expect(kachel(z, 3)).to_have_count(0)
    z.click("#tab-rennleitung")
    z.click("#rl-tab-letzte")
    zeile = z.locator("#letzte li").first
    expect(zeile).to_contain_text("Nr. 1")
    expect(zeile).to_contain_text("Ziel Berg (Posten)")
    zeile.locator(".letzte-text").click()
    expect(zeile.locator(".btn-rueckgaengig")).to_be_visible()
    expect(zeile.locator(".btn-umbuchen")).to_have_count(0)
