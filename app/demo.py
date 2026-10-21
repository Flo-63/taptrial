"""Demo-Veranstaltung „Demo-Zeitfahren Kottenforst“.

    uv run python scripts/demo.py --db data/zeitfahren.sqlite3 [--ersetzen]
    uv run python -m app.verwaltung demo [--ersetzen]          # im Container: docker compose exec app …

Legt eine abgeschlossene Veranstaltung am 04.10.2026 an – ausschließlich über die Funktionen der
App: Einrichtung wie im Assistenten (Klassen und Wertung, Messpunkte, Strecken aus Streckendateien,
Teilnehmer per CSV-Import, Zielmonitor-Vorgabe), Rennereignisse wie von Start-, Posten- und
Zielgeräten (gleiche Prüfung wie die API), Zuordnung in der Rennleitung, offiziell erklären,
abschließen. Die Uhr der App steht dabei auf dem jeweiligen Zeitpunkt am Renntag (nur die Uhr,
keine Logik ist ersetzt). Danach ist die Demo nicht aktiv; eine vorher aktive Veranstaltung ist es
wieder.
"""

import argparse
import json
import random
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import api, saetze, strecken, umgebung
from app import korrekturen as k
from app import monitor_konfig as mk
from app import segmente as sg
from app import strecken_datei as sd
from app.auth import geraet_zugang
from app.einstellungen import STANDARD_KLASSEN_DATEI, STANDARD_WERTUNGEN
from app.models import Klassensatz, Veranstaltung, Wertungssatz, aktive_veranstaltung
from app.teilnehmer_import import ImportFehler, importiere_teilnehmer

NAME = "Demo-Zeitfahren Kottenforst"
DATUM = date(2026, 10, 4)
WERTUNG = "DRA A.1 flach"
ZEITZONE = ZoneInfo("Europe/Berlin")
MIN = 60_000

STRECKE_20 = """version = 1
name = "20 km"
laenge_km = 20
beschreibung = "Demo: Start 20k → P1, zwei Runden P1 → P2 → P1, P1 → Ziel"
startort = "Start 20k"
zielort = "Ziel"
messpunkte = ["P1", "P2"]

[[teil]]
name = ""
wdh = 1
[[teil.segment]]
name = "Start→P1"
von = "Start"
nach = "P1"
km = 2

[[teil]]
name = "Runde"
wdh = 2
[[teil.segment]]
name = "P1→P2"
von = "P1"
nach = "P2"
km = 4
[[teil.segment]]
name = "P2→P1"
von = "P2"
nach = "P1"
km = 4

[[teil]]
name = ""
wdh = 1
[[teil.segment]]
name = "P1→Ziel"
von = "P1"
nach = "Ziel"
km = 2
"""

STRECKE_5 = """version = 1
name = "5 km"
laenge_km = 5
beschreibung = "Demo: Jugendstrecke Start Jugend → Ziel"
startort = "Start Jugend"
zielort = "Ziel"
messpunkte = []
"""


@dataclass(frozen=True)
class Fahrer:
    nr: int
    vorname: str
    nachname: str
    geschlecht: str
    jahrgang: int
    kmh: float | None  # None = DNS
    verein: str = ""
    abzeichen: bool = True
    jugend: bool = False


# Startreihenfolge = Startnummer; Legenden schnell (Cancellara am schnellsten), Comics gemütlicher
FAHRER = (
    Fahrer(1, "Eddy", "Merckx", "m", 1971, 40.5),
    Fahrer(2, "Fausto", "Coppi", "m", 1985, 40.0),
    Fahrer(3, "Bernard", "Hinault", "m", 1964, 39.5),
    Fahrer(4, "Miguel", "Induráin", "m", 1990, 41.0),
    Fahrer(5, "Fabian", "Cancellara", "m", 2001, 41.8),
    Fahrer(6, "Jens", "Voigt", "m", 1953, 38.0, abzeichen=False),
    Fahrer(7, "Jeannie", "Longo", "w", 1958, 37.5),
    Fahrer(8, "Marianne", "Vos", "w", 1994, 39.0),
    Fahrer(9, "Donald", "Duck", "m", 1999, 28.5, "RV Entenhausen"),
    Fahrer(10, "Daisy", "Duck", "w", 1979, 31.0, "RV Entenhausen"),
    Fahrer(11, "Asterix", "Gallier", "m", 1997, 34.0, "Gallisches Dorf"),
    Fahrer(12, "Obelix", "Gallier", "m", 1982, 29.0, "Gallisches Dorf"),
    Fahrer(13, "Lucky", "Luke", "m", 1968, None, "Daisy Town"),
    Fahrer(14, "Tick", "Duck", "m", 2012, 28.5, "RV Entenhausen", jugend=True),
    Fahrer(15, "Lisa", "Simpson", "w", 2010, 30.0, "Springfield", jugend=True),
)
VERGESSEN_P2 = 9  # Donald: vergessener Klick an P2 (zweite Runde)
DOPPELTIPP_P1 = 10  # Daisy: Doppeltipp an P1 (erste Durchfahrt)
OHNE_NUMMER = 11  # Asterix: Zielzeit ohne Nummer, in der Rennleitung zugeordnet
DNF_NACH_RUNDE = 12  # Obelix: DNF nach der ersten Runde
DNS = 13  # Lucky Luke


class DemoFehler(Exception):
    pass


def _ms(h: int, m: int, s: int = 0) -> int:
    return int(
        datetime(DATUM.year, DATUM.month, DATUM.day, h, m, s, tzinfo=ZEITZONE).timestamp() * 1000
    )


@contextmanager
def uhr(ms: int):
    """Die Uhr der App (Empfangszeit, Korrekturen, Einfrieren) steht auf `ms`."""
    module = (api, k, saetze)
    alt = [m.jetzt_ms for m in module]
    for m in module:
        m.jetzt_ms = lambda: ms
    try:
        yield
    finally:
        for m, f in zip(module, alt, strict=True):
            m.jetzt_ms = f


def _teilnehmer_csv() -> bytes:
    zeilen = [
        "startnummer;vorname;nachname;geschlecht;geburtsjahr;verein;abzeichen;ausser_konkurrenz"
    ]
    zeilen += [f"{f.nr};{f.vorname};{f.nachname};{f.geschlecht};{f.jahrgang};{f.verein};"
               f"{'ja' if f.abzeichen else ''};" for f in FAHRER]  # fmt: skip
    return ("\n".join(zeilen) + "\n").encode()


def _einrichten(session: Session, ks: Klassensatz, ws: Wertungssatz) -> Veranstaltung:
    """Wie der Assistent: 1 Name/Datum · 2 Klassen & Wertung, Messpunkte, Strecken je Platz ·
    3 Teilnehmer · 4 Zielmonitor (Vorgabe) · 5 Abschluss."""
    v = k.veranstaltung_anlegen(session, NAME, DATUM, aktivieren=False, klassensatz_id=ks.id)
    v.einrichtung_schritt = 2
    v.einrichtung_json = json.dumps(
        {"plaetze": {}, "vorlaeufig": strecken.strecken_von(session, v)[0].id}
    )
    session.commit()
    saetze.regeln_setzen(session, v, ks.id, ws.id, None)
    start, ziel = (strecken.standard_ort(session, v, a) for a in ("start", "ziel"))
    strecken.orte_speichern(session, v, [{"id": start.id, "name": "Start 20k"},
                                         {"id": ziel.id, "name": "Ziel"}])  # fmt: skip
    neu = {}
    for text in (STRECKE_5, STRECKE_20):
        entwurf = sd.lesen(text.encode())
        neu[entwurf.name] = sd.uebernehmen(session, v, entwurf, entwurf.name, set())
    for platz in strecken.plaetze(session, v):
        strecken.platz_entscheiden(session, v, platz.schluessel, neu[platz.name])
    session.commit()
    if strecken.plaetze_offen(session, v):
        raise DemoFehler("Die Strecken passen nicht zu den Plätzen des Wertungssatzes.")
    strecken.wertung_laengen_pruefen(session, v, weiter=True)
    v.einrichtung_schritt = 3
    try:
        importiere_teilnehmer(session, v, _teilnehmer_csv(), saetze.regeln(session, v).klassen)
    except ImportFehler as e:
        raise DemoFehler("Teilnehmer-Import abgelehnt: " + "; ".join(e.fehler)) from e
    v.einrichtung_schritt = 5
    v.monitor_json = json.dumps(mk.vorschlag(strecken.strecken_von(session, v)), ensure_ascii=False)
    v.einrichtung_schritt = None
    session.commit()
    return v


def _ereignis(art: str, zeit: int, geraet: str, **felder) -> dict:
    return {"uuid": str(uuid.uuid4()), "art": art, "erstellt_ms": zeit, "geraet": geraet,
            "zeit_ms": zeit if art in ("START", "ZIEL", "DURCHFAHRT") else None, **felder}  # fmt: skip


def _senden(session: Session, token: str, ereignisse: list[dict]) -> None:
    """Ein Gerät überträgt seinen Stapel (Prüfung wie POST /api/ereignisse)."""
    if not ereignisse:
        return
    zugang = geraet_zugang(session, token)
    with uhr(max(e["erstellt_ms"] for e in ereignisse) + 3_000):
        try:
            api.ereignisse_annehmen(session, zugang, api.Batch(ereignisse=ereignisse))
        except HTTPException as e:
            raise DemoFehler(f"Gerät abgelehnt: {e.detail}") from e


def _rennen(session: Session, v: Veranstaltung) -> None:
    orte = {m.name: m for m in strecken.orte_von(session, v)}
    p1, p2, jugend = orte["P1"], orte["P2"], orte["Start Jugend"]
    s20 = next(s for s in strecken.strecken_von(session, v) if s.name == "20 km")
    folge = sg.abfolge(strecken.teile_von(s20))
    km = [d.segment.km for d in folge]
    punkte = [d.segment.nach for d in folge]
    geraete = {"start": [], "jugend": [], "p1": [], "p2": [], "ziel": [], "rennleitung": []}
    for f in FAHRER:
        zufall = random.Random(68_000 + f.nr)
        slot = _ms(10, 15 + f.nr - 14) if f.jugend else _ms(10, f.nr - 1)
        if f.kmh is None:
            geraete["start"].append(
                _ereignis("STATUS", slot + 20_000, "start-demo", startnummer=f.nr, wert="DNS")
            )
            continue
        t = slot + zufall.randint(0, 400)
        geraete["jugend" if f.jugend else "start"].append(
            _ereignis(
                "START", t, "start-jugend-demo" if f.jugend else "start-demo", startnummer=f.nr
            )
        )
        if f.jugend:
            ziel_ms = t + round(5 / f.kmh * 3_600_000 * zufall.uniform(0.99, 1.01))
            geraete["ziel"].append(_ereignis("ZIEL", ziel_ms, "ziel-demo", startnummer=f.nr))
            continue
        # Segmentzeiten nach Länge, je Segment ±3 %, im Mittel genau das Tempo des Fahrers
        abw = [zufall.uniform(-0.015, 0.015) for _ in km]
        mittel = sum(a * x for a, x in zip(abw, km, strict=True)) / sum(km)
        for j, (x, a, punkt) in enumerate(zip(km, abw, punkte, strict=True)):
            t += round(x / f.kmh * 3_600_000 * (1 + a - mittel))
            if punkt == sg.ZIEL:
                if f.nr == OHNE_NUMMER:
                    ohne = _ereignis("ZIEL", t, "ziel-demo")
                    geraete["ziel"].append(ohne)
                    geraete["rennleitung"].append(_ereignis("ZUORDNUNG", t + 40_000, "ziel-demo",
                                                            bezug_uuid=ohne["uuid"], startnummer=f.nr))  # fmt: skip
                else:
                    geraete["ziel"].append(_ereignis("ZIEL", t, "ziel-demo", startnummer=f.nr))
                break
            if f.nr == VERGESSEN_P2 and j == 3:
                continue
            posten = "p1" if punkt == str(p1.id) else "p2"
            geraete[posten].append(
                _ereignis("DURCHFAHRT", t, f"posten-{posten}-demo", startnummer=f.nr, wert=punkt)
            )
            if f.nr == DOPPELTIPP_P1 and j == 0:
                geraete["p1"].append(
                    _ereignis(
                        "DURCHFAHRT", t + 1_800, "posten-p1-demo", startnummer=f.nr, wert=punkt
                    )
                )
            if f.nr == DNF_NACH_RUNDE and j == 2:
                geraete["rennleitung"].append(_ereignis("STATUS", t + 15 * MIN, "ziel-demo", startnummer=f.nr,
                                                        wert="DNF", notiz="Nach der ersten Runde ausgestiegen"))  # fmt: skip
                break
    _senden(session, v.token_start, geraete["start"])
    _senden(session, jugend.token, geraete["jugend"])
    _senden(session, p1.token, geraete["p1"])
    _senden(session, p2.token, geraete["p2"])
    _senden(session, v.token_ziel, geraete["ziel"])
    _senden(session, v.token_ziel, geraete["rennleitung"])  # Rennleitung auf dem Zielgerät


def erzeugen(session: Session, ersetzen: bool = False, klassen_datei: str = STANDARD_KLASSEN_DATEI,
             wertungen_verzeichnis: str = STANDARD_WERTUNGEN) -> Veranstaltung:  # fmt: skip
    """Legt die Demo an (DemoFehler: nichts angelegt). Mit ersetzen wird eine vorhandene Demo
    vorher über die normale Löschfunktion entfernt."""
    saetze.standardsaetze_anlegen(
        session, klassen_datei, wertungen_verzeichnis
    )  # wie der erste App-Start
    ws = saetze.nach_name(session, Wertungssatz, WERTUNG)
    if ws is None:
        raise DemoFehler(f"Wertungssatz „{WERTUNG}“ (DRA-Vorlage) fehlt – Demo nicht angelegt.")
    ks = session.get(Klassensatz, ws.klassensatz_id)
    alte = [x for x in session.scalars(select(Veranstaltung)) if x.name == NAME]
    if alte and not ersetzen:
        raise DemoFehler(f"„{NAME}“ gibt es schon (Veranstaltung {alte[0].id}) – nichts geändert. "
                         "Mit --ersetzen wird sie vorher gelöscht.")  # fmt: skip
    laufend = k.laufende_andere(session)
    if laufend is not None and laufend.name != NAME:
        raise DemoFehler(f"{k.verdraengen_text(laufend)} Demo nicht angelegt.")
    for alt in alte:
        k.loeschen(session, alt)
    vorher = aktive_veranstaltung(session)
    v = None
    try:
        with uhr(_ms(9, 0)):
            v = _einrichten(session, ks, ws)
            k.aktiv_setzen(session, v)
        _rennen(session, v)
        with uhr(_ms(11, 30)):
            gruende = k.offiziell_sperrgruende(k.ergebnis(session, v))
            if gruende:
                raise DemoFehler("Ergebnis nicht offiziell: " + "; ".join(gruende))
            k.ergebnis_offiziell(session, v, "Ergebnis offiziell erklärt")
            k.abschliessen(session, v)
    except Exception as e:  # halbe Demo wieder entfernen, vorher aktive bleibt aktiv
        session.rollback()
        if v is not None:
            k.loeschen(session, session.get(Veranstaltung, v.id))
        if vorher is not None:
            k.aktiv_wiederherstellen(session, vorher)
        if isinstance(e, (DemoFehler, k.KorrekturFehler, saetze.SatzFehler)):
            raise DemoFehler(f"{e} – Demo nicht angelegt.") from e
        raise
    if vorher is not None:
        k.aktiv_wiederherstellen(session, vorher)  # die bisher aktive bleibt aktiv, wie sie war
    else:
        k.aktiv_beenden(session, v)
    return v


def main(argv: list[str] | None = None, db_pfad: str | None = None) -> int:
    from app.db import erzeuge_engine, erzeuge_sessionmaker, tabellen_anlegen

    parser = argparse.ArgumentParser(description="Demo-Veranstaltung anlegen")
    if db_pfad is None:
        parser.add_argument("--db", default=umgebung.wert("db_pfad", "data/zeitfahren.sqlite3"))
    parser.add_argument("--ersetzen", action="store_true", help="vorhandene Demo vorher löschen")
    args = parser.parse_args(argv)
    pfad = db_pfad or args.db
    engine = erzeuge_engine(pfad)
    tabellen_anlegen(engine)
    try:
        with erzeuge_sessionmaker(engine)() as session:
            try:
                v = erzeugen(session, args.ersetzen,
                             umgebung.wert("klassen_datei", STANDARD_KLASSEN_DATEI),
                             umgebung.wert("wertungen_verzeichnis", STANDARD_WERTUNGEN))  # fmt: skip
            except DemoFehler as e:
                session.rollback()
                print(e, file=sys.stderr)
                return 1
            print(f"Veranstaltung {v.id} „{v.name}“ am {v.datum:%d.%m.%Y} angelegt: "
                  f"{len(FAHRER)} Teilnehmer, Ergebnis offiziell, abgeschlossen, nicht aktiv. "
                  f"Entfernen: Admin → Veranstaltung → „Status & Datenschutz“ → „Komplett löschen“.")  # fmt: skip
            print(f"Datenbank: {pfad}")
            return 0
    finally:
        engine.dispose()
