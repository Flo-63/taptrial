"""Im Browser: Klassen- und Wertungssätze im Admin."""

from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import TOKENS, admin_seite, einstellungen


@pytest.fixture
def admin(browser, server):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, service_workers="block")
    page = ctx.new_page()
    page.goto(f"{server.url}/admin")
    page.fill("input[name=token]", TOKENS["admin"])
    page.click("#anmelden button")
    yield page
    ctx.close()


def test_klassensatz_kopieren_bearbeiten_exportieren_loeschen(admin):
    admin.click("#nav-vorlagen")
    expect(admin.locator("#klassensaetze tr[data-satz='DRA 2026']")).to_be_visible()
    admin.click(".sl-nav a[data-seite=wertung]")
    expect(admin.locator("#wertungssaetze tr[data-satz='DRA A.3 Berg Gelände']")).to_be_visible()
    admin.click(".sl-nav a[data-seite=klassen]")
    admin.locator("#klassensaetze tr[data-satz='DRA 2026'] button:has-text('Kopieren')").click()
    expect(admin.locator(".meldung")).to_contain_text("Kopie „Kopie von DRA 2026“ angelegt")

    # Tabelle bearbeiten: Name ändern, letzte Klasse entfernen (→ Lücke), neue Zeile ergänzen
    admin.fill("#klassensatz input[name=name]", "Vereinsmeisterschaft")
    admin.check("#klassen tbody tr:nth-child(1) input[type=checkbox]")  # Jungen 8-11 entfernen
    leer = admin.locator("#klassen tbody tr").nth(20)
    leer.locator("input[name^=name_]").fill("Senioren")
    leer.locator("select").select_option("m")
    leer.locator("input[name^=alter_von_]").fill("111")
    leer.locator("input[name^=alter_bis_]").fill("120")
    admin.click("#klassensatz button[type=submit]")
    expect(admin.locator(".meldung")).to_contain_text("„Vereinsmeisterschaft“ gespeichert")
    expect(admin.locator("#warnung")).to_have_count(0)  # 8-11 fehlt am Rand: keine Lücke
    expect(admin.locator("#klassen input[value='Senioren']")).to_be_visible()

    # Überschneidung → Fehler, Eingaben bleiben
    admin.locator("#klassen tbody tr").nth(0).locator("input[name^=alter_bis_]").fill("16")
    admin.click("#klassensatz button[type=submit]")
    expect(admin.locator(".fehler")).to_contain_text("überschneiden")
    expect(
        admin.locator("#klassen tbody tr").nth(0).locator("input[name^=alter_bis_]")
    ).to_have_value("16")

    with admin.expect_download() as dl:
        admin.click("text=TOML")
    assert "[[klasse]]" in Path(dl.value.path()).read_text(encoding="utf-8")

    admin.click("text=Zurück zu den Vorlagen")
    admin.locator(
        "#klassensaetze tr[data-satz='Vereinsmeisterschaft'] button:has-text('Löschen')"
    ).click()
    admin.click("#dialog-ok")  # Bestätigungsdialog
    expect(admin.locator(".meldung")).to_contain_text("gelöscht")
    expect(
        admin.locator("#klassensaetze tr[data-satz='DRA 2026'] button:has-text('Löschen')")
    ).to_have_count(0)


def test_veranstaltung_zeigt_eingefroren(admin, server):
    server.starten(1)  # erstes Ereignis friert Klassen und Wertung ein
    admin.click("#veranstaltungen a")
    einstellungen(admin, "klassen")
    expect(admin.locator("#eingefroren")).to_contain_text("Eingefroren")
    expect(admin.locator("#eingefroren")).to_contain_text("DRA 2026")
    expect(admin.locator("#regeln select[name=klassensatz_id]")).to_be_disabled()
    # erst die Länge (Reiter „Strecken“), dann die Wertung einschalten (eingefroren: kein Neuvorschlag)
    admin_seite(admin, "strecken")
    admin.click("#strecke-oeffnen-0")
    admin.fill("#s-laenge", "20,0")
    admin.click("#alles-speichern")
    expect(admin.locator(".meldung")).to_contain_text("gespeichert")
    einstellungen(admin, "klassen")
    admin.select_option("#regeln select[name=wertungssatz_id]", label="DRA A.1 flach")
    admin.click("#wertung button[type=submit]")
    expect(admin.locator(".meldung")).to_contain_text(
        "Klassen, Leistungswertung und Strecken gespeichert"
    )


def test_wertungseditor_klassenauswahl(admin):
    admin.click("#nav-vorlagen")
    admin.click(".sl-nav a[data-seite=wertung]")
    admin.click("text=Neuer Wertungssatz")
    admin.fill("#wertungssatz input[name=name]", "Mini")
    # kleiner Klassensatz mit zwei Klassen anlegen geht über die Kopie; hier: DRA 2026 nutzen
    klassen = admin.locator("#stufen tbody tr").first.locator("select option").all_inner_texts()
    assert "Damen 31-40" in klassen and len(klassen) > 2
    admin.locator("#stufen tbody tr").first.locator("select").select_option("Damen 31-40")
    admin.click("#zeile-neu")
    zweite = admin.locator("#stufen tbody tr").nth(1).locator("select option").all_inner_texts()
    assert "Damen 31-40" not in zweite and "Herren 31-40" in zweite
    # nach dem Entfernen ist die Klasse wieder frei
    admin.check("#stufen tbody tr:nth-child(1) input.entfernen")
    zweite = admin.locator("#stufen tbody tr").nth(1).locator("select option").all_inner_texts()
    assert "Damen 31-40" in zweite


def test_wertungseditor_alle_klassen_belegt(admin):
    admin.click("#nav-vorlagen")
    admin.click("text=Neuer Klassensatz")
    admin.fill("#klassensatz input[name=name]", "Zwei")
    for i, (name, g) in enumerate((("Männer", "m"), ("Frauen", "w"))):
        z = admin.locator("#klassen tbody tr").nth(i)
        z.locator("input[name^=name_]").fill(name)
        z.locator("select").select_option(g)
        z.locator("input[name^=alter_von_]").fill("0")
        z.locator("input[name^=alter_bis_]").fill("99")
    admin.click("#klassensatz button[type=submit]")
    expect(admin.locator(".meldung")).to_contain_text("gespeichert")
    admin.click("text=Zurück zu den Vorlagen")
    admin.click(".sl-nav a[data-seite=wertung]")
    admin.click("text=Neuer Wertungssatz")
    admin.select_option("#stufen-klassensatz", label="Zwei")
    erste = admin.locator("#stufen tbody tr").first.locator("select")
    expect(erste.locator("option")).to_have_count(3)  # „– Klasse wählen –“ + 2
    erste.select_option("Männer")
    expect(admin.locator("#zeile-neu")).to_be_enabled()
    admin.click("#zeile-neu")
    zweite = admin.locator("#stufen tbody tr").nth(1).locator("select")
    expect(zweite.locator("option")).to_have_count(2)
    zweite.select_option("Frauen")
    expect(admin.locator("#zeile-neu")).to_be_disabled()
    expect(admin.locator("#alle-klassen")).to_have_text("Alle Klassen haben einen Eintrag.")
    expect(admin.locator("#alle-klassen")).to_be_visible()


def test_klassensatzwechsel_setzt_wertung_zurueck(admin):
    admin.click("#nav-vorlagen")
    admin.locator("#klassensaetze tr[data-satz='DRA 2026'] button:has-text('Kopieren')").click()
    expect(admin.locator(".meldung")).to_contain_text("angelegt")
    admin.click("#nav-veranstaltungen")
    admin.click("#veranstaltungen a")
    einstellungen(admin, "klassen")
    admin.select_option("#wahl-wertungssatz", label="DRA A.1 flach")
    expect(admin.locator("#wertung-zurueckgesetzt")).to_be_hidden()
    admin.select_option("#wahl-klassensatz", label="Kopie von DRA 2026")
    expect(admin.locator("#wahl-wertungssatz")).to_have_value("")
    expect(admin.locator("#wertung-zurueckgesetzt")).to_be_visible()
    sichtbar = admin.locator("#wahl-wertungssatz option:not([hidden])").all_inner_texts()
    assert sichtbar == ["keine Wertung"]
