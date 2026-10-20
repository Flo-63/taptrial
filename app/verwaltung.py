"""Kommandozeile für Einrichtung und Betrieb ohne Admin-Oberfläche.

uv run python -m app.verwaltung veranstaltung "Zeitfahren Kottenforst" 2026-10-10
uv run python -m app.verwaltung import teilnehmer.csv [--ersetzen]
uv run python -m app.verwaltung links https://zeitfahren.example.org
uv run python -m app.verwaltung demo [--ersetzen]
"""

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import update

from app import saetze, strecken, umgebung
from app.db import erzeuge_engine, erzeuge_sessionmaker, tabellen_anlegen
from app.einstellungen import STANDARD_KLASSEN_DATEI, STANDARD_WERTUNGEN
from app.models import Veranstaltung, Wertungssatz, aktive_veranstaltung
from app.teilnehmer_import import ImportFehler, importiere_teilnehmer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.verwaltung")
    befehle = parser.add_subparsers(dest="befehl", required=True)
    p = befehle.add_parser("veranstaltung", help="Veranstaltung anlegen und aktiv setzen")
    p.add_argument("name")
    p.add_argument("datum", type=date.fromisoformat, help="JJJJ-MM-TT")
    p = befehle.add_parser("import", help="Teilnehmer-CSV in die aktive Veranstaltung laden")
    p.add_argument("datei", help="Pfad zur CSV-Datei oder - für stdin")
    p.add_argument("--ersetzen", action="store_true", help="vorhandene Teilnehmer vorher löschen")
    p = befehle.add_parser("wertung", help="Leistungswertung der aktiven Veranstaltung setzen")
    p.add_argument("datei", help="Name des Wertungssatzes (z. B. „DRA A.1 flach“) oder „aus“")
    p.add_argument("strecke_km", nargs="?", help="gemessene Streckenlänge, z. B. 20,0")
    p.add_argument("--laengen-verwerfen", action="store_true",
                   help="ohne Wirkung (Strecken werden nicht mehr neu vorgeschlagen)")  # fmt: skip
    p = befehle.add_parser("backup", help="konsistente Kopie der SQLite-Datei (auch im Betrieb)")
    p.add_argument("ziel", help="Pfad der Sicherungsdatei (darf noch nicht existieren)")
    p = befehle.add_parser("links", help="Gerätelinks mit Tokens ausgeben")
    p.add_argument("basis_url")
    befehle.add_parser("demo", add_help=False,
                       help="Demo-Veranstaltung anlegen, --ersetzen löscht eine vorhandene")  # fmt: skip
    args, rest = parser.parse_known_args(argv)
    if args.befehl == "demo":
        from app import demo

        return demo.main(rest, db_pfad=umgebung.wert("db_pfad", "data/zeitfahren.sqlite3"))
    if rest:
        parser.error(f"unbekannte Argumente: {' '.join(rest)}")

    # Für Veranstaltung/Import reichen DB-Pfad und Klassen-Datei, Tokens sind nicht nötig.
    db_pfad = umgebung.wert("db_pfad", "data/zeitfahren.sqlite3")
    klassen_datei = umgebung.wert("klassen_datei", STANDARD_KLASSEN_DATEI)
    wertungen_verzeichnis = umgebung.wert("wertungen_verzeichnis", STANDARD_WERTUNGEN)
    if args.befehl == "backup":
        return _backup(db_pfad, args.ziel)
    engine = erzeuge_engine(db_pfad)
    tabellen_anlegen(engine)
    try:
        with erzeuge_sessionmaker(engine)() as session:
            if args.befehl == "veranstaltung":
                from app import korrekturen

                laufend = korrekturen.laufende_andere(session)
                if laufend is not None:
                    print(korrekturen.verdraengen_text(laufend), file=sys.stderr)
                    return 1
                saetze.standardsaetze_anlegen(session, klassen_datei, wertungen_verzeichnis)
                session.execute(update(Veranstaltung).values(aktiv=False))
                session.flush()
                v = Veranstaltung(name=args.name, datum=args.datum, aktiv=True,
                                  klassensatz_id=saetze.standard_klassensatz(session).id)  # fmt: skip
                session.add(v)
                session.flush()
                strecken.sicherstellen(session, v)
                session.commit()
                print(f"Veranstaltung {v.id} „{v.name}“ am {v.datum:%d.%m.%Y} angelegt (aktiv)")
                return 0

            v = aktive_veranstaltung(session)
            if v is None and args.befehl == "links":
                print("Keine aktive Veranstaltung.", file=sys.stderr)
                return 1
            if args.befehl == "links":
                basis = args.basis_url.rstrip("/")
                print(f"Veranstaltung: {v.name} ({v.datum:%d.%m.%Y})")
                print(f"Start: {basis}/start?token={v.token_start}")
                print(f"Ziel:  {basis}/ziel?token={v.token_ziel}")
                if v.rennleitung_getrennt:
                    print(f"Rennleitung: {basis}/rennleitung?token={v.token_rennleitung}")
                else:
                    print(f"Rennleitung: {basis}/ziel?token={v.token_ziel}&sicht=rennleitung")
                admin = umgebung.wert("token_admin")
                if admin:
                    print(f"Admin: {basis}/admin?token={admin}")
                return 0
            if v is None:
                print(
                    "Keine aktive Veranstaltung. Zuerst „veranstaltung“ ausführen.", file=sys.stderr
                )
                return 1
            if args.befehl == "wertung":
                return _wertung(session, v, args)
            if args.datei == "-":
                inhalt = sys.stdin.buffer.read()
            else:
                with open(args.datei, "rb") as f:
                    inhalt = f.read()
            klassen = saetze.regeln(session, v).klassen
            try:
                n = importiere_teilnehmer(session, v, inhalt, klassen, ersetzen=args.ersetzen)
            except ImportFehler as e:
                print("Import abgebrochen, nichts übernommen:", file=sys.stderr)
                for zeile in e.fehler:
                    print(f"  {zeile}", file=sys.stderr)
                return 1
            print(f"{n} Teilnehmer in „{v.name}“ importiert")
            return 0
    finally:
        engine.dispose()


def _wertung(session, v, args) -> int:
    """Wertungssatz der aktiven Veranstaltung setzen: Name des Satzes (oder bisherige Datei) bzw. „aus“."""
    vorhanden = saetze.wertungssaetze(session)
    ws = None
    if args.datei != "aus":
        name = saetze.WERTUNG_NAMEN.get(args.datei, args.datei)
        ws = saetze.nach_name(session, Wertungssatz, name)
        if ws is None:
            print(f"Wertungssatz „{args.datei}“ gibt es nicht.", file=sys.stderr)
            print("Verfügbar: " + ", ".join(w.name for w in vorhanden), file=sys.stderr)
            return 1
    try:
        saetze.regeln_setzen(session, v, v.klassensatz_id or saetze.standard_klassensatz(session).id,
                             ws.id if ws else None, saetze.strecke_parsen(args.strecke_km),
                             laengen_verwerfen=args.laengen_verwerfen)  # fmt: skip
    except saetze.SatzFehler as e:
        print(e, file=sys.stderr)
        return 1
    liste = ", ".join(f"{s.name} ({strecken.km_text(s.laenge_km) or '–'} km)"
                      for s in strecken.strecken_von(session, v))  # fmt: skip
    print(f"Leistungswertung: {ws.name}; Strecken: {liste}" if ws else "Leistungswertung aus")
    return 0


def _backup(db_pfad: str, ziel: str) -> int:
    """SQLite-Online-Backup: konsistent auch während die App schreibt (WAL)."""
    if Path(ziel).exists():
        print(f"{ziel} existiert bereits, nichts überschrieben.", file=sys.stderr)
        return 1
    if not Path(db_pfad).exists():
        print(f"Datenbank {db_pfad} nicht gefunden.", file=sys.stderr)
        return 1
    Path(ziel).parent.mkdir(parents=True, exist_ok=True)
    quelle = sqlite3.connect(db_pfad)
    kopie = sqlite3.connect(ziel)
    try:
        quelle.backup(kopie)
    finally:
        kopie.close()
        quelle.close()
    print(f"Sicherung geschrieben: {ziel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
