"""Im Browser: CSV-Import mit Vorschau/Bearbeitung, Admin-Layout auf dem Handy."""

import re
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import TOKENS, admin_seite

CSV = (
    "startnummer;vorname;nachname;geschlecht;geburtsjahr;verein\n"
    "11;Anna;Neu;w;1990;RC\n"
    "12;Ben;Neu;x;1980;\n"  # ungültiges Geschlecht
    "11;Anna;Neu;w;1991;\n"  # Startnummer doppelt, gleicher Name (Warnung)
)


def anmelden(browser, server, breite=1280, hoehe=900):
    ctx = browser.new_context(viewport={"width": breite, "height": hoehe}, service_workers="block")
    page = ctx.new_page()
    page.goto(f"{server.url}/admin")
    page.fill("input[name=token]", TOKENS["admin"])
    page.click("#anmelden button")
    page.click("#veranstaltungen a")
    return ctx, page


@pytest.fixture
def admin(browser, server):
    ctx, page = anmelden(browser, server)
    yield page
    ctx.close()


def zelle(page, zeile, feld):
    return page.locator("#import-tabelle tbody tr").nth(zeile).locator(f"td[data-feld={feld}]")


def test_vorschau_bearbeiten_und_importieren(admin, tmp_path):
    datei = tmp_path / "teilnehmer.csv"
    datei.write_text(CSV, encoding="utf-8")
    admin_seite(admin, "teilnehmer")
    admin.click("#csv-importieren")
    admin.set_input_files("#import input[type=file]", str(datei))
    admin.click("#import button")

    expect(admin.locator("#zaehler")).to_have_text("2 Fehler, 1 Warnungen")
    expect(admin.locator("#importieren")).to_be_disabled()
    expect(zelle(admin, 1, "geschlecht")).to_contain_text("ungültiges Geschlecht")
    assert "zelle-fehler" in zelle(admin, 1, "geschlecht").get_attribute("class")
    assert "zelle-warnung" in zelle(admin, 2, "nachname").get_attribute("class")

    # Filter: nur fehlerhafte Zeilen
    admin.check("#nur-fehler")
    expect(admin.locator("#import-tabelle tbody tr:visible")).to_have_count(2)
    admin.uncheck("#nur-fehler")

    # Zelle korrigieren → sofortige Prüfung
    zelle(admin, 1, "geschlecht").locator("input").fill("m")
    expect(admin.locator("#zaehler")).to_have_text("1 Fehler, 1 Warnungen")
    assert "zelle-fehler" not in zelle(admin, 1, "geschlecht").get_attribute("class")

    # Zeile löschen → fehlerfrei, Import freigegeben
    admin.locator("#import-tabelle tbody tr").nth(2).locator(".zeile-loeschen").click()
    expect(admin.locator("#zaehler")).to_have_text("0 Fehler, 0 Warnungen")
    expect(admin.locator("#importieren")).to_be_enabled()

    # Zeile hinzufügen → neue leere Zeile hat Fehler
    admin.click("#zeile-hinzufuegen")
    expect(admin.locator("#import-tabelle tbody tr")).to_have_count(3)
    expect(admin.locator("#zaehler")).to_have_text(re.compile(r"^[1-9]\d* Fehler"))
    expect(admin.locator("#importieren")).to_be_disabled()
    neu = admin.locator("#import-tabelle tbody tr").nth(2)
    for feld, wert in (("startnummer", "13"), ("vorname", "Cara"), ("nachname", "C"),
                       ("geschlecht", "w"), ("geburtsjahr", "1975")):  # fmt: skip
        neu.locator(f"td[data-feld={feld}] input").fill(wert)
    expect(admin.locator("#zaehler")).to_have_text("0 Fehler, 0 Warnungen")

    # Entwurf liegt auf dem Server: neu laden behält die Korrekturen
    admin.reload()
    expect(admin.locator("#import-tabelle tbody tr")).to_have_count(3)
    expect(zelle(admin, 1, "geschlecht").locator("input")).to_have_value("m")
    with admin.expect_download() as dl:
        admin.click("#entwurf-csv")
    inhalt = Path(dl.value.path()).read_text(encoding="utf-8-sig")
    assert "12;Ben;Neu;m;1980" in inhalt and "13;Cara;C;w;1975" in inhalt

    # Ersetzen zeigt eine Warnung
    admin.check("input[name=modus][value=ersetzen]")
    expect(admin.locator("#ersetzen-warnung")).to_be_visible()
    admin.check("input[name=modus][value=ergaenzen]")
    expect(admin.locator("#ersetzen-warnung")).to_be_hidden()
    admin.click("#importieren")
    expect(admin.locator(".meldung")).to_contain_text("3 Teilnehmer importiert")
    expect(admin.locator("#teilnehmer tbody tr")).to_have_count(13)


def test_admin_layout_auf_dem_handy(browser, server):
    ctx, page = anmelden(browser, server, 390, 800)
    for reiter in ("uebersicht", "einstellungen", "teilnehmer", "geraete", "aufgaben", "protokoll",
                   "ergebnis", "drucken", "status"):  # fmt: skip
        admin_seite(page, reiter)
        breite = page.evaluate("document.documentElement.scrollWidth")
        assert breite <= 390, (reiter, breite)  # kein waagerechtes Scrollen der Seite
        assert page.locator("#sl-nav").is_hidden()  # Menü klappt nach dem Seitenwechsel zu
    basis = page.url.rsplit("/", 1)[0]
    for pfad in ("/teilnehmer/neu", "/import", "/geraetedatei", "/teilnehmer?stand=strecke"):
        page.goto(basis + pfad)
        assert page.evaluate("document.documentElement.scrollWidth") <= 390, pfad
    page.goto(basis + "/teilnehmer")
    page.locator("#teilnehmer a.tbl-link").first.click()  # Fahrer-Seite
    assert page.evaluate("document.documentElement.scrollWidth") <= 390, "fahrer"
    for pfad in ("/admin", "/admin/saetze", "/admin/saetze/import", "/admin/saetze/klassen/1",
                 "/admin/saetze/wertung/1", "/admin/einrichtung/neu"):  # fmt: skip
        page.goto(page.url.split("/admin")[0] + pfad)
        assert page.evaluate("document.documentElement.scrollWidth") <= 390, pfad
    page.goto(basis)
    admin_seite(page, "teilnehmer")
    box = page.locator("#teilnehmer").locator("xpath=ancestor::div[contains(@class,'tbl-box')]")
    assert box.evaluate("e => getComputedStyle(e).overflow").startswith("auto")
    kopf = page.locator("#teilnehmer thead th").first
    assert kopf.evaluate("e => getComputedStyle(e).position") == "sticky"
    # Kopfzeile bleibt beim Scrollen im Tabellenbereich sichtbar
    oben = kopf.bounding_box()["y"]
    box.evaluate("e => e.scrollTop = 200")
    assert abs(kopf.bounding_box()["y"] - oben) < 2
    ctx.close()
