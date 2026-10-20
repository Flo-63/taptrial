"""Zielmonitor mit gemeinsamem Raster. „Auf der Strecke“,
„Letzte Zieleinläufe“ und das Ergebnis stehen untereinander in voller Breite, die Spalten stehen in
allen Bereichen exakt untereinander. Alle sichtbaren Zeilen teilen sich die Höhe (Zeilenhöhe 4–6 % der
Fensterhöhe), die Schrift folgt der Zeilenhöhe; geblättert wird erst, wenn die Mindesthöhe nicht
reicht – dann mit mindestens 3 Zeilen je Seite. Leere Bereiche entfallen.
Formate: 1920×1080, 1366×768, hochkant 1080×1920 und 360 px; je mit 5 und mit 25 Fahrern auf der
Strecke (Bildschirmfotos unter tmp_path)."""

import re
import time
import uuid

import pytest
from playwright.sync_api import expect

from tests.e2e.test_monitor_segmente import durchfahrten, senden_direkt

MIN = 60_000
FORMATE = [(1920, 1080), (1366, 768), (1080, 1920), (360, 740)]
BEREICHE = ("unterwegs", "letzte", "klassen")


@pytest.fixture
def rundkurs():
    return "monitor"


@pytest.fixture
def teilnehmer_anzahl():
    return 40


def rennen(server, unterwegs: int, im_ziel: int = 5):
    """Nr. 1 … unterwegs+im_ziel gestartet; die letzten im_ziel sind im Ziel."""
    gestartet = unterwegs + im_ziel
    t0 = int(time.time() * 1000) - (gestartet + 20) * MIN
    starts = {nr: t0 + (nr - 1) * MIN for nr in range(1, gestartet + 1)}
    server.senden([{"uuid": str(uuid.uuid4()), "art": "START", "startnummer": nr, "zeit_ms": t, "erstellt_ms": t,
                    "geraet": "start-api"} for nr, t in starts.items()])  # fmt: skip
    senden_direkt(server, durchfahrten(server, starts))
    ziel = [{"uuid": str(uuid.uuid4()), "art": "ZIEL", "startnummer": nr, "zeit_ms": starts[nr] + 20 * MIN,
             "erstellt_ms": starts[nr] + 20 * MIN, "geraet": "ziel-api"}
            for nr in range(unterwegs + 1, gestartet + 1)]  # fmt: skip
    if ziel:
        server.senden(ziel, rolle="ziel")


def nachstarten(server, nummern):
    t = int(time.time() * 1000) - 2 * MIN
    server.senden([{"uuid": str(uuid.uuid4()), "art": "START", "startnummer": nr, "zeit_ms": t + nr * 1000,
                    "erstellt_ms": t + nr * 1000, "geraet": "start-api"} for nr in nummern])  # fmt: skip


def nummern(page, liste):
    return page.locator(f"#mon-{liste} tr[data-nr]").evaluate_all(
        "rs => rs.map(r => +r.dataset.nr)"
    )


def zeitkanten(page) -> set[int]:
    """Rechte Kante der Zeit-Spalte in Spaltenkopf und allen Bereichen (gerundet)."""
    kanten = {round(page.locator("#mon-spaltenkopf .zeit").bounding_box()["x"] + page.locator(
        "#mon-spaltenkopf .zeit").bounding_box()["width"])}  # fmt: skip
    for b in BEREICHE:
        for r in page.locator(f"#mon-{b} tr[data-nr] td.zeit").evaluate_all(
            "ts => ts.map(t => t.getBoundingClientRect().right)"
        ):
            kanten.add(round(r))
    return kanten


def schrift(page) -> float:
    return page.locator("#mon-letzte tr[data-nr] td.name").first.evaluate(
        "e => parseFloat(getComputedStyle(e).fontSize)"
    )


def pruefen_gemeinsam(page, breite, hoehe):
    for b in ("unterwegs", "letzte"):
        expect(page.locator(f"#box-{b}")).to_be_visible()
    boxen = [
        page.locator(s).bounding_box() for s in ("#box-unterwegs", "#box-letzte", "#box-ergebnis")
    ]
    assert boxen[0]["y"] < boxen[1]["y"] < boxen[2]["y"]  # Strecke, Zieleinläufe, Ergebnis
    assert all(b["width"] >= breite - 2 for b in boxen)  # volle Breite
    kanten = zeitkanten(page)
    assert max(kanten) - min(kanten) <= 1, kanten  # Zeit-Spalte überall gleich
    assert boxen[2]["y"] + boxen[2]["height"] <= hoehe + 1  # nichts ragt hinaus
    assert page.evaluate("document.documentElement.scrollWidth") <= breite


@pytest.mark.parametrize("breite,hoehe", FORMATE)
def test_raster_hoehe_und_schrift(browser, server, tmp_path, breite, hoehe):
    rennen(server, 5)
    page = browser.new_page(viewport={"width": breite, "height": hoehe})
    page.goto(f"{server.url}/monitor")
    expect(page.locator("#mon-unterwegs tr[data-nr='1'] .segment .seg-zeit")).to_have_text("4:00")
    page.wait_for_timeout(500)
    page.screenshot(path=tmp_path / f"monitor-{breite}x{hoehe}-5.png")
    expect(page.locator("#mon-klassen tr[data-nr]")).to_have_count(5)
    pruefen_gemeinsam(page, breite, hoehe)
    # 5 Fahrer: keine Seitenzahl, alle sichtbar, der Inhalt füllt mindestens 90 % der Höhe
    for s in ("#seite-unterwegs", "#seite-letzte", "#mon-seite"):
        expect(page.locator(s)).to_have_text("")
    assert nummern(page, "unterwegs") == [1, 2, 3, 4, 5]
    unten = page.locator("#mon-klassen tr").last.bounding_box()
    assert unten["y"] + unten["height"] >= 0.9 * hoehe
    zeile = page.locator("#mon-letzte tr[data-nr]").first.bounding_box()["height"]
    assert 0.04 * hoehe - 1 <= zeile <= 0.06 * hoehe + 1
    gross = schrift(page)

    # 25 Fahrer auf der Strecke: kleinere Schrift, Blättern mit mindestens 3 Zeilen je Seite
    nachstarten(server, range(11, 31))
    expect(page.locator("#anzahl-unterwegs")).to_have_text("25", timeout=8_000)
    page.wait_for_timeout(300)
    page.screenshot(path=tmp_path / f"monitor-{breite}x{hoehe}-25.png")
    pruefen_gemeinsam(page, breite, hoehe)
    assert schrift(page) < gross
    zeile = page.locator("#mon-letzte tr[data-nr]").first.bounding_box()["height"]
    assert zeile >= 0.04 * hoehe - 1
    seite = page.locator("#seite-unterwegs").inner_text()
    assert re.fullmatch(r"Seite 1/\d+", seite), seite
    for b in BEREICHE:
        assert page.locator(f"#mon-{b} tr").count() >= 3, b
    gezeigt = set()
    for _ in range(int(seite.split("/")[1])):
        zeilen = nummern(page, "unterwegs")
        assert len(zeilen) >= 3
        gezeigt |= set(zeilen)
        page.evaluate("window.monitorBereicheBlaettern()")
    assert gezeigt == set(range(1, 6)) | set(
        range(11, 31)
    )  # alle Fahrer auf der Strecke kommen vor


def test_bereich_blaettert_alle_10_sekunden(browser, server):
    rennen(server, 25)
    page = browser.new_page(viewport={"width": 1366, "height": 768})
    page.goto(f"{server.url}/monitor")
    seite = page.locator("#seite-unterwegs")
    expect(seite).to_have_text(re.compile(r"^Seite 1/\d+$"))
    expect(page.locator("#fortschritt-unterwegs")).to_be_visible()
    erste, gesamt = nummern(page, "unterwegs"), int(seite.inner_text().split("/")[1])
    page.wait_for_timeout(8_000)
    expect(seite).to_have_text(f"Seite 1/{gesamt}")  # noch nicht nach 8 s
    expect(seite).to_have_text(f"Seite 2/{gesamt}", timeout=4_000)
    zweite = nummern(page, "unterwegs")
    assert len(zweite) == len(erste) and zweite[0] > erste[0]
    for _ in range(gesamt - 1):
        page.evaluate("window.monitorBereicheBlaettern()")
    expect(seite).to_have_text(f"Seite 1/{gesamt}")  # danach wieder von vorn


def test_keine_seite_mit_einer_zeile_bei_freiem_platz(browser, server):
    """Fehler 14.10.: 4 Fahrer unterwegs, „Seite 2/2“ mit einem Fahrer, darunter leer (1632×920)."""
    rennen(server, 4, im_ziel=1)
    page = browser.new_page(viewport={"width": 1632, "height": 920})
    page.goto(f"{server.url}/monitor")
    expect(page.locator("#mon-unterwegs tr[data-nr]")).to_have_count(4)
    expect(page.locator("#seite-unterwegs")).to_have_text("")
    page.wait_for_timeout(10_500)
    expect(page.locator("#mon-unterwegs tr[data-nr]")).to_have_count(4)


def test_leere_bereiche_ausgeblendet(browser, server):
    rennen(server, 3, im_ziel=0)  # noch kein Zieleinlauf
    page = browser.new_page(viewport={"width": 1366, "height": 768})
    page.goto(f"{server.url}/monitor")
    expect(page.locator("#mon-unterwegs tr[data-nr]")).to_have_count(3)
    expect(page.locator("#box-letzte")).to_be_hidden()
    expect(page.locator("#box-ergebnis")).to_be_hidden()  # noch keine Ergebnisse
    kopf = page.locator("#mon-spaltenkopf").bounding_box()
    assert page.locator("#box-unterwegs").bounding_box()["y"] <= kopf["y"] + kopf["height"] + 1
    expect(page.locator("#box-unterwegs .mon-orientierung")).to_have_text(
        "laufende Zeiten nur zur Orientierung"
    )
    # alle im Ziel: „Auf der Strecke“ verschwindet, kein „Niemand auf der Strecke“
    t = int(time.time() * 1000)
    server.senden([{"uuid": str(uuid.uuid4()), "art": "ZIEL", "startnummer": nr, "zeit_ms": t - 1000 * nr,
                    "erstellt_ms": t, "geraet": "ziel-api"} for nr in (1, 2, 3)], rolle="ziel")  # fmt: skip
    expect(page.locator("#box-unterwegs")).to_be_hidden(timeout=8_000)
    expect(page.locator("#box-letzte")).to_be_visible()
    expect(page.locator("#box-ergebnis")).to_be_visible()
    expect(page.locator("body")).not_to_contain_text("Niemand auf der Strecke")
    expect(page.locator("#mon-zaehlung")).to_have_text(
        "3 gestartet · 3 im Ziel · 37 noch nicht gestartet · nächste 4, 5, 6"
    )


@pytest.mark.parametrize("breite,hoehe", [(1920, 1080), (1100, 800), (360, 740)])
def test_ergebnis_beste_abschnittszeit_und_schmale_fenster(browser, server, breite, hoehe):
    """Ergebnis nach Klasse gruppiert, Spalte Abschnitt = beste Zeit des ersten gewählten Felds mit
        mehreren Durchfahrten; unter 1200 px ohne Klasse, unter 900 px Abschnitt unter dem Namen
    ."""
    rennen(server, 2, im_ziel=0)  # Nr. 1: Kreisel→Berg 5:00, dann 4:00
    t = int(time.time() * 1000)
    server.senden([{"uuid": str(uuid.uuid4()), "art": "ZIEL", "startnummer": 1, "zeit_ms": t, "erstellt_ms": t,
                    "geraet": "ziel-api"}], rolle="ziel")  # fmt: skip
    page = browser.new_page(viewport={"width": breite, "height": hoehe})
    page.goto(f"{server.url}/monitor")
    zeile = page.locator("#mon-klassen tr[data-nr='1']")
    expect(zeile.locator("td.abschnitt")).to_have_text("beste Kreisel→Berg4:00")
    expect(zeile.locator("td.abschnitt .seg-zeit")).to_be_visible()
    expect(zeile.locator("td.klasse")).to_have_text("")  # Klasse steht in der Gruppenzeile
    expect(page.locator("#mon-klassen .mon-klasse-kopf")).to_have_count(1)
    klasse = page.locator("#mon-letzte tr[data-nr='1'] td.klasse")
    if breite >= 1200:
        expect(klasse).to_be_visible()
    else:
        expect(klasse).to_be_hidden()
        expect(page.locator("#mon-spaltenkopf .klasse")).to_be_hidden()
    name = zeile.locator("td.name").bounding_box()
    abschnitt = zeile.locator("td.abschnitt").bounding_box()
    if breite < 900:  # unter dem Namen
        assert abschnitt["y"] >= name["y"] + name["height"] - 1
        assert abschnitt["x"] <= name["x"] + 1
    else:  # daneben
        assert abschnitt["x"] >= name["x"] + name["width"] - 1
    assert page.evaluate("document.documentElement.scrollWidth") <= breite
