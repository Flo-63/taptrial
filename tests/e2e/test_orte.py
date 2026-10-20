"""Im Browser: Seite „Messpunkte“, Start-/Zielort im Strecken-Editor, Geräte je Ort."""

from playwright.sync_api import expect

from tests.e2e.conftest import TOKENS, admin_seite


def admin(server, browser, breite=1280):
    ctx = browser.new_context(viewport={"width": breite, "height": 900}, service_workers="block")
    a = ctx.new_page()
    a.goto(f"{server.url}/admin")
    a.fill("input[name=token]", TOKENS["admin"])
    a.click("#anmelden button")
    a.click("#veranstaltungen a")
    return ctx, a


def ort_hinzufuegen(a, art: str, name: str):
    a.select_option("#ort-neu-art", label=art)
    a.click("#messpunkt-neu")
    a.locator("#messpunkte tbody tr").last.locator(".mp-name").fill(name)


def test_orte_anlegen_und_strecke_waehlt_start_und_ziel(server, browser):
    ctx, a = admin(server, browser)
    admin_seite(a, "messpunkte")
    expect(a.locator("h1")).to_have_text("Messpunkte")
    expect(a.locator("#messpunkte tr[data-ort=Start] .ort-art")).to_have_text("Start Standard")
    expect(a.locator("#messpunkte tr[data-ort=Ziel] .ort-art")).to_have_text("Ziel Standard")
    ort_hinzufuegen(a, "Start", "Nord")
    expect(a.locator("#messpunkte tbody tr").last.locator(".ort-art")).to_have_text("Start")
    ort_hinzufuegen(a, "Ziel", "Hof")
    a.click("#messpunkte-speichern")
    expect(a.locator(".meldung")).to_contain_text("Messpunkte gespeichert")
    expect(a.locator("#messpunkte tr[data-ort=Nord][data-art=start]")).to_have_count(1)
    expect(a.locator("#messpunkte tr[data-ort=Hof][data-art=ziel]")).to_have_count(1)
    expect(a.locator("#ort-neu-art option")).to_have_text(["Start", "Zwischenzeit", "Ziel"])
    # Strecken-Editor: Startort Nord, Zielort Hof – Punktfolge zeigt die Namen sofort
    admin_seite(a, "strecken")
    a.click("#strecke-oeffnen-0")
    a.click("#strecke-reiter [data-reiter=verlauf]")
    expect(a.locator("#punktfolge > li.pf-fest").first).to_have_text("Start")
    a.select_option("#s-startort", label="Nord")
    a.select_option("#s-zielort", label="Hof")
    expect(a.locator("#punktfolge > li.pf-fest").first).to_have_text("Nord")
    expect(a.locator("#punktfolge > li.pf-fest").last).to_have_text("Hof")
    expect(a.locator("#verlauf-vorschau .abfolge")).to_have_text("Nord · Hof")
    a.click("#alles-speichern")
    expect(a.locator(".meldung")).to_contain_text("Strecke gespeichert")
    a.click("#strecke-reiter [data-reiter=verlauf]")
    expect(a.locator("#s-startort option:checked")).to_have_text("Nord")
    expect(a.locator("#s-zielort option:checked")).to_have_text("Hof")
    admin_seite(a, "messpunkte")
    expect(a.locator("#messpunkte tr[data-ort=Nord] .mp-sperre")).to_contain_text("genutzt von")
    ctx.close()


# --- Geräte je Ort ------------------------------------------------------------------

import pytest

from tests.e2e.conftest import jetzt_synchronisieren, oeffnen, offline
from tests.e2e.test_start import ENTPRELLUNG_MS
from tests.e2e.test_ziel import auswahl, kachel, rennleitung


@pytest.mark.parametrize(("zwei_strecken", "rundkurs"), [(True, "orte")])
def test_zwei_startorte_eigene_startfolge_offline(server, neues_geraet, zwei_strecken, rundkurs):
    """Damen (ungerade Nummern) starten am Startort „Nord“, Herren am Standard-Start – je Gerät eine
    eigene Startfolge, auch offline; der bisherige Start-Link bleibt der Standard-Startort."""
    nord = oeffnen(neues_geraet(), server, "start-nord")
    start = oeffnen(neues_geraet(), server, "start")
    expect(nord.locator(".statusleiste .rolle")).to_have_text("Start · Nord")
    expect(start.locator(".statusleiste .rolle")).to_have_text("Start")
    expect(nord.locator("#naechster-nr")).to_have_text("1")
    expect(start.locator("#naechster-nr")).to_have_text("2")
    expect(nord.locator("#fortschritt")).to_have_text("Gestartet: 0 von 5")
    offline(nord.context, True)
    for _ in range(2):
        nord.click("#btn-start")
        nord.wait_for_timeout(ENTPRELLUNG_MS)
    start.click("#btn-start")
    expect(nord.locator("#naechster-nr")).to_have_text("5")
    expect(start.locator("#naechster-nr")).to_have_text("4")
    offline(nord.context, False)
    jetzt_synchronisieren(nord)
    jetzt_synchronisieren(start)
    expect(nord.locator("#ausstehend")).to_have_text("0 ausstehend", timeout=10_000)
    assert sorted(server.stand()["gestartet"]) == [1, 2, 3]
    expect(nord.locator("#fortschritt")).to_have_text("Gestartet: 2 von 5")
    expect(start.locator("#fortschritt")).to_have_text("Gestartet: 1 von 5")


@pytest.mark.parametrize(("zwei_strecken", "rundkurs"), [(True, "zielorte")])
def test_zielort_zeigt_eigene_fahrer_und_bietet_sie_zuerst_an(
    server, neues_geraet, zwei_strecken, rundkurs
):
    """Damen (ungerade) enden am Zielort „Hof“: dessen Zielgerät zeigt nur sie; beim Zuordnen einer
    Zeit ohne Nummer stehen sie vorne, die Rennleitung kann aber alle zuordnen."""
    server.starten(1, 2, 3, 4)
    hof = oeffnen(neues_geraet(), server, "ziel-hof")
    expect(hof.locator(".statusleiste .rolle")).to_contain_text("Hof")
    expect(kachel(hof, 1)).to_be_visible()
    expect(kachel(hof, 3)).to_be_visible()
    expect(kachel(hof, 2)).to_have_count(0)
    expect(hof.locator("#ziel-info")).to_have_text("2 auf der Strecke")
    hof.click("#btn-ohne-nummer")
    rennleitung(hof)
    hof.locator("#offene-zeiten button").first.click()
    nummern = hof.locator("#auswahl button").evaluate_all("bs => bs.map(b => Number(b.dataset.nr))")
    assert nummern[:5] == [1, 3, 5, 7, 9] and sorted(nummern) == list(range(1, 11))
    auswahl(hof, 4).click()  # Rennleitung darf auch andere Fahrer zuordnen
    ziel = oeffnen(neues_geraet(), server, "ziel")
    expect(kachel(ziel, 2)).to_be_visible()
    expect(kachel(ziel, 1)).to_have_count(0)
    jetzt_synchronisieren(hof)
    assert server.teilnehmer(4)["ziel_ms"] is not None


# --- Strecke im Assistenten aus Datei ersetzen --------------------------------------


def test_assistent_strecke_aus_datei_ersetzen(server, browser, tmp_path):
    datei = tmp_path / "strecke.toml"
    datei.write_text(
        'version = 1\nname = "Waldrunde"\nlaenge_km = 12.3\nbeschreibung = "Neu"\nmesspunkte = []\n'
    )
    ctx, a = admin(server, browser)
    vid = a.url.rstrip("/").rsplit("/", 1)[1]
    a.goto(f"{server.url}/admin/einrichtung/{vid}/2")
    a.fill("input[name=strecke_name_0]", "Wald")  # Eingabe geht nicht verloren
    a.click("#strecke-laden-0")
    expect(a.locator(".meldung")).to_contain_text("Schritt 2 gespeichert")
    a.set_input_files("#strecke-datei", str(datei))
    a.click("#vorschau-zeigen")
    expect(a.locator("#modus-ersetzen")).not_to_be_checked()  # nichts vorausgewählt
    expect(a.locator("#modus-neu")).not_to_be_checked()
    expect(a.locator("#laden-name-feld")).to_be_hidden()
    # Ohne Wahl schickt der Browser nicht ab – die Vorschau (und Datei) bleibt
    a.click("#uebernehmen")
    expect(a.locator("#vorschau")).to_be_visible()
    assert a.evaluate("document.getElementById('modus-ersetzen').validity.valueMissing")
    assert "/strecken/laden" in a.url
    a.check("#modus-neu")
    expect(a.locator("#laden-name")).to_be_visible()
    a.check("#modus-ersetzen")
    a.click("#uebernehmen")
    expect(a.locator("#bestaetigung-text")).to_contain_text("Start/Ziel von ‚Wald‘ werden ersetzt.")
    a.click("#dialog-ok")
    expect(a.locator(".meldung")).to_contain_text("aus der Datei ersetzt")
    expect(a.locator("#s-laenge")).to_have_value("12,3")
    expect(a.locator("#s-name")).to_have_value("Wald")
    expect(a.locator("#zurueck-strecken")).to_have_text("Zurück zum Assistenten")
    ctx.close()


# --- Strecke mit Klassen entfernen ---------------------------------------------------


@pytest.mark.parametrize("zwei_strecken", [True])
def test_strecke_mit_klassen_entfernen_per_dialog(server, browser, zwei_strecken):
    ctx, a = admin(server, browser)
    admin_seite(a, "strecken")
    a.click("#strecke-oeffnen-0")
    expect(a.locator("#entfernen-hinweis")).to_contain_text("beim Entfernen wählst du")
    a.click("#entfernen-oeffnen")
    expect(a.locator("#entfernen-dialog")).to_be_visible()
    a.click("#entfernen-abbrechen")
    expect(a.locator("#entfernen-dialog")).to_be_hidden()
    a.click("#entfernen-oeffnen")
    a.click("#entfernen-ok")  # ohne Wahl: Pflichtfeld, Dialog bleibt
    expect(a.locator("#entfernen-dialog")).to_be_visible()
    a.select_option("#klassen-an", label="20 km")
    a.click("#entfernen-ok")
    expect(a.locator(".meldung")).to_contain_text("ihre Klassen fährt jetzt „20 km“")
    expect(a.locator(".strecke-kurz")).to_have_count(1)
    a.click("#strecke-oeffnen-0")
    expect(a.locator("#entfernen-hinweis")).to_contain_text("Gesperrt: die letzte Strecke")
    ctx.close()
