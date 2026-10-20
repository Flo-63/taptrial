"""Fixierter Rahmen im Admin: Kopfzeile und Seitenleiste bleiben stehen."""

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import admin_seite, einstellungen
from tests.e2e.test_import_vorschau import anmelden


@pytest.fixture
def teilnehmer_anzahl() -> int:
    return 50  # lange Teilnehmerliste: die Seite scrollt


def test_kopf_und_seitenleiste_bleiben_stehen(browser, server):
    ctx, page = anmelden(browser, server, 1280, 700)
    einstellungen(page, "zielmonitor")  # Zielmonitor-Einstellungen: lang, scrollt im Fenster
    kopf = page.locator("#topbar")
    hoehe = kopf.bounding_box()["height"]
    page.mouse.move(800, 400)  # über dem Inhalt (die Seitenleiste scrollt bei Bedarf selbst)
    page.mouse.wheel(0, 3000)
    page.wait_for_function("window.scrollY > 200")
    assert kopf.bounding_box()["y"] == 0
    leiste = page.locator("#seitenleiste").bounding_box()
    assert abs(leiste["y"] - hoehe) < 2 and leiste["y"] + leiste["height"] <= 701
    expect(page.locator(".sl-nav a[data-seite=status]")).to_be_in_viewport()
    # Tabellen-Box mit Sticky Header funktioniert weiter
    admin_seite(page, "teilnehmer")
    th = page.locator("#teilnehmer thead th").first
    assert th.evaluate("e => getComputedStyle(e).position") == "sticky"
    ctx.close()


def test_handy_nur_kopfzeile_mit_menue_fixiert(browser, server):
    ctx, page = anmelden(browser, server, 390, 700)
    admin_seite(page, "teilnehmer")
    expect(page.locator("#seitenleiste")).to_be_hidden()
    page.mouse.wheel(0, 3000)
    page.wait_for_function("window.scrollY > 200")
    knopf = page.locator("#menue-knopf")
    expect(knopf).to_be_in_viewport()
    assert page.locator("#topbar").bounding_box()["y"] == 0
    knopf.click()
    expect(page.locator(".sl-nav a[data-seite=aufgaben]")).to_be_in_viewport()
    page.click(".sl-nav a[data-seite=aufgaben]")
    expect(page.locator("h1")).to_have_text("Aufgaben")
    expect(page.locator("#seitenleiste")).to_be_hidden()
    ctx.close()


def test_geraete_karten_buttons_auf_gleicher_hoehe(browser, server):
    ctx, page = anmelden(browser, server, 1280, 900)
    admin_seite(page, "geraete")
    hoehen = page.locator(".geraet .kopieren").evaluate_all(
        "ks => ks.map(k => Math.round(k.getBoundingClientRect().top))"
    )
    # Karten je Ort gruppiert – Start; Ziel und Rennleitung in einer Reihe; Zielmonitor
    assert len(hoehen) == 4 and hoehen[1] == hoehen[2] and hoehen[0] < hoehen[1] < hoehen[3]
    url = page.locator(".geraet[data-titel=Rennleitung] code.url")
    assert url.get_attribute("title").startswith("http")
    assert url.evaluate("e => getComputedStyle(e).textOverflow") == "ellipsis"
    ctx.close()


def test_hilfe_im_selben_tab_mit_abschnitt_und_rueckweg(browser, server):
    ctx, page = anmelden(browser, server, 1280, 800)
    admin_seite(page, "geraete")
    seite = page.url
    page.click("#nav-hilfe")
    page.wait_for_selector("h2#geraete")
    assert "/hilfe/admin?zurueck=" in page.url and page.url.endswith("#geraete")
    abschnitt = page.locator("h2#geraete")
    expect(abschnitt).to_be_in_viewport()
    nav = page.locator(".hilfe-nav").bounding_box()
    assert abschnitt.bounding_box()["y"] >= nav["y"] + nav["height"] - 1  # nicht verdeckt
    page.mouse.wheel(0, 2000)
    assert page.locator(".hilfe-nav").bounding_box()["y"] == 0  # Kopfzeile bleibt stehen
    page.click("#zurueck-admin")
    assert page.url == seite
    ctx.close()


def test_einstellungen_reiter_auf_dem_handy(browser, server):
    """Reiterleiste waagerecht scrollbar, die Seite selbst scrollt bei 390 px nicht waagerecht."""
    ctx, page = anmelden(browser, server, 390, 800)
    for seite in ("strecken", "messpunkte"):  # Eigene Seiten
        admin_seite(page, seite)
        assert page.evaluate("document.documentElement.scrollWidth") <= 390, seite
    for reiter in ("allgemein", "klassen", "zielmonitor"):
        einstellungen(page, reiter)
        assert page.evaluate("document.documentElement.scrollWidth") <= 390, reiter
    leiste = page.locator("#einstellungen-reiter")
    assert leiste.evaluate("e => getComputedStyle(e).overflowX") == "auto"
    assert leiste.evaluate("e => e.scrollWidth > e.clientWidth")  # schiebbar statt umbrechend
    expect(page.locator("#einstellungen-reiter a[aria-current=page]")).to_have_text(
        "Zielmonitor und Ergebnisse"
    )
    ctx.close()


def test_strecken_seite_auf_dem_handy(browser, server):
    ctx, page = anmelden(browser, server, 390, 800)
    admin_seite(page, "strecken")
    page.click("#strecke-oeffnen-0")
    for reiter in ("grunddaten", "verlauf", "abschnitte"):  # Reiter und Aktionsleiste
        page.click(f"#strecke-reiter [data-reiter={reiter}]")
        expect(page.locator("#aktionsleiste")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth") <= 390, reiter
    ctx.close()


def test_abbrechen_ohne_speichern(browser, server):
    """„Abbrechen“ verwirft Eingaben – Seite verlassen bzw. Aufklapp-Formular schließen."""
    ctx, page = anmelden(browser, server, 1280, 800)
    einstellungen(page, "allgemein")
    alt = page.locator("#e-name").input_value()
    page.fill("#e-name", "Geändert")
    page.locator("#stammdaten .abbrechen").click()
    expect(page.locator(".sl-nav a[data-seite=uebersicht][aria-current=page]")).to_have_count(1)
    einstellungen(page, "allgemein")
    expect(page.locator("#e-name")).to_have_value(alt)
    admin_seite(page, "teilnehmer")
    page.locator("#teilnehmer tr.row").first.click()
    page.click("#aktion-manuell summary")
    page.fill("#m-notiz", "Test")
    page.locator("#aktion-manuell .abbrechen").click()
    expect(page.locator("#aktion-manuell")).not_to_have_attribute("open", "")
    page.click("#aktion-manuell summary")
    expect(page.locator("#m-notiz")).to_have_value("")
    ctx.close()
