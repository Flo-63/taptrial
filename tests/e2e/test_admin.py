"""Admin im Browser: Admin auf dem Laptop, Geräte als Handys."""

import uuid
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import TOKENS, admin_seite, warte_bis

FIXTURE = Path(__file__).resolve().parents[2] / "config" / "teilnehmer_beispiel.csv"


@pytest.fixture
def admin(browser, server):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, service_workers="block")
    page = ctx.new_page()
    page.goto(f"{server.url}/admin")
    page.fill("input[name=token]", TOKENS["admin"])
    page.click("#anmelden button")
    expect(page.locator("h1")).to_have_text("Veranstaltungen")
    yield page
    ctx.close()


def veranstaltung_oeffnen(admin, name="E2E-Zeitfahren"):
    admin.goto(admin.url.split("/admin")[0] + "/admin")
    admin.click(f"#veranstaltungen a:text-is('{name}')")


def seite(admin, name):
    admin_seite(admin, name)


def zeile(admin, nr):
    return admin.locator(f"#ergebnis tr[data-nr='{nr}']")


def geraet_oeffnen(ctx, url):
    page = ctx.new_page()
    page.goto(url)
    page.wait_for_function("window.geraetBereit === true")
    return page


def stempel(server, art, nr=None, zeit=None, rolle="ziel"):
    e = {"uuid": str(uuid.uuid4()), "art": art, "zeit_ms": zeit, "erstellt_ms": zeit,
         "geraet": f"{rolle}-api"}  # fmt: skip
    if nr is not None:
        e["startnummer"] = nr
    server.senden([e], rolle=rolle)
    return e


def test_einrichtung_komplett_ueber_admin(admin, server, neues_geraet):
    # geführte Einrichtung: 1 Name/Datum → 2 Klassen & Wertung → 3 Teilnehmer → 4 Abschluss
    admin.click("#neue-veranstaltung")
    expect(admin.locator("#fortschritt li.aktuell")).to_contain_text("Name und Datum")
    admin.fill("#schritt input[name=name]", "Kottenforst 2026")
    admin.fill("#schritt input[name=datum]", "2026-10-10")
    admin.click("#weiter")
    expect(admin.locator(".meldung")).to_contain_text("angelegt")
    expect(admin.locator("#fortschritt li.aktuell")).to_contain_text("Klassen")
    expect(admin.locator("#fortschritt li.erledigt")).to_have_count(1)
    # Erst je Platz entscheiden, dann „Strecken & Zuordnung“ und „Weiter“
    expect(admin.locator("#weiter")).to_be_disabled()
    admin.click(".platz-anlegen")
    expect(admin.locator("#strecken-tabelle tbody tr")).to_have_count(1)
    admin.click("#weiter")
    expect(admin.locator("#fortschritt li.aktuell")).to_contain_text("Teilnehmer")
    admin.set_input_files("#import input[type=file]", str(FIXTURE))
    admin.click("#import button")
    expect(admin.locator("#zaehler")).to_contain_text("0 Fehler")
    admin.click("#importieren")
    expect(admin.locator(".meldung")).to_contain_text("50 Teilnehmer importiert")
    expect(admin.locator("#anzahl-teilnehmer")).to_have_text("50")
    admin.click("#zurueck")  # Zurück behält den Stand
    expect(admin.locator("#fortschritt li.aktuell")).to_contain_text("Klassen")
    admin.click("#weiter")
    admin.click("#weiter")
    expect(admin.locator("#fortschritt li.aktuell")).to_contain_text("Zielmonitor")
    expect(admin.locator("#monitor-vorschau")).to_be_visible()
    expect(
        admin.locator("#seiten-tabelle tr[data-typ=ergebnis] input[name=seite_an_0]")
    ).to_be_checked()
    admin.click("#weiter")
    expect(admin.locator("#zusammenfassung")).to_contain_text("Teilnehmer: 50")
    expect(admin.locator("#links .qr svg")).to_have_count(3)
    start_url = admin.locator(".geraet[data-titel=Start] a.link").get_attribute("href")
    ziel_url = admin.locator(".geraet[data-titel=Ziel] a.link").get_attribute("href")
    leitung_url = admin.locator(".geraet[data-titel=Rennleitung] a.link").get_attribute("href")
    assert "sicht=rennleitung" in leitung_url and TOKENS["start"] not in start_url
    admin.click("#fertig")
    expect(admin.locator(".meldung")).to_contain_text("aktive Veranstaltung")
    seite(admin, "teilnehmer")
    expect(admin.locator("#teilnehmer tbody tr")).to_have_count(50)

    start = geraet_oeffnen(neues_geraet(), start_url)
    expect(start.locator("#naechster-nr")).to_have_text("1")
    start.click("#btn-start")
    ziel = geraet_oeffnen(neues_geraet(), ziel_url)
    ziel.evaluate("window.geraet.sync()")
    ziel.locator("#fahrer button[data-nr='1']").click()

    seite(admin, "ergebnis")
    warte_bis(lambda: _fahrzeit_sichtbar(admin, 1), text="Fahrzeit im Admin")
    expect(zeile(admin, 1).locator(".status")).to_have_text("")
    # die alte Veranstaltung ist nicht mehr aktiv, ihr Token aber weiter gültig (läuft noch)
    assert server.stand()["veranstaltung"]["name"] == "Kottenforst 2026"


def _fahrzeit_sichtbar(admin, nr) -> bool:
    admin.reload()
    return zeile(admin, nr).locator(".zeit").inner_text().strip() != ""


def fahrer_oeffnen(admin, nr):
    seite(admin, "teilnehmer")
    admin.locator(f"#teilnehmer tr[data-nr='{nr}'] a.tbl-link").click()
    expect(admin.locator("h1")).to_contain_text(f"Nr. {nr} ·")


def aktion(admin, kennung, **felder):
    """Aufklappbare Aktion auf Fahrer-Seite oder „Aufgaben“ ausfüllen und absenden."""
    admin.click(f"#{kennung} summary")
    for name, wert in felder.items():
        feld = admin.locator(f"#{kennung} [name={name}]")
        if feld.evaluate("e => e.tagName") == "SELECT":
            feld.select_option(value=wert)
        else:
            feld.fill(wert)
    admin.locator(f"#{kennung} form button[type=submit]").click()
    expect(admin.locator(".meldung")).to_be_visible()


def test_jede_korrekturart_ueber_die_oberflaeche(admin, server):
    starts = server.starten(1, 2, 3, 4, abstand_ms=60_000)
    z1 = stempel(server, "ZIEL", 1, starts[2] + 20 * 60_000)  # eigentlich Nr. 2
    stempel(server, "ZIEL", 3, starts[3] + 18 * 60_000)
    doppelt = stempel(server, "ZIEL", 3, starts[3] + 19 * 60_000)
    stempel(server, "ZIEL", 4, starts[4] + 21 * 60_000)
    veranstaltung_oeffnen(admin)
    seite(admin, "aufgaben")
    expect(admin.locator("#konflikte")).to_contain_text("2 Zielstempel")
    # Doppelstempel direkt in der Aufgabe stornieren
    fall = admin.locator("[data-fall=mehrere_ziele][data-nr='3']")
    fall.locator("input[name=notiz]").fill("Doppelstempel")
    fall.locator(f"button[value='{doppelt['uuid']}']").click()
    expect(admin.locator(".meldung")).to_contain_text("storniert")
    expect(admin.locator("#konflikte")).to_contain_text("Keine Konflikte")
    # Sonderfall: Zeiten tauschen
    aktion(admin, "aktion-tauschen", startnummer="3", startnummer_b="4", art="ZIEL",
           notiz="Nummern vertauscht")  # fmt: skip
    # Fahrer-Seite: umbuchen, manuelle Zeit, Status
    fahrer_oeffnen(admin, 1)
    aktion(admin, "aktion-umbuchen", stempel=z1["uuid"], startnummer="2", notiz="war Nr. 2")
    expect(admin.locator("h1")).to_contain_text("Nr. 1 ·")  # bleibt auf der Fahrer-Seite
    aktion(admin, "aktion-manuell", art="ZIEL", fahrzeit="25:00,0", notiz="Handstoppuhr")
    expect(admin.locator("#fahrer-badges")).to_contain_text("manuelle Zeit")
    fahrer_oeffnen(admin, 4)
    aktion(admin, "aktion-status", wert="DNF", notiz="Sturz")
    expect(admin.locator("#fahrer-status")).to_have_text("DNF")
    expect(admin.locator("#verlauf .notiz", has_text="Sturz")).to_be_visible()

    seite(admin, "ergebnis")
    expect(zeile(admin, 2).locator(".zeit")).to_have_text("20:00,0")
    expect(zeile(admin, 1).locator(".zeit")).to_have_text("25:00,0")
    expect(zeile(admin, 1)).to_contain_text("manuell")
    # Nr. 3 bekommt das Ziel von Nr. 4 (Start 1 min nach Nr. 3, 21 min Fahrzeit) → 22 min
    expect(zeile(admin, 3).locator(".zeit")).to_have_text("22:00,0")
    expect(zeile(admin, 4).locator(".status")).to_have_text("DNF")

    seite(admin, "protokoll")
    for notiz in ("war Nr. 2", "Doppelstempel", "Nummern vertauscht", "Handstoppuhr", "Sturz"):
        expect(admin.locator("#protokoll .notiz", has_text=notiz).first).to_be_visible()
    expect(admin.locator("#protokoll tr.storniert")).to_have_count(1)
    admin.fill("input[name=nr]", "4")
    admin.click("text=Filtern")
    expect(admin.locator("#protokoll .notiz", has_text="Sturz")).to_be_visible()
    expect(admin.locator("#protokoll .notiz", has_text="Handstoppuhr")).to_have_count(0)


def test_token_erneuern(admin, server, neues_geraet):
    veranstaltung_oeffnen(admin)
    seite(admin, "geraete")
    admin.click("#erneuern-start")
    expect(admin.locator("#bestaetigung-dialog")).to_be_visible()  # umkehrbar: Dialog
    expect(admin.locator("#bestaetigung-text")).to_contain_text("alte Link")
    admin.click("#dialog-ok")
    expect(admin.locator(".meldung")).to_contain_text("erneuert")
    alt = httpx.get(
        f"{server.url}/api/stand", headers={"Authorization": f"Bearer {TOKENS['start']}"}
    )
    assert alt.status_code == 401
    neu_url = admin.locator(".geraet[data-titel=Start] a.link").get_attribute("href")
    assert TOKENS["start"] not in neu_url
    start = geraet_oeffnen(neues_geraet(), neu_url)
    expect(start.locator("#naechster-nr")).to_have_text("1")
    expect(start.locator("#netz")).to_have_text("online")
    # Zielgerät mit unverändertem Token läuft weiter
    ziel = httpx.get(
        f"{server.url}/api/stand", headers={"Authorization": f"Bearer {TOKENS['ziel']}"}
    )
    assert ziel.status_code == 200


def gefahr(admin, kennung, name):
    """Aktion der Gefahrenzone: aufklappen, Namen eintippen, absenden."""
    admin.click(f"#{kennung}-zeile summary")
    admin.fill(f"#{kennung} input[name=name]", name)
    admin.click(f"#{kennung} button[type=submit]")


def test_lebenszyklus_abschliessen_exporte_anonymisieren_loeschen(admin, server):
    starts = server.starten(1)
    stempel(server, "ZIEL", 1, starts[1] + 15 * 60_000)
    veranstaltung_oeffnen(admin)
    seite(admin, "status")
    gefahr(admin, "abschliessen", "E2E-Zeitfahren")
    expect(admin.locator(".meldung")).to_contain_text("abgeschlossen")
    expect(admin.locator(".hinweis", has_text="Diese Veranstaltung ist")).to_contain_text(
        "schreibgeschützt"
    )
    r = httpx.get(f"{server.url}/api/stand", headers={"Authorization": f"Bearer {TOKENS['ziel']}"})
    assert r.status_code == 410  # Veranstaltung beendet
    seite(admin, "aufgaben")
    expect(admin.locator("#tauschen button")).to_be_disabled()

    seite(admin, "drucken")
    with admin.expect_download() as dl:
        admin.click("#csv-ergebnis")
    inhalt = Path(dl.value.path()).read_text(encoding="utf-8-sig")
    assert "Vorname1;Nachname1" in inhalt and "15:00,0" in inhalt
    with admin.expect_download() as dl:
        admin.click("#csv-protokoll")
    assert "ZIEL" in Path(dl.value.path()).read_text(encoding="utf-8-sig")

    seite(admin, "status")
    gefahr(admin, "anonymisieren", "falsch")
    expect(admin.locator(".fehler")).to_contain_text("Namen")
    gefahr(admin, "anonymisieren", "E2E-Zeitfahren")
    expect(admin.locator(".meldung")).to_contain_text("anonymisiert")
    seite(admin, "ergebnis")
    expect(zeile(admin, 1).locator(".zeit")).to_have_text("15:00,0")
    expect(admin.locator("#ergebnis")).not_to_contain_text("Nachname1")
    expect(zeile(admin, 1)).to_contain_text("Damen 61-70")

    seite(admin, "status")
    gefahr(admin, "loeschen", "E2E-Zeitfahren")
    expect(admin.locator(".meldung")).to_contain_text("komplett gelöscht")
    expect(admin.locator("#veranstaltungen")).to_contain_text("Noch keine Veranstaltung")


def test_dsq_entscheiden_und_ergebnis_offiziell(admin, server):
    starts = server.starten(1, 2)
    for nr in (1, 2):
        stempel(server, "ZIEL", nr, starts[nr] + (10 + nr) * 60_000)
        server.senden([{"uuid": f"dsq-{nr}", "art": "STATUS", "startnummer": nr,
                        "wert": "DSQ_VORLAEUFIG", "notiz": f"Grund {nr}", "erstellt_ms": 1,
                        "geraet": "ziel-api"}], rolle="ziel")  # fmt: skip
    veranstaltung_oeffnen(admin)
    expect(admin.locator("#kpi-aufgaben")).to_contain_text("2offene Aufgaben")
    expect(admin.locator("[data-aufgabe=aufgaben]")).to_contain_text("2 offene Aufgaben")
    seite(admin, "ergebnis")
    expect(zeile(admin, 1)).to_contain_text("unter Vorbehalt")
    expect(admin.locator("#ergebnisstatus")).to_contain_text("gesperrt")
    seite(admin, "aufgaben")
    expect(admin.locator("#dsq")).to_contain_text("Vorläufige DSQ (2)")

    form = admin.locator(".dsq-form[data-nr='1']")
    form.locator("input[name=notiz]").fill("Video eindeutig")
    form.locator("button[value=bestaetigen]").click()
    expect(admin.locator(".meldung")).to_contain_text("bestätigt")
    form = admin.locator(".dsq-form[data-nr='2']")
    form.locator("input[name=notiz]").fill("kein Verstoß")
    form.locator("button[value=verwerfen]").click()
    expect(admin.locator("#dsq")).to_have_count(0)
    seite(admin, "ergebnis")
    expect(zeile(admin, 1).locator(".status")).to_have_text("DSQ")

    # Ergebnis bleibt gesperrt, solange Konflikte offen sind (hier: nicht zugeordnete Zielzeit)
    stempel(server, "ZIEL", None, starts[2] + 30 * 60_000)
    admin.reload()
    expect(admin.locator("#ergebnisstatus")).to_contain_text("Konflikt")
    seite(admin, "aufgaben")
    fall = admin.locator("[data-fall=offene-zeit]")
    fall.locator("details summary").click()
    fall.locator("details input[name=notiz]").fill("Fehlstempel")
    fall.locator("details button[type=submit]").click()
    expect(admin.locator(".meldung")).to_contain_text("storniert")
    seite(admin, "ergebnis")
    admin.click("#offiziell-erklaeren")
    expect(admin.locator("#bestaetigung-dialog")).to_be_visible()
    admin.click("#dialog-ok")
    expect(admin.locator("#ergebnisstatus h2")).to_contain_text("Offizielles Ergebnis, Stand")
    admin.fill("#ergebnisstatus input[name=notiz]", "Einspruch")
    admin.click("#ergebnisstatus button")
    admin.click("#dialog-ok")
    expect(admin.locator("#ergebnisstatus h2")).to_have_text("Vorläufiges Ergebnis")
    seite(admin, "protokoll")
    expect(admin.locator("#protokoll tr[data-art=ERGEBNIS]")).to_have_count(2)
    expect(admin.locator("#protokoll .notiz", has_text="Einspruch")).to_be_visible()


def test_einstieg_anmelden_abmelden(browser, server):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, service_workers="block")
    page = ctx.new_page()
    page.goto(server.url + "/")
    expect(page.locator("#veranstaltung")).to_have_text("E2E-Zeitfahren")
    page.click("#link-admin")
    expect(page).to_have_url(server.url + "/admin")
    expect(page.locator("#anmelden input[name=token]")).to_be_visible()
    expect(page.locator("#link-ergebnisse")).to_have_attribute("href", "/ergebnisse")
    page.fill("input[name=token]", "falsch")
    page.click("#anmelden button")
    expect(page.locator(".fehler")).to_contain_text("Admin-Token falsch")
    page.fill("input[name=token]", TOKENS["admin"])
    page.click("#anmelden button")
    expect(page.locator("h1")).to_have_text("Veranstaltungen")
    page.click("text=Abmelden")
    expect(page.locator("#anmelden input[name=token]")).to_be_visible()
    # Alt-Link funktioniert weiter und entfernt den Token aus der Adresszeile
    page.goto(f"{server.url}/admin?token={TOKENS['admin']}")
    expect(page).to_have_url(server.url + "/admin")
    expect(page.locator("h1")).to_have_text("Veranstaltungen")
    ctx.close()


def test_assistent_strecke_mit_messpunkten(admin):
    """„mit Messpunkten“ → „Messpunkte einrichten“ → Strecken-Seite → zurück in den Assistenten."""
    admin.click("#neue-veranstaltung")
    admin.fill("#schritt input[name=name]", "Mit Posten")
    admin.fill("#schritt input[name=datum]", "2026-10-11")
    admin.click("#weiter")
    admin.click(".platz-anlegen")  # Erst für den Platz entscheiden
    knopf = admin.locator(".messpunkte-einrichten").first
    expect(knopf).to_be_hidden()  # „einfach“ wie bisher
    admin.check("input[name=strecke_art_0][value=messpunkte]")
    expect(knopf).to_be_visible()
    admin.fill("input[name=strecke_name_0]", "Rund")
    knopf.click()
    expect(admin.locator("#streckenverlauf")).to_be_visible()
    expect(admin.locator(".meldung")).to_contain_text("Schritt 2 gespeichert")
    admin.click("#zurueck-strecken")  # „Zurück zum Assistenten“
    expect(admin.locator("#fortschritt li.aktuell")).to_contain_text("Klassen")
    expect(admin.locator("input[name=strecke_name_0]")).to_have_value("Rund")
    expect(admin.locator("#strecke-laden")).to_be_visible()


STRECKE_5KM = """version = 1
name = "Jugendrunde"
laenge_km = 5.2
messpunkte = []
"""


def test_assistent_plaetze_datei_und_platzhalter(admin, tmp_path):
    """Der Assistent legt keine Strecken von selbst an. Platz 5 km aus Datei (deren
    Länge gilt, mit Hinweis), Platz 20 km als Platzhalter – Klassen richtig, keine weiteren Strecken."""
    datei = tmp_path / "jugend.toml"
    datei.write_text(STRECKE_5KM, encoding="utf-8")
    admin.click("#neue-veranstaltung")
    admin.fill("#schritt input[name=name]", "Mit Plätzen")
    admin.fill("#schritt input[name=datum]", "2026-10-12")
    admin.click("#weiter")
    admin.select_option("#wahl-wertungssatz", label="DRA A.1 flach")
    admin.locator(".platz[data-platz='5 km'] .platz-laden").click()  # speichert Schritt 2 zuerst
    expect(admin.locator("#laden-platz")).to_have_count(0)  # Hinweis erst in der Vorschau
    admin.set_input_files("#strecke-datei", str(datei))
    admin.click("#vorschau-zeigen")
    expect(admin.locator("#laden-platz")).to_contain_text("„5 km“")
    admin.click("#uebernehmen")
    expect(admin.locator("#fortschritt li.aktuell")).to_contain_text("Klassen")
    expect(admin.locator("#wahl-wertungssatz option:checked")).to_have_text(
        "DRA A.1 flach"
    )  # nichts verloren
    expect(admin.locator(".meldung")).to_contain_text("aus der Datei geladen")
    platz5 = admin.locator(".platz[data-platz='5 km']")
    expect(platz5.locator(".platz-entschieden")).to_contain_text("Jugendrunde")
    expect(platz5.locator(".platz-hinweis")).to_contain_text(
        "Die Datei gibt 5,2 km an (Platz 5,0 km) – es gilt die Datei."
    )
    expect(admin.locator("#weiter")).to_be_disabled()
    admin.locator(".platz[data-platz='20 km'] .platz-anlegen").click()
    expect(admin.locator(".platz[data-platz='20 km'] .platz-entschieden")).to_contain_text("20 km")
    expect(admin.locator("#weiter")).to_be_enabled()
    namen = admin.locator("#strecken-tabelle input.strecke-name")
    expect(namen).to_have_count(2)  # keine weiteren Strecken
    expect(namen.nth(0)).to_have_value("Jugendrunde")
    expect(namen.nth(1)).to_have_value("20 km")
    expect(
        admin.locator("#zuordnung tr[data-klasse='Jungen 12-14'] select option:checked")
    ).to_have_text("Jugendrunde")
    expect(
        admin.locator("#zuordnung tr[data-klasse='Herren 31-40'] select option:checked")
    ).to_have_text("20 km")
    admin.click("#weiter")
    expect(admin.locator("#fortschritt li.aktuell")).to_contain_text("Teilnehmer")


STRECKE_20KM = """version = 1
name = "20 km Rund"
laenge_km = 20.0
messpunkte = []
"""


def test_assistent_platz_aendern_und_tatsaechliche_zuordnung(admin, tmp_path):
    """Platz 5 km versehentlich mit der 20-km-Datei geladen → „Ändern“ → Platzhalter:
    Klassen richtig, keine überzählige Strecke; eine Umordnung in der Klassenzuordnung zeigt
    „Strecken je Länge“ sofort."""
    datei = tmp_path / "zwanzig.toml"
    datei.write_text(STRECKE_20KM, encoding="utf-8")
    admin.click("#neue-veranstaltung")
    admin.fill("#schritt input[name=name]", "Korrektur")
    admin.fill("#schritt input[name=datum]", "2026-10-13")
    admin.click("#weiter")
    admin.select_option("#wahl-wertungssatz", label="DRA A.1 flach")
    platz5 = admin.locator(".platz[data-platz='5 km']")
    platz5.locator(".platz-laden").click()
    admin.set_input_files("#strecke-datei", str(datei))
    admin.click("#vorschau-zeigen")
    admin.click("#uebernehmen")
    expect(platz5.locator(".platz-entschieden")).to_contain_text("20 km Rund")  # falsch
    platz5.locator(".platz-aendern summary").click()
    platz5.locator("input[name='platz_name:5 km']").fill("5 km")
    platz5.locator("input[name='platz_laenge:5 km']").fill("5,0")
    platz5.locator(".platz-anlegen").click()
    expect(admin.locator(".meldung")).to_contain_text("Platzhalter-Strecke „5 km“ angelegt")
    expect(platz5.locator(".platz-entschieden")).to_contain_text("5 km")
    expect(platz5.locator(".platz-entschieden")).not_to_contain_text("20 km Rund")
    admin.locator(".platz[data-platz='20 km'] .platz-anlegen").click()
    namen = admin.locator("#strecken-tabelle input.strecke-name")
    expect(namen).to_have_count(2)  # „20 km Rund“ ist weg
    expect(namen.nth(0)).to_have_value("5 km")
    expect(namen.nth(1)).to_have_value("20 km")
    expect(
        admin.locator("#zuordnung tr[data-klasse='Jungen 12-14'] select option:checked")
    ).to_have_text("5 km")
    # Umordnung in der Klassenzuordnung → Anzeige folgt (eine Quelle der Wahrheit)
    vid = admin.url.split("/einrichtung/")[1].split("/")[0]
    admin.goto(admin.url.split("/admin/")[0] + f"/admin/v/{vid}/strecken")
    admin.select_option("#zuordnung tr[data-klasse='Jungen 12-14'] select", label="20 km")
    admin.click("#zuordnung-speichern")
    expect(admin.locator(".meldung")).to_contain_text("Klassenzuordnung gespeichert")
    admin.goto(admin.url.split("/admin/")[0] + f"/admin/einrichtung/{vid}/2")
    expect(platz5.locator(".platz-entschieden")).to_contain_text("20 km 20,0 km (Jungen 12-14)")
