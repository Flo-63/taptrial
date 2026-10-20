"""Im Browser: Strecken im Admin anlegen, Klassen umordnen, Kennzeichen auf den Geräten."""

import re

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import TOKENS, admin_seite, einstellungen, jetzt_synchronisieren, oeffnen


@pytest.fixture
def admin(browser, server):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, service_workers="block")
    page = ctx.new_page()
    page.goto(f"{server.url}/admin")
    page.fill("input[name=token]", TOKENS["admin"])
    page.click("#anmelden button")
    page.click("#veranstaltungen a")
    admin_seite(page, "strecken")
    yield page
    ctx.close()


def test_strecke_anlegen_und_kennzeichen_ueberall(admin, server, neues_geraet):
    # Seed: eine Strecke, Nr. 1 = Damen 61-70, Nr. 2 = Herren 61-70
    expect(admin.locator("#strecken-liste .strecke-kurz")).to_have_count(1)
    # erste Strecke umbenennen (eigene Seite, Grunddaten)
    admin.click("#strecke-oeffnen-0")
    admin.fill("#s-name", "Lang")
    admin.click("#alles-speichern")
    expect(admin.locator(".meldung")).to_contain_text("Strecke gespeichert")
    admin.click("#zurueck-strecken")
    # Strecke hinzufügen → ihre Seite öffnet sich; Name, Länge, Klasse „Damen 61-70“ hierher holen
    admin.click("#strecke-neu")
    expect(admin.locator(".meldung")).to_contain_text("angelegt")
    admin.fill("#s-name", "Kurz")
    admin.fill("#s-laenge", "5,0")
    admin.click("#alles-speichern")
    expect(admin.locator(".meldung")).to_contain_text("Strecke gespeichert")
    admin.click("#zurueck-strecken")
    expect(admin.locator("#strecken-liste .strecke-marke")).to_have_text(["Lang", "Kurz"])
    expect(admin.locator(".strecke-kurz[data-strecke='Kurz']")).to_contain_text("5,0 km")
    # Klassenzuordnung: eine Tabelle, ein Speichern
    admin.select_option("#zuordnung tr[data-klasse='Damen 61-70'] select", label="Kurz")
    admin.click("#zuordnung-speichern")
    expect(admin.locator(".meldung")).to_contain_text("Klassenzuordnung gespeichert")
    expect(
        admin.locator("#zuordnung tr[data-klasse='Damen 61-70'] select option:checked")
    ).to_have_text("Kurz")

    # Reiter „Klassen & Wertung“: ein Wertungssatz legt keine Strecken an –
    # fehlt einer Strecke die Länge, nennt die Seite sie; die Strecken bleiben
    einstellungen(admin, "klassen")
    expect(admin.locator("#strecken-neu-info")).to_be_visible()
    admin.select_option("#wahl-wertungssatz", label="DRA A.1 flach")
    expect(admin.locator("#laengen-verwerfen")).to_have_count(0)
    admin.click("#wertung button[type=submit]")
    expect(admin.locator(".fehler")).to_contain_text("Streckenlänge Pflicht (Strecke „Lang“)")
    admin_seite(admin, "strecken")
    expect(admin.locator("#strecken-liste .strecke-marke")).to_have_text(["Lang", "Kurz"])

    # Startgerät: Kennzeichen beim nächsten Fahrer (Text + Farbe)
    start = oeffnen(neues_geraet(), server, "start")
    marke = start.locator("#naechster-strecke .strecke-marke")
    expect(marke).to_have_text("Kurz")
    assert "strecke-farbe-1" in marke.get_attribute("class")
    farbe = marke.evaluate("e => getComputedStyle(e).backgroundColor")
    start.click("#btn-start")
    expect(start.locator("#naechster-strecke .strecke-marke")).to_have_text("Lang")
    assert (
        start.locator("#naechster-strecke .strecke-marke").evaluate(
            "e => getComputedStyle(e).backgroundColor"
        )
        != farbe
    )
    start.wait_for_timeout(1_100)  # Entprellung des Start-Knopfs (1 s)
    start.click("#btn-start")
    jetzt_synchronisieren(start)
    expect(start.locator("#ausstehend")).to_have_text("0 ausstehend", timeout=10_000)

    # Zielgerät: Kacheln und Rennleitung
    ziel = oeffnen(neues_geraet(), server, "ziel")
    ziel.evaluate("window.geraet.sync()")
    expect(ziel.locator("#fahrer button[data-nr='1'] .strecke-marke")).to_have_text("Kurz")
    expect(ziel.locator("#fahrer button[data-nr='2'] .strecke-marke")).to_have_text("Lang")
    ziel.locator("#fahrer button[data-nr='1']").click()
    ziel.click("#tab-rennleitung")
    expect(ziel.locator("#letzte li").first.locator(".strecke-marke")).to_have_text("Kurz")
    expect(ziel.locator("#status-auswahl button[data-nr='2'] .strecke-marke")).to_have_text("Lang")
    ziel.evaluate("window.geraet.sync()")

    # Monitor: letzte Einläufe, auf der Strecke, Klassenüberschriften
    mon = neues_geraet().new_page()
    mon.set_viewport_size({"width": 1600, "height": 900})
    mon.goto(f"{server.url}/monitor")
    expect(mon.locator("#mon-letzte tr[data-nr='1'] .mon-strecke")).to_have_text("Kurz")
    expect(mon.locator("#mon-unterwegs tr[data-nr='2'] .mon-strecke")).to_have_text("Lang")
    expect(mon.locator(".mon-klasse-kopf .mon-strecke").first).to_have_text("Kurz")

    # öffentliche Ergebnisse: eigener Abschnitt je Strecke
    mon.goto(f"{server.url}/ergebnisse")
    expect(
        mon.locator("section.strecke[data-strecke='Kurz'] tr[data-nr='1']").first
    ).to_be_visible()


def test_strecken_editor_reiter_aktionsleiste(admin):
    """Reiter wechseln ohne Speichern, Fehler am Feld markieren den Reiter, „Abbrechen“
    verwirft, „Zurück“ warnt bei ungespeicherten Änderungen."""
    admin.click("#strecke-oeffnen-0")
    expect(admin.locator("#grunddaten")).to_be_visible()
    expect(admin.locator("#streckenverlauf")).to_be_hidden()
    admin.fill("#s-name", "Geändert")
    admin.fill("#s-laenge", "abc")
    admin.click("#strecke-reiter [data-reiter=verlauf]")  # Wechsel ohne Speichern, Eingaben bleiben
    expect(admin.locator("#streckenverlauf")).to_be_visible()
    expect(admin.locator("#grunddaten")).to_be_hidden()
    expect(admin.locator("#aktionsleiste")).to_be_visible()
    admin.click("#alles-speichern")
    expect(admin.locator("#strecke-reiter [data-reiter=grunddaten]")).to_have_class(
        re.compile("reiter-fehler")
    )
    expect(admin.locator("#grunddaten")).to_be_visible()  # springt zum Reiter mit dem Fehler
    expect(admin.locator("#s-laenge")).to_have_attribute("aria-invalid", "true")
    expect(admin.locator("#s-laenge + .feld-fehler")).to_contain_text("länge „abc“ ungültig")
    expect(admin.locator("#s-name")).to_have_value("Geändert")  # nichts verloren
    # „Zurück“ mit ungespeicherten Änderungen: Rückfrage, „Abbrechen“ im Dialog bleibt auf der Seite
    admin.click("#zurueck-strecken")
    expect(admin.locator("#bestaetigung-dialog")).to_be_visible()
    admin.click("#dialog-abbrechen")
    expect(admin.locator("#s-name")).to_have_value("Geändert")
    # „Abbrechen“ verwirft
    admin.click("#aenderungen-verwerfen")
    expect(admin.locator("#s-name")).not_to_have_value("Geändert")
    nach = admin.locator("#s-name").input_value()
    admin.fill("#s-laenge", "20,0")
    admin.click("#alles-speichern")
    expect(admin.locator(".meldung")).to_contain_text("Strecke gespeichert")
    admin.click("#zurueck-strecken")  # ohne Änderungen: keine Rückfrage
    expect(admin.locator(".strecke-kurz[data-strecke='" + nach + "']")).to_contain_text("20,0 km")
