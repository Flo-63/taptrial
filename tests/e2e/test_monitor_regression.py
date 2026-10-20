"""Regression – Abschnittsfelder im Live-Bereich, im Admin gewählt, zeigen auf /monitor
je Fahrer die letzte Zeit mit Namen (Segment, Rundenblock, eigener Abschnitt), bei „mit Pfeil und
Differenz“ mit ▼/▲ und Differenz; der Monitor lädt sich bei einer neuen App-Version selbst neu."""

import time
import uuid

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import TOKENS, einstellungen

MIN = 60_000
FELDER = ("Kreisel→Berg", "Runde", "Abfahrt")  # Segment, Rundenblock, eigener Abschnitt


@pytest.fixture
def rundkurs():
    return "eigen"


def felder_waehlen(browser, server):
    ctx = browser.new_context(viewport={"width": 1366, "height": 900})
    a = ctx.new_page()
    a.goto(f"{server.url}/admin")
    a.fill("input[name=token]", TOKENS["admin"])
    a.click("#anmelden button")
    a.click("#veranstaltungen a")
    einstellungen(a, "zielmonitor")
    for name in FELDER:
        feld = a.locator("#monitor-live .live-feld", has=a.get_by_text(name, exact=True))
        feld.locator("input[name=live_abschnitt]").check()
        feld.locator("input[name=live_vergleich]").check()
    a.click("#zielmonitor-speichern")
    expect(a.locator(".meldung")).to_contain_text("Zielmonitor gespeichert")
    expect(a.locator("#monitor-live input[name=live_vergleich]:checked")).to_have_count(3)
    ctx.close()


def rundkurs_fahren(server):
    """Nr. 1: Kreisel 3, Berg 8, Kreisel 13 (Runde 10 min), Berg 17, Kreisel 21 (Runde 8 min);
    Nr. 2: gestartet, noch an keinem Messpunkt."""
    t0 = int(time.time() * 1000) - 30 * MIN
    server.senden([{"uuid": str(uuid.uuid4()), "art": "START", "startnummer": nr, "zeit_ms": t,
                    "erstellt_ms": t, "geraet": "start-api"} for nr, t in ((1, t0), (2, t0 + MIN))])  # fmt: skip
    kreisel = server.api("/api/stand", rolle="posten")["messpunkt"]["id"]
    berg = server.api("/api/stand", rolle="posten-berg")["messpunkt"]["id"]
    for mp, rolle, m in ((kreisel, "posten", 3), (berg, "posten-berg", 8), (kreisel, "posten", 13),
                         (berg, "posten-berg", 17), (kreisel, "posten", 21)):  # fmt: skip
        server.senden([{"uuid": str(uuid.uuid4()), "art": "DURCHFAHRT", "startnummer": 1, "zeit_ms": t0 + m * MIN,
                        "erstellt_ms": t0 + m * MIN, "geraet": rolle, "wert": str(mp)}], rolle=rolle)  # fmt: skip


ERWARTET = {  # Name, Zeit, Pfeil, Differenz
    "Kreisel→Berg": ("Kreisel→Berg (2)", "4:00", "▼", "60 s"),
    "Runde": ("Runde (2)", "8:00", "▼", "120 s"),
    "Abfahrt": ("Abfahrt (2)", "4:00", "▼", "60 s"),
}


@pytest.mark.parametrize("rundkurs", ["eigen"])
def test_live_felder_aus_dem_admin_mit_zeit_name_und_pfeil(browser, server, rundkurs):
    felder_waehlen(browser, server)
    rundkurs_fahren(server)
    # API: je Feld die letzte Zeit mit Pfeil und Differenz
    d = server.api("/api/oeffentlich")
    assert [x["id"].split(":")[1] for x in d["monitor"]["live"]] == ["s1.0", "b1", "a1"]
    nr1 = next(u for u in d["unterwegs"] if u["startnummer"] == 1)["abschnitte"]
    assert [(f["label"], f["zeit"], f["pfeil"], f["diff"]) for f in nr1.values()] == list(
        ERWARTET.values()
    )
    # Monitor quer und hochkant: Name, Zeit und Pfeil sichtbar, kein waagerechtes Scrollen
    for breite, hoehe in ((1920, 1080), (768, 1024)):
        page = browser.new_page(viewport={"width": breite, "height": hoehe})
        page.goto(f"{server.url}/monitor")
        zeile = page.locator("#mon-unterwegs tr[data-nr='1']")
        felder = zeile.locator(".segment")
        expect(felder).to_have_count(3)
        for i, (label, zeit, pfeil, _diff) in enumerate(ERWARTET.values()):
            expect(felder.nth(i).locator(".seg-label")).to_have_text(label)
            expect(felder.nth(i).locator(".seg-label")).to_be_visible()
            expect(felder.nth(i).locator(".seg-zeit")).to_have_text(zeit)
            expect(felder.nth(i).locator(".seg-pfeil")).to_have_text(pfeil)
            expect(felder.nth(i).locator(".seg-pfeil")).to_be_visible()
            box, z = felder.nth(i).bounding_box(), felder.nth(i).locator(".seg-zeit").bounding_box()
            assert z["x"] + z["width"] <= box["x"] + box["width"] + 1  # Zeit ganz im Feld
        expect(page.locator("#mon-unterwegs tr[data-nr='2'] .segment")).to_have_count(3)
        expect(page.locator("#mon-unterwegs tr[data-nr='2'] .segment .seg-zeit")).to_have_count(0)
        assert page.evaluate("document.documentElement.scrollWidth") <= breite
        page.close()


@pytest.mark.parametrize("rundkurs", [None])
def test_monitor_laedt_bei_neuer_app_version_neu(browser, server, rundkurs):
    page = browser.new_page(viewport={"width": 1366, "height": 768})
    page.goto(f"{server.url}/monitor")
    version = page.locator("body").get_attribute("data-version")
    assert version and f"monitor.js?v={version}" in page.content()
    expect(page.locator("#mon-titel")).to_have_text("E2E-Zeitfahren · 10.10.2026")
    # alter Code im Tab: die Seite wurde mit einer anderen Version geladen → beim nächsten Abruf neu laden
    page.evaluate("document.body.dataset.version = 'alt'; window.vorNeuladen = 1")
    page.wait_for_function("window.vorNeuladen === undefined", timeout=8_000)
    expect(page.locator("body")).to_have_attribute("data-version", version)
