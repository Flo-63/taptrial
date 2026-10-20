"""Erzeugt die Screenshots für die Handbücher aus einem simulierten Rennen.

    uv run python scripts/screenshots.py                 # schreibt nach docs/handbuch/bilder/
    uv run python scripts/screenshots.py --ziel /tmp/bilder

Startet einen eigenen Server mit frischer Datenbank (Rennplan aus app/simulation.py, Stand
mitten im Rennen), bedient Start, Ziel, Rennleitung, Monitor, Ergebnisse und Admin mit Playwright
und speichert die Bilder. Geräteseiten im Handyformat, Admin/Monitor im Querformat.
Die Geräteuhr wird auf einen Zeitpunkt im Rennen gesetzt, damit Uhrzeiten und laufende Zeiten passen.
"""

import argparse
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import date, datetime
from pathlib import Path

PROJEKT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJEKT))

import httpx
from playwright.sync_api import sync_playwright

from app import saetze, segmente, strecken
from app.db import erzeuge_engine, erzeuge_sessionmaker, tabellen_anlegen
from app.klassen import klassifizieren
from app.models import Ereignis, Messpunkt, Teilnehmer, Veranstaltung, Wertungssatz
from app.simulation import BEISPIEL_MESSPUNKTE, ZEITZONE, beispiel_teile, rennplan, rundkurs

TOKEN_START, TOKEN_ZIEL, TOKEN_ADMIN = "handbuch-start", "handbuch-ziel", "handbuch-admin"
TOKEN_POSTEN = "handbuch-posten"  # Messpunkt „Kreisel“
HANDY = "Pixel 7"
BILDER = [
    "start.png", "ziel-erfassen.png", "rennleitung.png", "rennleitung-zuordnen.png",
    "rennleitung-status.png", "geraet-ende.png", "monitor.png", "monitor-hochkant.png",
    "ergebnisse.png", "startseite.png",
    "anmelden.png",
    "admin-uebersicht.png", "admin-v-uebersicht.png", "admin-stammdaten.png", "admin-klassen.png",
    "admin-teilnehmer.png", "admin-import.png", "admin-geraete.png", "admin-rennen.png",
    "admin-ergebnis.png", "admin-saetze.png", "admin-klassensatz.png", "admin-wertungssatz.png",
    "admin-einrichtung.png", "posten.png",
]  # fmt: skip


def _freier_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def daten_anlegen(db: str, ziele_bis: int = 6) -> int:
    """Rennen bis kurz nach dem n-ten Zieleinlauf (Standard 6: noch nicht alle gestartet);
    gibt „Jetzt“ (ms) zurück. Für den Zielmonitor ein späterer Stand (viele Klassen gefüllt)."""
    plan = rennplan()
    ziele = sorted(plan.ziel_ms.values())
    jetzt = ziele[ziele_bis - 1] + 20_000
    engine = erzeuge_engine(db)
    tabellen_anlegen(engine)
    with erzeuge_sessionmaker(engine)() as s:
        saetze.standardsaetze_anlegen(s)
        v = Veranstaltung(name="Zeitfahren Kottenforst", datum=plan.datum, aktiv=True,
                          token_start=TOKEN_START, token_ziel=TOKEN_ZIEL,
                          klassensatz_id=saetze.standard_klassensatz(s).id,
                          wertungssatz_id=saetze.nach_name(s, Wertungssatz, "DRA A.1 flach").id)  # fmt: skip
        s.add(v)
        s.flush()
        # zwei Strecken wie der Vorschlag aus DRA A.1, mit gemessenen Längen
        vorschlag = strecken.vorschlag(saetze.regeln(s, v).klassen, saetze.regeln(s, v).wertung)
        for x, km in zip(vorschlag, (5.1, 20.2), strict=True):
            x.laenge_km = km
        strecken.anlegen(s, v, vorschlag)
        # Segmente: die 20-km-Strecke als Beispielstrecke mit Messpunkten Kreisel und Berg
        lang = strecken.strecken_von(s, v)[1]
        mps = [Messpunkt(veranstaltung_id=v.id, position=i, name=name, **({"token": TOKEN_POSTEN} if i == 0 else {}))
               for i, (_, name) in enumerate(BEISPIEL_MESSPUNKTE)]  # fmt: skip
        s.add_all(mps)
        s.flush()
        mp_id = {k: str(m.id) for (k, _), m in zip(BEISPIEL_MESSPUNKTE, mps, strict=True)}
        teile = beispiel_teile(mp_id)
        lang.teile_json = segmente.nach_json(teile)
        # Abschnitt „Runde“ = Kreisel bis nächster Kreisel und Monitor mit Live-Feld dazu
        import json

        lang.abschnitte_json = json.dumps({"segmente": {}, "eigene": [
            {"id": "a1", "name": "Runde", "von": mp_id["P1"], "nach": mp_id["P1"], "art": "wiederkehrend",
             "vergleich": True}]})  # fmt: skip
        s.flush()
        v.monitor_json = json.dumps({"seiten": [{"typ": "ergebnis", "an": True, "dauer": 10, "live": True,
                                                  "abzeichen": True, "strecken": []}],
                                     "live_abschnitte": [f"{lang.id}:a1"]})  # fmt: skip
        stichtag = date(plan.datum.year, 12, 31)
        klassen = saetze.regeln(s, v).klassen
        auf_lang = {f.startnummer for f in plan.fahrer
                    if any(sk.klasse == klassifizieren(f, stichtag, klassen) for sk in lang.klassen)}  # fmt: skip
        for f in plan.fahrer:
            s.add(Teilnehmer(veranstaltung_id=v.id, startnummer=f.startnummer, vorname=f.vorname,
                             nachname=f.nachname, geschlecht=f.geschlecht,
                             geburtsjahr=f.geburtsjahr, verein=f.verein, abzeichen=f.abzeichen,
                             ausser_konkurrenz=f.ausser_konkurrenz))  # fmt: skip

        def e(art, zeit, geraet, **felder):
            s.add(Ereignis(uuid=str(uuid.uuid4()), veranstaltung_id=v.id, art=art, geraet=geraet,
                           erstellt_ms=zeit, empfangen_ms=zeit, **felder))  # fmt: skip

        for zeit, art, nr in plan.ereignisse():
            if zeit > jetzt:
                continue
            if art == "START":
                e("START", zeit, "start-1a2b", startnummer=nr, zeit_ms=zeit)
            elif art == "DNS":
                e("STATUS", zeit, "start-1a2b", startnummer=nr, wert="DNS")
            elif art in ("ZIEL", "ZIEL_OHNE_NUMMER"):
                e("ZIEL", zeit, "ziel-3c4d", startnummer=nr, zeit_ms=zeit)
        for d in rundkurs(plan, auf_lang, seed=3, teile=teile):
            if d.zeit_ms <= jetzt:
                e("DURCHFAHRT", d.zeit_ms, f"posten-{d.ort}", startnummer=d.startnummer,
                  zeit_ms=d.zeit_ms, wert=d.ort)  # fmt: skip
        # eine offene Zeit (Fahrer nicht erkannt) und ein vorläufiger DSQ
        e("ZIEL", jetzt - 8_000, "ziel-3c4d", zeit_ms=jetzt - 8_000)
        erster = min(plan.ziel_ms, key=plan.ziel_ms.get)
        e("STATUS", jetzt - 5_000, "ziel-3c4d", startnummer=erster, wert="DSQ_VORLAEUFIG",
          notiz="Windschatten gefahren")  # fmt: skip
        s.commit()
    engine.dispose()
    return jetzt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ziel", default=str(PROJEKT / "docs" / "handbuch" / "bilder"))
    args = parser.parse_args(argv)
    ziel = Path(args.ziel)
    ziel.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "handbuch.sqlite3")
        jetzt = daten_anlegen(db)
        _mit_server(db, jetzt, lambda browser, p, url, uhrzeit: _aufnehmen(
            browser, p.devices[HANDY], url, uhrzeit, ziel))  # fmt: skip
        # Zielmonitor mit späterem Stand (40 Zieleinläufe, viele Klassen): quer und hochkant
        db_monitor = str(Path(tmp) / "monitor.sqlite3")
        jetzt = daten_anlegen(db_monitor, ziele_bis=40)
        _mit_server(db_monitor, jetzt, lambda browser, p, url, uhrzeit: _monitor_aufnehmen(
            browser, url, uhrzeit, ziel))  # fmt: skip
    for name in BILDER:
        print(ziel / name)
    return 0


def _monitor_aufnehmen(browser, url, uhrzeit, ziel: Path) -> None:
    for name, groesse in (("monitor.png", (1366, 768)), ("monitor-hochkant.png", (768, 1024))):
        ctx = browser.new_context(viewport={"width": groesse[0], "height": groesse[1]})
        m = ctx.new_page()
        m.clock.set_fixed_time(uhrzeit)
        m.goto(url + "/monitor")
        m.wait_for_selector("#mon-klassen tr[data-nr]")
        m.wait_for_timeout(300)
        m.screenshot(path=str(ziel / name))
        ctx.close()


def _mit_server(db: str, jetzt: int, aufnehmen) -> None:
    """Server mit der Datenbank starten, `aufnehmen(browser, playwright, url, uhrzeit)` ausführen."""
    uhrzeit = datetime.fromtimestamp(jetzt / 1000, ZEITZONE)
    port = _freier_port()
    env = {**os.environ, "TAPTRIAL_DB_PFAD": db, "TAPTRIAL_TOKEN_ADMIN": TOKEN_ADMIN}
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
        cwd=PROJEKT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    url = f"http://localhost:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(f"{url}/up").status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            aufnehmen(browser, p, url, uhrzeit)
            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=5)


def _aufnehmen(browser, handy, url, uhrzeit, ziel: Path) -> None:
    def handyseite(pfad):
        ctx = browser.new_context(**handy)
        page = ctx.new_page()
        page.clock.set_fixed_time(uhrzeit)
        page.goto(url + pfad)
        page.wait_for_function("window.geraetBereit === true")
        page.wait_for_timeout(600)
        # feste Bilduhr ≠ Serveruhr: Uhrprüfung für die Bilder auf „±0,1 s“ setzen
        page.evaluate(
            "window.geraet.uhrMessungen = [{laufzeit: -1, abweichung: 100}];"
            "window.geraet.zeigeStatus()"
        )
        return ctx, page

    def foto(page, name, ganz=False):
        page.wait_for_timeout(300)
        page.screenshot(path=str(ziel / name), full_page=ganz)

    # Start
    ctx, start = handyseite(f"/start?token={TOKEN_START}")
    foto(start, "start.png")
    ctx.close()

    # Posten am Messpunkt
    ctx, posten = handyseite(f"/posten?token={TOKEN_POSTEN}")
    foto(posten, "posten.png")
    ctx.close()

    # Ziel: Erfassen und Rennleitung
    ctx, z = handyseite(f"/ziel?token={TOKEN_ZIEL}")
    foto(z, "ziel-erfassen.png")
    z.click("#tab-rennleitung")
    z.click("#rl-tab-letzte")
    z.locator("#letzte .letzte-text").first.click()  # eine Zeile aufgeklappt
    z.evaluate("window.scrollTo(0, 0)")
    foto(z, "rennleitung.png")
    z.locator("#letzte .letzte-text").first.click()
    z.click("#rl-tab-offen")
    z.locator("#offene-zeiten button").first.click()
    foto(z, "rennleitung-zuordnen.png")
    z.click("#btn-zuordnen-abbrechen")
    z.click("#rl-tab-status")
    z.locator("#status-auswahl button").nth(25).click()
    z.evaluate("document.getElementById('status-box').scrollIntoView({block: 'center'})")
    foto(z, "rennleitung-status.png")
    z.click("#btn-status-abbrechen")
    z.click("#btn-zuruecksetzen")
    z.click("#btn-zuruecksetzen")  # zeigt die Meldung nach dem Aufräumen
    z.evaluate("window.scrollTo(0, 0)")
    foto(z, "geraet-ende.png")
    ctx.close()

    # Monitor und Ergebnisse
    ctx = browser.new_context(viewport={"width": 1600, "height": 900})
    m = ctx.new_page()
    m.clock.set_fixed_time(uhrzeit)
    m.set_viewport_size({"width": 1100, "height": 900})
    m.goto(url + "/ergebnisse")
    foto(m, "ergebnisse.png")
    ctx.close()

    # Startseite im Handyformat
    ctx = browser.new_context(**handy)
    s = ctx.new_page()
    s.goto(url + "/")
    foto(s, "startseite.png")
    ctx.close()

    # Admin
    ctx = browser.new_context(viewport={"width": 1280, "height": 860})
    a = ctx.new_page()
    a.clock.set_fixed_time(uhrzeit)
    a.goto(url + "/admin")
    foto(a, "anmelden.png")
    a.fill("input[name=token]", TOKEN_ADMIN)
    a.click("#anmelden button")
    foto(a, "admin-uebersicht.png")
    a.click("#veranstaltungen a")
    foto(a, "admin-v-uebersicht.png")
    for seite, name in (("einstellungen", "admin-einstellungen.png"), ("teilnehmer", "admin-teilnehmer.png"),
                        ("geraete", "admin-geraete.png"), ("aufgaben", "admin-aufgaben.png"),
                        ("protokoll", "admin-protokoll.png"), ("ergebnis", "admin-ergebnis.png"),
                        ("drucken", "admin-drucken.png"), ("status", "admin-status.png")):  # fmt: skip
        a.click(f".sl-nav a[data-seite={seite}]")
        foto(a, name)
    # Strecken, Messpunkte und die Seite der langen Strecke
    a.click(".sl-nav a[data-seite=einstellungen]")
    a.click("#einstellungen-reiter a[data-reiter=zielmonitor]")
    foto(a, "admin-zielmonitor.png")
    a.click(".sl-nav a[data-seite=messpunkte]")
    foto(a, "admin-messpunkte.png")
    a.click(".sl-nav a[data-seite=strecken]")
    foto(a, "admin-strecken.png")
    a.click("#strecke-oeffnen-1")
    a.click("#strecke-reiter [data-reiter=verlauf]")
    a.wait_for_selector("#streckenverlauf")
    foto(a, "admin-strecke.png")
    # Import-Vorschau mit Fehlern und Warnung, danach verwerfen
    a.click(".sl-nav a[data-seite=teilnehmer]")
    a.locator("#teilnehmer tr.row").nth(3).click()  # Fahrer-Seite
    a.wait_for_selector("#zeiten")
    foto(a, "admin-fahrer.png")
    a.click("#zurueck-teilnehmer")
    a.click("#csv-importieren")
    foto(a, "admin-import-start.png")
    csv = ("startnummer;vorname;nachname;geschlecht;geburtsjahr;verein\n"
           "101;Lena;Hoffmann;w;1988;RC Bonn\n102;Paul;Weber;x;1975;\n"
           "103;Lena;Hoffmann;w;1990;\n104;;Schulz;m;2001;RSV Kottenforst\n")  # fmt: skip
    a.set_input_files("#import input[type=file]",
                      files=[{"name": "nachmeldungen.csv", "mimeType": "text/csv",
                              "buffer": csv.encode()}])  # fmt: skip
    a.click("#import button")
    a.wait_for_selector("#import-tabelle")
    foto(a, "admin-import.png")
    a.click("#verwerfen button")
    a.click("#nav-vorlagen")
    foto(a, "admin-saetze.png")
    a.click("#klassensaetze a:has-text('DRA 2026')")
    foto(a, "admin-klassensatz.png")
    a.click(".sl-nav a[data-seite=wertung]")
    a.click("#wertungssaetze a:has-text('DRA A.1 flach')")
    foto(a, "admin-wertungssatz.png")
    # Assistent: neue Veranstaltung, Schritt 2
    a.click("#nav-veranstaltungen")
    a.click("#neue-veranstaltung")
    a.fill("#schritt input[name=name]", "Zeitfahren Frühjahr")
    a.fill("#schritt input[name=datum]", "2027-04-17")
    a.click("#weiter")
    a.select_option("#wahl-wertungssatz", label="DRA A.1 flach")  # Je Längen-Platz entscheiden
    a.wait_for_selector(".platz[data-platz='5 km']")
    foto(a, "admin-einrichtung.png")
    ctx.close()


if __name__ == "__main__":
    sys.exit(main())
