"""E2E: Ziel und Rennleitung getrennt."""

import re

import httpx
from playwright.sync_api import expect

from tests.e2e.conftest import TOKENS, warte_bis
from tests.e2e.test_ziel import kachel


def _trennen(server) -> str:
    """Schaltet im Admin auf „getrennt“ und liefert den Rennleitungs-Link."""
    with httpx.Client(base_url=server.url, follow_redirects=True) as c:
        c.post("/admin/anmelden", data={"token": TOKENS["admin"]})
        csrf = re.search(r'name="csrf" value="([0-9a-f]+)"', c.get("/admin").text).group(1)
        r = c.post("/admin/v/1/rennleitung", data={"csrf": csrf, "bestaetigung": "1"})
        assert r.url.path == "/admin/v/1/geraete" and "getrennt" in r.text
        html = c.get("/admin/v/1/geraete").text
    return re.search(r'href="(http[^"]*/rennleitung\?token=[^"]+)"', html).group(1)


def _oeffnen(ctx, url):
    page = ctx.new_page()
    page.goto(url)
    page.wait_for_function("navigator.serviceWorker && navigator.serviceWorker.controller")
    page.wait_for_function("window.geraetBereit === true")
    return page


def test_ziel_nur_erfassen_rennleitung_eigener_link(server, neues_geraet):
    server.starten(1, 2)
    link = _trennen(server)
    ziel = _oeffnen(neues_geraet(), server.seite("ziel"))
    expect(ziel.locator("#haupt-reiter")).to_be_hidden()
    expect(ziel.locator("#sicht-erfassen")).to_be_visible()
    kachel(ziel, 1).click()
    ziel.click("#btn-plus")  # ±1 s am eigenen letzten Einlauf erlaubt
    ziel.click("#btn-ohne-nummer")
    expect(ziel.locator("#ausstehend")).to_have_text("0 ausstehend")
    warte_bis(lambda: server.teilnehmer(1)["ziel_ms"] is not None, text="Einlauf übertragen")
    assert any(e["art"] == "ZEITKORREKTUR" for e in server.ereignisse())

    leitung = _oeffnen(neues_geraet(), link)
    expect(leitung.locator("#haupt-reiter")).to_be_hidden()
    expect(leitung.locator("#sicht-rennleitung")).to_be_visible()
    expect(leitung.locator(".statusleiste .rolle")).to_have_text("Rennleitung")
    expect(leitung.locator("#rl-tab-offen")).to_have_text("Offene Zeiten (1)")
    leitung.locator("#offene-zeiten button").first.click()
    leitung.locator("#auswahl button[data-nr='2']").click()
    warte_bis(lambda: server.teilnehmer(2)["ziel_ms"] is not None, text="zugeordnet")
    assert {e["geraet"].split("-")[0] for e in server.ereignisse() if e["art"] == "ZUORDNUNG"} == {
        "rennleitung"
    }
    # Hilfe passt zur Rolle
    assert leitung.locator("#link-hilfe").get_attribute("href") == "/hilfe/rennleitung"
