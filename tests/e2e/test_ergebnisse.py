"""Abnahme Phase 7 im Browser: Ergebnisse, Druckansicht, Zielmonitor, Abzeichen in der Rennleitung."""

import re
import time
import uuid

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import TOKENS, admin_seite, einstellungen, oeffnen


@pytest.fixture
def bildschirm(browser):
    kontexte = []

    def erzeugen(breite=1920, hoehe=1080):
        ctx = browser.new_context(
            viewport={"width": breite, "height": hoehe}, service_workers="block"
        )
        kontexte.append(ctx)
        return ctx

    yield erzeugen
    for ctx in kontexte:
        ctx.close()


def ziel(server, nr, zeit_ms):
    server.senden([{"uuid": str(uuid.uuid4()), "art": "ZIEL", "startnummer": nr, "zeit_ms": zeit_ms,
                    "erstellt_ms": zeit_ms, "geraet": "ziel-api"}], rolle="ziel")  # fmt: skip


def test_monitor_live_ohne_token(server, bildschirm):
    starts = server.starten(1, 2, 3, abstand_ms=20_000)  # Start vor 60, 40, 20 s
    page = bildschirm().new_page()
    page.goto(f"{server.url}/monitor")
    unterwegs = page.locator("#mon-unterwegs tr[data-nr]")
    expect(unterwegs).to_have_count(3)
    assert unterwegs.evaluate_all("rs => rs.map(r => r.dataset.nr)") == ["1", "2", "3"]
    # Segmente 72: Kennzahlen in der Kopfleiste, leere Angaben entfallen, nächste drei Nummern
    expect(page.locator("#mon-zaehlung")).to_have_text(
        "3 gestartet · 3 auf der Strecke · 7 noch nicht gestartet · nächste 4, 5, 6"
    )
    expect(page.locator("#mon-status")).to_have_text("Vorläufiges Ergebnis")

    laufzeit = page.locator("#mon-unterwegs tr[data-nr='1'] .laufzeit")
    expect(laufzeit).to_have_text(re.compile(r"^\d+:\d{2}$"))  # ganze Sekunden, keine Zehntel
    vorher = laufzeit.inner_text()
    page.wait_for_timeout(1200)
    assert laufzeit.inner_text() != vorher  # läuft im Browser weiter

    ziel(server, 2, starts[2] + 30_000)
    letzte = page.locator("#mon-letzte tr[data-nr='2']")
    expect(letzte).to_be_visible(timeout=8000)  # ohne Neuladen
    expect(letzte).to_contain_text("0:30,0")
    expect(letzte).to_contain_text("1.")  # Platz in der Klasse
    expect(page.locator("#mon-unterwegs tr[data-nr='2']")).to_have_count(0)
    expect(page.locator("#mon-zaehlung")).to_have_text(
        "3 gestartet · 2 auf der Strecke · 1 im Ziel · 7 noch nicht gestartet · nächste 4, 5, 6"
    )
    expect(page.locator("#mon-klassen tr[data-nr='2']")).to_be_visible()


def test_monitor_stand_von_bei_ausfall(server, bildschirm):
    server.starten(1)
    ctx = bildschirm()
    page = ctx.new_page()
    page.goto(f"{server.url}/monitor")
    expect(page.locator("#mon-unterwegs tr[data-nr='1']")).to_be_visible()
    ctx.route("**/api/oeffentlich", lambda route: route.abort())
    page.evaluate("window.monitorLaden()")
    expect(page.locator("#mon-stand")).to_contain_text(re.compile(r"Stand von \d{2}:\d{2}:\d{2}"))
    expect(page.locator("#mon-unterwegs tr[data-nr='1']")).to_be_visible()  # letzter Stand bleibt


def test_monitor_status_und_vorbehalt(server, bildschirm):
    starts = server.starten(1, 2, 3)
    ziel(server, 1, starts[1] + 30_000)
    ziel(server, 3, starts[3] + 40_000)
    server.senden([{"uuid": "v1", "art": "STATUS", "startnummer": 1, "wert": "DSQ_VORLAEUFIG",
                    "notiz": "Windschatten", "erstellt_ms": 1, "geraet": "ziel"},
                   {"uuid": "d2", "art": "STATUS", "startnummer": 2, "wert": "DNF",
                    "erstellt_ms": 1, "geraet": "ziel"}], rolle="ziel")  # fmt: skip
    page = bildschirm().new_page()
    page.goto(f"{server.url}/monitor")
    zeile1 = page.locator("#mon-klassen tr[data-nr='1']")
    expect(zeile1).to_have_class(re.compile("vorbehalt"))
    # dunkel hinterlegt (nicht die helle Farbe der Ergebnisseite), Schrift lesbar
    assert zeile1.locator("td.name").evaluate("e => getComputedStyle(e).backgroundColor") in (
        "rgba(0, 0, 0, 0)",
        "transparent",
    )
    assert zeile1.evaluate("e => getComputedStyle(e).backgroundColor") == "rgb(43, 38, 32)"
    expect(zeile1).to_contain_text("unter Vorbehalt")
    expect(zeile1.locator(".rang")).to_have_text("1")
    expect(page.locator("#mon-klassen tr[data-nr='2']")).to_contain_text("DNF")
    expect(page.locator("#mon-zaehlung")).to_contain_text("1 DNF")


def test_monitor_blaettert_wenn_zu_viel(server, bildschirm):
    starts = server.starten(*range(1, 11), abstand_ms=1000)
    for nr in range(1, 11):
        ziel(server, nr, starts[nr] + 30_000 + nr * 100)
    page = bildschirm(700, 420).new_page()
    page.goto(f"{server.url}/monitor")
    expect(page.locator("#mon-seite")).to_have_text(re.compile(r"^Seite 1/\d+$"))
    erste = page.locator("#mon-klassen tr[data-nr]").first.get_attribute("data-nr")
    expect(page.locator("#mon-letzte tr[data-nr]").first).to_be_visible()
    page.wait_for_timeout(10_500)  # blättert von selbst (alle 10 s)
    expect(page.locator("#mon-seite")).to_have_text(re.compile(r"^Seite 2/\d+$"))
    assert page.locator("#mon-klassen tr[data-nr]").first.get_attribute("data-nr") != erste
    expect(page.locator("#mon-letzte tr[data-nr]").first).to_be_visible()  # bleibt sichtbar


def test_ergebnisse_aktualisieren_sich_automatisch(server, bildschirm):
    starts = server.starten(1, 2)
    ziel(server, 1, starts[1] + 30_000)
    page = bildschirm(1280, 900).new_page()
    page.goto(f"{server.url}/ergebnisse")
    expect(page.locator("#gesamt tr[data-nr='1']")).to_be_visible()
    expect(page.locator("#ergebnisstatus")).to_have_text("Vorläufiges Ergebnis")
    ziel(server, 2, starts[2] + 25_000)
    zeile = page.locator("#gesamt tr[data-nr='2']")
    expect(zeile).to_be_visible(timeout=13_000)  # Polling alle 10 s, ohne Neuladen
    expect(zeile.locator(".rang")).to_have_text("1")


def test_ergebnisse_stand_von_bei_ausfall(server, bildschirm):
    ctx = bildschirm(1280, 900)
    page = ctx.new_page()
    page.goto(f"{server.url}/ergebnisse")
    ctx.route("**/ergebnisse/inhalt", lambda route: route.abort())
    page.evaluate("window.ergebnisseAktualisieren()")
    expect(page.locator("#stand")).to_contain_text("Stand von")


def test_druckansicht_eine_klasse_pro_seite(server, bildschirm):
    starts = server.starten(1, 2, 3)
    for nr in (1, 2, 3):
        ziel(server, nr, starts[nr] + 30_000)
    page = bildschirm(1280, 900).new_page()
    page.goto(f"{server.url}/ergebnisse/druck")
    page.emulate_media(media="print")
    seiten = page.locator(".seite")
    assert seiten.count() == 2  # Nr. 1 und 3: Damen 61-70, Nr. 2: Herren 61-70
    umbrueche = seiten.evaluate_all("ss => ss.map(s => getComputedStyle(s).breakAfter)")
    assert umbrueche == ["page", "auto"]
    expect(page.locator(".druck-kopf").first).to_contain_text("Vorläufiges Ergebnis")
    expect(page.locator(".nicht-drucken")).to_be_hidden()


def test_rennleitung_zeigt_abzeichen_erfassen_nicht(server, bildschirm, neues_geraet):
    admin = bildschirm(1280, 900).new_page()
    admin.goto(f"{server.url}/admin")
    admin.fill("input[name=token]", TOKENS["admin"])
    admin.click("#anmelden button")
    admin.click("#veranstaltungen a")
    admin_seite(
        admin, "strecken"
    )  # Segmente 49: gemessene Länge zuerst (A.1 legt keine Strecken an)
    admin.click("#strecke-oeffnen-0")
    admin.fill("#s-laenge", "20,0")
    admin.click("#alles-speichern")
    expect(admin.locator(".meldung")).to_contain_text("Strecke gespeichert")
    einstellungen(admin, "klassen")
    admin.select_option("#wertung select[name=wertungssatz_id]", label="DRA A.1 flach")
    admin.click("#wertung button[type=submit]")
    expect(admin.locator(".meldung")).to_contain_text(
        "Klassen, Leistungswertung und Strecken gespeichert"
    )
    admin_seite(admin, "teilnehmer")
    admin.locator("#teilnehmer tr[data-nr='1'] td[data-merkmal=abzeichen]").click()  # Zeile öffnet
    admin.locator("form.merkmal-zeile[data-merkmal=abzeichen] button").click()
    expect(admin.locator(".meldung")).to_contain_text("Nr. 1: Abzeichen ja")
    admin.click("#zurueck-teilnehmer")
    expect(admin.locator("#merkmale-zaehler")).to_contain_text("Abzeichen: 1")

    server.starten(1)  # Nr. 1: Damen 61-70, Fahrzeit ~1 min → Gold
    geraet = oeffnen(neues_geraet(), server, "ziel")
    geraet.locator("#fahrer button[data-nr='1']").click()
    time.sleep(0.5)
    geraet.evaluate("window.geraet.sync()")
    expect(geraet.locator("#sicht-erfassen")).not_to_contain_text("Gold")
    geraet.click("#tab-rennleitung")
    expect(geraet.locator("#letzte li").first).to_contain_text("Gold")
