"""Füllt eine lokale Instanz mit einem simulierten Rennen (für Gate C ohne Handys).

    uv run python scripts/simulieren.py                 # DB aus RCB_DB_PFAD bzw. data/zeitfahren.sqlite3
    uv run python scripts/simulieren.py --db /tmp/sim.sqlite3 --seed 7 --ohne-wertung

Legt eine neue, aktive Veranstaltung „Simulation …“ mit 50 Fahrern an und schreibt das Rennen
aus app/simulation.py als Ereignisse in die Datenbank (wie Start- und Zielgerät es tun würden):
zufällige Startabstände, 3 Überholungen, zwei fast gleichzeitige Zieleinläufe (per „Zeit ohne
Nummer“ und Zuordnung), 1 DNS, 1 DNF. Zwei Strecken wie der Vorschlag aus DRA A.1: „5 km“
(Jugendklassen, fünf Jugendliche) und „20 km“ (alle anderen). Zusätzlich ein vorläufiger DSQ und –
wenn nicht abgeschaltet – die Leistungswertung A.1. Danach Server starten und ansehen:

    RCB_TOKEN_ADMIN=admin uv run uvicorn app.main:app
    http://localhost:8000/ergebnisse   ·   /monitor   ·   /admin (Admin-Token „admin“)
"""

import argparse
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import update

from app import saetze, strecken
from app.db import erzeuge_engine, erzeuge_sessionmaker, tabellen_anlegen
from app.models import Ereignis, Teilnehmer, Veranstaltung, Wertungssatz
from app.simulation import rennplan

WERTUNG = "DRA A.1 flach"
DSQ_VORLAEUFIG_NR = 9


def ereignis(v, art, zeit, geraet, **felder) -> Ereignis:
    return Ereignis(uuid=str(uuid.uuid4()), veranstaltung_id=v.id, art=art, geraet=geraet,
                    erstellt_ms=zeit, empfangen_ms=zeit + 2_000, **felder)  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=os.environ.get("RCB_DB_PFAD") or "data/zeitfahren.sqlite3")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--ohne-wertung", action="store_true")
    args = parser.parse_args(argv)

    plan = rennplan(seed=args.seed)
    engine = erzeuge_engine(args.db)
    tabellen_anlegen(engine)
    with erzeuge_sessionmaker(engine)() as s:
        s.execute(update(Veranstaltung).values(aktiv=False))
        v = Veranstaltung(
            name=f"Simulation (Seed {args.seed})",
            datum=plan.datum,
            aktiv=True,
        )
        saetze.standardsaetze_anlegen(s)
        v.klassensatz_id = saetze.standard_klassensatz(s).id
        a1 = saetze.nach_name(s, Wertungssatz, WERTUNG)
        if not args.ohne_wertung:
            v.wertungssatz_id = a1.id
        s.add(v)
        s.flush()
        # zwei Strecken (5 km Jugend, 20 km Erwachsene) wie der Vorschlag aus DRA A.1
        klassen = saetze.klassen_von(saetze.standard_klassensatz(s))
        strecken.anlegen(s, v, strecken.vorschlag(klassen, saetze.wertung_von(a1)))
        for f in plan.fahrer:
            s.add(Teilnehmer(veranstaltung_id=v.id, startnummer=f.startnummer, vorname=f.vorname,
                             nachname=f.nachname, geschlecht=f.geschlecht,
                             geburtsjahr=f.geburtsjahr, verein=f.verein, abzeichen=f.abzeichen,
                             ausser_konkurrenz=f.ausser_konkurrenz))  # fmt: skip
        ohne_nummer: dict[int, Ereignis] = {}
        for zeit, art, nr in plan.ereignisse():
            if art == "START":
                s.add(ereignis(v, "START", zeit, "start-sim", startnummer=nr, zeit_ms=zeit))
            elif art == "DNS":
                s.add(ereignis(v, "STATUS", zeit, "start-sim", startnummer=nr, wert="DNS"))
            elif art == "ZIEL":
                s.add(ereignis(v, "ZIEL", zeit, "ziel-sim", startnummer=nr, zeit_ms=zeit))
            elif art == "ZIEL_OHNE_NUMMER":
                e = ereignis(v, "ZIEL", zeit, "ziel-sim", zeit_ms=zeit)
                s.add(e)
                ohne_nummer[nr] = e
            elif art == "DNF":
                s.add(ereignis(v, "STATUS", zeit, "ziel-sim", startnummer=nr, wert="DNF"))
        letzte = max(plan.ziel_ms.values())
        for i, (nr, e) in enumerate(ohne_nummer.items(), start=1):
            s.add(
                ereignis(v, "ZUORDNUNG", letzte + i, "ziel-sim", bezug_uuid=e.uuid, startnummer=nr)
            )
        s.add(ereignis(v, "STATUS", letzte + 10, "ziel-sim", startnummer=DSQ_VORLAEUFIG_NR,
                       wert="DSQ_VORLAEUFIG", notiz="Simulation: Windschatten gefahren"))  # fmt: skip
        s.flush()
        saetze.einfrieren(s, v)  # wie beim ersten Ereignis über die API
        s.commit()
        print(f"Veranstaltung {v.id} „{v.name}“ angelegt und aktiviert: {len(plan.fahrer)} Fahrer, "
              f"DNS Nr. {plan.dns}, DNF Nr. {plan.dnf}, vorläufiger DSQ Nr. {DSQ_VORLAEUFIG_NR}, "
              f"Strecken 5 km und 20 km, Leistungswertung {'aus' if args.ohne_wertung else 'A.1'}.")  # fmt: skip
        print(f"Datenbank: {args.db}")
    engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
