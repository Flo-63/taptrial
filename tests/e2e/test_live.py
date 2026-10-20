"""Übersicht und Geräte aktualisieren sich alle 10 s selbst – ohne Neuladen; Fokus,
offene Bereiche und Eingaben bleiben, im Hintergrund-Tab ruht es."""

import re

import httpx
from playwright.sync_api import expect

from tests.e2e.conftest import TOKENS, admin_seite


def admin(browser, server):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, service_workers="block")
    page = ctx.new_page()
    page.clock.install()
    page.goto(f"{server.url}/admin")
    page.fill("input[name=token]", TOKENS["admin"])
    page.click("#anmelden button")
    page.click("#veranstaltungen a")
    return ctx, page


def melden(server, rolle: str, geraet: str):
    """Ein Gerät meldet sich (Stand abrufen mit Uhrabweichung)."""
    httpx.get(f"{server.url}/api/stand", headers={"Authorization": f"Bearer {TOKENS[rolle]}",
                                                   "X-Geraet": geraet, "X-Uhr-Abweichung": "40"}).raise_for_status()  # fmt: skip


def verstecken(page, versteckt: bool):
    page.evaluate("""v => { Object.defineProperty(document, 'hidden', {value: v, configurable: true});
                           document.dispatchEvent(new Event('visibilitychange')); }""", versteckt)  # fmt: skip


def test_uebersicht_aktualisiert_sich_alle_10_s(server, browser):
    ctx, a = admin(browser, server)
    expect(a.locator("#kpi-gestartet strong")).to_have_text("0")
    expect(a.locator("#live-stand")).to_have_text(re.compile(r"^Stand: \d\d:\d\d:\d\d$"))
    url = a.url
    server.starten(1, 2)
    a.clock.run_for(9_000)
    expect(a.locator("#kpi-gestartet strong")).to_have_text("0")  # noch nicht
    a.clock.run_for(1_500)
    expect(a.locator("#kpi-gestartet strong")).to_have_text("2")
    assert a.url == url  # ohne Neuladen
    # Hintergrund-Tab: Pause; zurück: sofort aktualisieren
    verstecken(a, True)
    server.starten(3)
    a.clock.run_for(25_000)
    expect(a.locator("#kpi-gestartet strong")).to_have_text("2")
    verstecken(a, False)
    expect(a.locator("#kpi-gestartet strong")).to_have_text("3")
    ctx.close()


def test_geraete_aktualisieren_sich_ohne_fokus_zu_stoeren(server, browser):
    ctx, a = admin(browser, server)
    admin_seite(a, "geraete")
    start = a.locator(".geraet[data-titel=Start] .geraet-status")
    expect(start).to_contain_text("nicht verbunden")
    expect(a.locator("#uhren")).to_contain_text("Noch keine Messung")
    a.locator(".geraet[data-titel=Start] button.kopieren").focus()  # Fokus in den Karten
    melden(server, "start", "start-live")
    a.clock.run_for(10_500)
    expect(a.locator("#uhren tbody tr")).to_have_count(1)  # Tabelle aktualisiert
    expect(a.locator("#uhren")).to_contain_text("start-live")
    expect(start).to_contain_text("nicht verbunden")  # Karten unberührt, Fokus bleibt
    assert a.evaluate("document.activeElement.classList.contains('kopieren')")
    a.evaluate("document.activeElement.blur()")
    a.clock.run_for(10_500)
    expect(start).to_contain_text("verbunden")
    expect(start.locator(".badge")).to_have_text("verbunden")
    # Kopieren funktioniert auch auf ausgetauschten Karten (Delegation)
    ctx.grant_permissions(["clipboard-read", "clipboard-write"])
    a.locator(".geraet[data-titel=Start] button.kopieren").click()
    expect(a.locator(".geraet[data-titel=Start] button.kopieren")).to_have_text("Kopiert")
    ctx.close()
