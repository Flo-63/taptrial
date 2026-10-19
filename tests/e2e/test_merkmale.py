"""„außer Konkurrenz“ in Rennleitung und Zielmonitor."""

import uuid

import pytest
from playwright.sync_api import expect

from app.simulation import Fahrer
from tests.e2e.conftest import jetzt_synchronisieren, oeffnen


@pytest.fixture
def teilnehmer_liste():
    return [
        Fahrer(1, "Anna", "Becker", "w", 1990),
        Fahrer(2, "Ben", "Fischer", "w", 1991, ausser_konkurrenz=True),
    ]


def test_ausser_konkurrenz_in_rennleitung_und_monitor(server, neues_geraet, browser):
    starts = server.starten(1, 2, abstand_ms=0)
    for nr, s in ((1, 31_000), (2, 30_000)):
        z = starts[nr] + s
        server.senden([{"uuid": str(uuid.uuid4()), "art": "ZIEL", "startnummer": nr, "zeit_ms": z,
                        "erstellt_ms": z, "geraet": "ziel-api"}], rolle="ziel")  # fmt: skip
    ziel = oeffnen(neues_geraet(), server, "ziel")
    jetzt_synchronisieren(ziel)
    ziel.click("#tab-rennleitung")
    expect(ziel.locator("#letzte li", has_text="Nr. 2")).to_contain_text("a. K.")
    expect(ziel.locator("#letzte li", has_text="Nr. 1")).not_to_contain_text("a. K.")

    monitor = browser.new_page(viewport={"width": 1600, "height": 900})
    monitor.goto(f"{server.url}/monitor")
    zeile = monitor.locator("#mon-klassen tr[data-nr='2']")
    expect(zeile.locator(".status")).to_have_text("a. K.")
    expect(zeile.locator(".rang")).to_have_text("")
    expect(monitor.locator("#mon-klassen tr[data-nr='1'] .rang")).to_have_text("1")
    # nach Zeit einsortiert: Nr. 2 (schneller) steht vor Nr. 1
    nummern = monitor.locator("#mon-klassen tr[data-nr]").evaluate_all(
        "rs => rs.map(r => r.dataset.nr)"
    )
    assert nummern == ["2", "1"]
    monitor.close()
