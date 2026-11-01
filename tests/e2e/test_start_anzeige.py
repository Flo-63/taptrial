"""K 2: Am Startgerät ist der aktuelle Starter (Nummer und Name) das größte Element; der
START-Knopf ist kleiner, bleibt aber gut mit dem Daumen zu treffen – hoch und quer."""

import pytest
from playwright.sync_api import expect

DAUMEN_PX = 64  # Mindesthöhe des START-Knopfs


def groesse(page, selektor):
    return page.evaluate(
        """(s) => { const e = document.querySelector(s); const r = e.getBoundingClientRect();
        return { h: r.height, w: r.width, unten: r.bottom,
                 schrift: parseFloat(getComputedStyle(e).fontSize) }; }""",
        selektor,
    )


@pytest.mark.parametrize(("breite", "hoehe"), [(390, 844), (1920, 1080)])
def test_starter_groesstes_element(server, browser, tmp_path, breite, hoehe):
    ctx = browser.new_context(viewport={"width": breite, "height": hoehe}, service_workers="block")
    page = ctx.new_page()
    page.goto(server.seite("start"))
    page.wait_for_function("window.geraetBereit === true")
    expect(page.locator("#naechster-name")).to_have_text("Vorname1 Nachname1")
    nr, name = groesse(page, "#naechster-nr"), groesse(page, "#naechster-name")
    box, start = groesse(page, "#naechster-box"), groesse(page, "#btn-start")
    # Nummer größer als jede andere Schrift, Name größer als die START-Beschriftung
    assert nr["schrift"] > start["schrift"] and name["schrift"] >= start["schrift"]
    assert box["h"] > start["h"]
    # START: daumentauglich und ohne Scrollen sichtbar
    assert start["h"] >= DAUMEN_PX and start["w"] >= 200 and start["unten"] <= hoehe
    assert page.evaluate("document.documentElement.scrollWidth") <= breite
    page.screenshot(path=tmp_path / f"start-{breite}x{hoehe}.png")
    ctx.close()
