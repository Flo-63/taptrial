"""Segmente im Browser: Posten am Messpunkt (Beispielstrecke), Segment-Editor im Admin."""

import re

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import (
    TOKENS,
    admin_seite,
    einstellungen,
    jetzt_synchronisieren,
    oeffnen,
    offline,
    warte_bis,
)
from tests.e2e.test_ziel import kachel, positionen


def runde(page, nr):
    return kachel(page, nr).locator(".kachel-runde")


def tippen(page, nr):
    """Nächste Durchfahrt: Geräteuhr 2 min weiter."""
    page.clock.fast_forward(120_000)
    kachel(page, nr).click()


@pytest.fixture
def rundkurs():
    return "messpunkte"


def durchfahrten(server):
    return [e for e in server.ereignisse() if e["art"] == "DURCHFAHRT"]


def test_posten_runden_korrektur_rueckgaengig_offline(server, neues_geraet):
    server.starten(1, 2, 3)
    ctx = neues_geraet()
    p = oeffnen(ctx, server, "posten")
    p.clock.install()
    jetzt_synchronisieren(p)
    expect(p.locator("#posten-titel")).to_have_text("Zwischenzeit Kreisel")
    expect(runde(p, 1)).to_have_text("1. von 3 Durchfahrten")
    leiste = p.locator("#letzter-einlauf")
    hoehe = leiste.bounding_box()["height"]
    kachel(p, 1).click()
    expect(runde(p, 1)).to_have_text("2. von 3 Durchfahrten")
    expect(p.locator("#letzter-text")).to_contain_text("Nr. 1 Nachname1")
    vorher = positionen(p)
    p.click("#btn-plus")
    expect(p.locator("#letzter-text")).to_contain_text("korrigiert +1,0 s")
    assert positionen(p) == vorher and leiste.bounding_box()["height"] == hoehe
    tippen(p, 1)
    expect(runde(p, 1)).to_have_text("3. von 3 Durchfahrten")
    # Rückgängig (zweiter Tipp): Durchfahrt wieder offen
    p.click("#btn-einlauf-zurueck")
    expect(p.locator("#btn-einlauf-zurueck")).to_have_text("Wirklich?")
    p.click("#btn-einlauf-zurueck")
    expect(runde(p, 1)).to_have_text("2. von 3 Durchfahrten")
    expect(p.locator("#letzter-text")).to_have_text("Noch keine Durchfahrt erfasst")
    # offline weiter; nach der letzten Durchfahrt verschwindet die Kachel
    offline(ctx, True)
    tippen(p, 1)
    tippen(p, 1)
    expect(kachel(p, 1)).to_have_count(0, timeout=3_000)
    expect(p.locator("#ausstehend")).to_contain_text("ausstehend")
    offline(ctx, False)
    jetzt_synchronisieren(p)
    expect(p.locator("#ausstehend")).to_have_text("0 ausstehend", timeout=10_000)
    warte_bis(lambda: len(durchfahrten(server)) == 4, text="4 Durchfahrten auf dem Server")
    arten = [
        e["art"]
        for e in server.ereignisse()
        if e["art"] in ("DURCHFAHRT", "STORNO", "ZEITKORREKTUR")
    ]
    assert arten.count("STORNO") == 1 and arten.count("ZEITKORREKTUR") == 1
    t1 = next(t for t in server.stand()["teilnehmer"] if t["startnummer"] == 1)
    assert t1["naechste_runde"]["1"] == 4  # drei gültige Durchfahrten am Kreisel


def test_segmente_im_admin_und_posten_link(server, browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, service_workers="block")
    a = ctx.new_page()
    a.goto(f"{server.url}/admin")
    a.fill("input[name=token]", TOKENS["admin"])
    a.click("#anmelden button")
    a.click("#veranstaltungen a")
    admin_seite(a, "strecken")
    expect(a.locator(".strecke-kurz")).to_contain_text(
        "Start · Kreisel · Berg · Kreisel · Berg · Kreisel · Ziel"
    )
    # neuer Messpunkt „Wald“ auf der Seite „Messpunkte“ (gemeinsam für alle Strecken)
    admin_seite(a, "messpunkte")
    a.click("#messpunkt-neu")
    a.fill("input[name=mp_name_4]", "Wald")
    a.click("#messpunkte-speichern")
    expect(a.locator(".meldung")).to_contain_text("Messpunkte gespeichert")
    expect(a.locator("#warnung")).to_contain_text("„Wald“ kommt noch in keiner Strecke vor")
    # Seite der Strecke: Wald vor dem Ziel; neues Segment Wald → Ziel
    admin_seite(a, "strecken")
    a.click("#strecke-oeffnen-0")
    a.click("#strecke-reiter [data-reiter=verlauf]")
    # Punktfolge (Segmente 28): Start · Kreisel · [Runde ×2: Berg · Kreisel] · Ziel
    expect(a.locator("#punktfolge > li")).to_have_count(4)
    expect(a.locator("#segmente-tabelle tbody tr")).to_have_count(4)
    a.click("#punkt-neu")
    a.locator("#punktfolge > li.pf-punkt").last.locator("select").select_option("Wald")
    expect(a.locator("#segmente-tabelle tbody tr")).to_have_count(5)  # Kreisel → Wald, Wald → Ziel
    expect(a.locator("#segmente-tabelle tbody tr").nth(3)).to_contain_text("Kreisel → Wald")
    expect(
        a.locator(".segment-abfolge .abfolge")
    ).to_have_text(  # Vorschau sofort, noch ungespeichert
        "Start · Kreisel · Berg · Kreisel · Berg · Kreisel · Wald · Ziel"
    )
    expect(a.locator("#ungespeichert")).to_be_visible()
    a.fill("input[name=sg_km_4]", "0,8")
    a.click("#alles-speichern")
    expect(a.locator(".meldung")).to_contain_text("Strecke gespeichert")
    expect(a.locator(".segment-abfolge .abfolge")).to_have_text(
        "Start · Kreisel · Berg · Kreisel · Berg · Kreisel · Wald · Ziel"
    )
    expect(a.locator(".segment-abfolge")).to_contain_text("Kreisel 3×, Berg 2×, Wald 1×")
    expect(a.locator("input[name=sg_km_4]")).to_have_value("0,8")
    # Rundenblock, der nicht dort endet, wo er beginnt: Fehler am Reiter „Verlauf“, nichts gespeichert
    a.click("#block-neu")
    a.locator("#punktfolge > li.pf-block").last.locator("select").first.select_option("Berg")
    a.locator("#punktfolge > li.pf-block").last.locator("select").last.select_option("Kreisel")
    a.click("#alles-speichern")
    expect(a.locator("#strecke-fehler")).to_contain_text("muss dort auch enden")
    expect(a.locator("#strecke-reiter [data-reiter=verlauf]")).to_have_class(
        re.compile("reiter-fehler")
    )
    a.locator("#punktfolge > li.pf-block").last.locator(".pf-block-kopf").get_by_role(
        "button", name="entfernen"
    ).click()
    expect(a.locator("#punktfolge > li.pf-block")).to_have_count(1)
    # Auswertungsabschnitt „Runde“ = Kreisel bis nächster Kreisel, wiederkehrend (Segmente 52: ohne
    # „Vergleich“ und „anzeigen“ – die stehen unter „Öffentlichkeit & Zielmonitor“)
    a.click("#strecke-reiter [data-reiter=abschnitte]")
    a.click("#abschnitt-neu")
    neu = a.locator("#abschnitte tbody tr").last
    neu.locator("input[name^=ab_name_]").fill("Runde")
    # Anfang und Ende als Auswahl aus der Abfolge (Segmente 33): bei „jede Durchfahrt“ je Punkt
    expect(neu.locator(".ab-von option")).to_have_text(["Start", "Kreisel", "Berg", "Wald", "Ziel"])
    neu.locator(".ab-von").select_option(label="Kreisel")
    neu.locator(".ab-nach").select_option(label="Kreisel")
    expect(neu.locator(".ab-vergleich")).to_have_count(0)
    # Vorschau in Worten (Segmente 29), sofort und ohne Speichern
    expect(neu.locator(".ab-vorschau")).to_have_text("Runde: Kreisel → Berg → Kreisel, 2-mal")
    neu.locator(".ab-art").select_option(label="eine bestimmte Durchfahrt")
    # … bei „eine bestimmte Durchfahrt“ je Durchfahrt; Ende nur nach dem Anfang wählbar
    expect(neu.locator(".ab-nach option")).to_have_text([
        "Start", "Kreisel · 1. Durchfahrt (nach Start→Kreisel)", "Berg · 1. Durchfahrt",
        "Kreisel · 2. Durchfahrt (nach Runde 1)", "Berg · 2. Durchfahrt",
        "Kreisel · 3. Durchfahrt (nach Runde 2)", "Wald · 1. Durchfahrt (nach Kreisel→Wald)", "Ziel",
    ])  # fmt: skip
    expect(neu.locator(".ab-von")).to_have_value("1:Kreisel")
    expect(neu.locator(".ab-nach option[value='1:Start']")).to_be_disabled()
    expect(neu.locator(".ab-nach option[value='1:Kreisel']")).to_be_disabled()
    expect(neu.locator(".ab-nach")).to_have_class(re.compile("feld-fehler"))
    neu.locator(".ab-nach").select_option(label="Kreisel · 2. Durchfahrt (nach Runde 1)")
    expect(neu.locator(".ab-nach")).not_to_have_class(re.compile("feld-fehler"))
    expect(neu.locator(".ab-vorschau")).to_have_text(
        "Runde: Kreisel → Berg → Kreisel, nur die 1. Durchfahrt an Kreisel"
    )
    neu.locator(".ab-art").select_option(label="jede Durchfahrt")
    a.click("#alles-speichern")
    expect(a.locator(".meldung")).to_contain_text("Strecke gespeichert")
    expect(a.locator("#abschnitte input[value='Runde']")).to_be_visible()
    expect(a.locator("#abschnitte .ab-vorschau")).to_have_text(
        "Runde: Kreisel → Berg → Kreisel, 2-mal"
    )
    a.click("#strecke-reiter [data-reiter=verlauf]")
    expect(a.locator("#segmente-tabelle tbody tr")).to_have_count(5)  # je Segment ein Abschnitt
    expect(a.locator("#segmente-tabelle thead th")).to_have_text(["Segment", "Name", "Länge (km)"])
    # Spalten (Segmente 52): unter „Öffentlichkeit & Zielmonitor“ für die Strecke, ein Speichern
    einstellungen(a, "zielmonitor")
    spalte = a.locator("#spalten tr", has_text="Runde").locator("input[name=anzeigen]")
    expect(spalte).to_be_checked()
    spalte.uncheck()
    a.click("#spalten-speichern")
    expect(a.locator(".meldung")).to_contain_text("Ergebnistabelle gespeichert")
    expect(
        a.locator("#spalten tr", has_text="Runde").locator("input[name=anzeigen]")
    ).not_to_be_checked()
    # Vergleich je Monitor-Feld: „mit Pfeil und Differenz“
    a.locator(".live-feld", has_text="Kreisel→Berg").locator("input[name=live_abschnitt]").check()
    a.locator(".live-feld", has_text="Kreisel→Berg").locator("input[name=live_vergleich]").check()
    a.click("#zielmonitor-speichern")
    expect(
        a.locator(".live-feld", has_text="Kreisel→Berg").locator("input[name=live_vergleich]")
    ).to_be_checked()
    admin_seite(a, "messpunkte")
    expect(a.locator("#messpunkte tr", has_text="Strecke 1")).to_have_count(
        5
    )  # alle genutzt (mit Start und Ziel)
    admin_seite(a, "geraete")
    expect(a.locator(".geraet[data-titel='Posten Wald'] .qr svg")).to_be_visible()
    url = a.locator(".geraet[data-titel='Posten Kreisel'] a.link").get_attribute("href")
    assert url.endswith(f"/posten?token={TOKENS['posten']}&ort=1")  # Segmente 44: eigenes Profil
    ctx.close()


def test_assistent_einfach_nur_mit_bestaetigung(server, browser):
    """Segmente 30: „mit Messpunkten“ → „einfach“ fragt nach; „Abbrechen“ stellt die Wahl zurück."""
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, service_workers="block")
    a = ctx.new_page()
    a.goto(f"{server.url}/admin")
    a.fill("input[name=token]", TOKENS["admin"])
    a.click("#anmelden button")
    a.click("#veranstaltungen a")
    vid = a.url.rstrip("/").rsplit("/", 1)[1]
    a.goto(f"{server.url}/admin/einrichtung/{vid}/2")
    einfach = a.locator("input[name=strecke_art_0][value=einfach]")
    mit = a.locator("input[name=strecke_art_0][value=messpunkte]")
    expect(mit).to_be_checked()
    einfach.check()
    expect(a.locator("#bestaetigung-dialog")).to_be_visible()
    expect(a.locator("#bestaetigung-text")).to_contain_text(
        "Verlauf und Abschnitte von ‚Strecke 1‘ werden gelöscht"
    )
    a.click("#dialog-abbrechen")
    expect(mit).to_be_checked()
    expect(a.locator("input[name=strecke_art_bestaetigt_0]")).to_have_value("")
    einfach.check()
    a.click("#dialog-ok")
    expect(einfach).to_be_checked()
    expect(a.locator("input[name=strecke_art_bestaetigt_0]")).to_have_value("1")
    ctx.close()
