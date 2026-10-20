"""Versionsanzeige an ruhigem Ort: Admin klein unten in der Seitenleiste (Handy: unten im
aufgeklappten Menü), öffentliche Seiten und Hilfe klein rechts in der Fußzeile mit Impressum und
Datenschutz, Druckansichten „erstellt mit TapTrial <Version>“, Zielmonitor und Geräteseiten ohne
Version. Nirgends mitten im Inhalt. 1920 px und 390 px, Bildschirmfotos unter tmp_path."""

import pytest
from playwright.sync_api import expect

from app.version import VERSION
from tests.e2e.conftest import TOKENS

FORMATE = [(1920, 1080), (390, 844)]


def box(page, selektor: str) -> dict:
    return page.locator(selektor).first.bounding_box()


def seitenhoehe(page) -> float:
    return page.evaluate("Math.max(document.documentElement.scrollHeight, window.innerHeight)")


@pytest.mark.parametrize(("breite", "hoehe"), FORMATE)
def test_admin_unten_in_der_seitenleiste(browser, server, tmp_path, breite, hoehe):
    ctx = browser.new_context(viewport={"width": breite, "height": hoehe}, service_workers="block")
    page = ctx.new_page()
    page.goto(f"{server.url}/admin")
    page.fill("input[name=token]", TOKENS["admin"])
    page.click("#anmelden button")
    page.click("#veranstaltungen a")
    version = page.locator("#admin-version")
    expect(version).to_have_text(f"TapTrial {VERSION}")
    assert page.locator("main .app-version").count() == 0  # nicht im Inhalt
    if breite < 900:
        expect(version).to_be_hidden()  # Handy: erst im aufgeklappten Menü
        page.click("#menue-knopf")
    expect(version).to_be_visible()
    v, letzter = box(page, "#admin-version"), page.locator(".sl-nav .nav-item").last.bounding_box()
    leiste = box(page, "#seitenleiste")
    assert v["y"] >= letzter["y"] + letzter["height"]  # unter dem letzten Punkt
    assert leiste["x"] <= v["x"] and v["x"] + v["width"] <= leiste["x"] + leiste["width"]
    assert (
        page.evaluate(
            "parseFloat(getComputedStyle(document.querySelector('#admin-version')).fontSize)"
        )
        <= 12
    )
    page.screenshot(path=tmp_path / f"admin-version-{breite}.png", full_page=True)
    ctx.close()


@pytest.mark.parametrize(("breite", "hoehe"), FORMATE)
def test_oeffentlich_und_hilfe_rechts_in_der_fusszeile(browser, server, tmp_path, breite, hoehe):
    ctx = browser.new_context(viewport={"width": breite, "height": hoehe}, service_workers="block")
    page = ctx.new_page()
    for pfad, selektor in (("/", "#oeffentlich-fuss .app-version"),
                           ("/ergebnisse", "#oeffentlich-fuss .app-version"),
                           ("/hilfe/start", ".fuss .app-version")):  # fmt: skip
        page.goto(server.url + pfad)
        version = page.locator(selektor)
        expect(version).to_contain_text(VERSION)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        v = box(page, selektor)
        unten = page.evaluate("window.scrollY") + v["y"] + v["height"]
        assert unten >= seitenhoehe(page) - 60, pfad  # ganz unten, nicht mitten im Inhalt
        assert v["x"] + v["width"] >= breite / 2, pfad  # rechts
        assert page.locator(f"{selektor.split()[0]} .app-version").count() == 1
        page.screenshot(
            path=tmp_path / f"version{pfad.replace('/', '-') or '-start'}-{breite}.png",
            full_page=True,
        )
    ctx.close()


def test_keine_version_auf_monitor_und_geraeten(browser, server):
    ctx = browser.new_context(viewport={"width": 1920, "height": 1080}, service_workers="block")
    page = ctx.new_page()
    page.goto(f"{server.url}/monitor")
    page.wait_for_selector("#mon-titel")
    assert page.locator(".app-version").count() == 0
    assert VERSION not in page.locator("body").inner_text()
    page.goto(server.seite("start"))
    page.wait_for_selector("body")
    assert page.locator(".app-version").count() == 0
    assert VERSION not in page.locator("body").inner_text()
    page.goto(f"{server.url}/ergebnisse/druck")
    expect(page.locator("#druck-fuss")).to_contain_text(f"erstellt mit TapTrial {VERSION}")
    ctx.close()
