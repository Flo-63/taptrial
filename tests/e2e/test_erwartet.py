"""E2E: erwartete Fahrer am Ziel hervorheben (AUSBAU A8)."""

import time
import uuid

from playwright.sync_api import expect

from tests.e2e.conftest import oeffnen

MIN = 60_000


def _ereignis(art, nr, t):
    return {"uuid": str(uuid.uuid4()), "art": art, "startnummer": nr, "zeit_ms": t,
            "erstellt_ms": t, "geraet": "api"}  # fmt: skip


def _kachel(page, nr):
    return page.locator(f"#fahrer .kachel[data-nr='{nr}']")


def test_ohne_zieleinlauf_keine_hervorhebung(server, neues_geraet):
    jetzt = int(time.time() * 1000)
    server.senden([_ereignis("START", 1, jetzt - 30 * MIN)])
    page = oeffnen(neues_geraet(), server, "ziel")
    expect(_kachel(page, 1)).to_be_visible()
    expect(page.locator("#fahrer .kachel.erwartet")).to_have_count(0)


def test_erwartete_und_ueberfaellige_bekommen_rahmen(server, neues_geraet):
    jetzt = int(time.time() * 1000)
    server.senden([_ereignis("START", nr, jetzt - vor * MIN)
                   for nr, vor in ((1, 20), (2, 9), (3, 2), (5, 15), (6, 30))])  # fmt: skip
    server.senden(
        [_ereignis("ZIEL", 1, jetzt - 10 * MIN), _ereignis("ZIEL", 6, jetzt - 20 * MIN)],
        rolle="ziel",
    )  # Median 10 min
    page = oeffnen(neues_geraet(), server, "ziel")
    expect(_kachel(page, 2)).to_have_class("kachel erwartet")  # in 1 min erwartet
    expect(_kachel(page, 5)).to_have_class("kachel erwartet")  # überfällig
    for nr in (3, 4):  # in 8 min erwartet bzw. nicht gestartet
        expect(_kachel(page, nr)).not_to_have_class("kachel erwartet")
    # kein Status-Text, Kacheln bleiben an ihrem Platz
    assert _kachel(page, 2).locator(".kachel-status").count() == 0
    nummern = page.locator("#fahrer .kachel").evaluate_all("ks => ks.map(k => +k.dataset.nr)")
    assert nummern == sorted(nummern)
    # Zieleinlauf: Rahmen weg (Kachel verschwindet nach der Entprellung)
    _kachel(page, 2).click()
    expect(_kachel(page, 2)).to_have_count(0, timeout=5000)
    expect(_kachel(page, 5)).to_have_class("kachel erwartet")
