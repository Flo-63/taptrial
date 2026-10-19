"""scripts/simulieren.py füllt eine Datenbank so, dass das Ergebnis den Sollwerten entspricht."""

import subprocess
import sys
from pathlib import Path

from sqlalchemy import select

from app import korrekturen as k
from app.db import erzeuge_engine, erzeuge_sessionmaker
from app.einstellungen import STANDARD_KLASSEN_DATEI
from app.klassen import lade_klassen
from app.models import Veranstaltung
from app.simulation import rennplan, sollergebnis, strecken_dra_a1

PROJEKT = Path(__file__).resolve().parents[2]


def test_skript_erzeugt_auswertbares_rennen(tmp_path):
    db = tmp_path / "sim.sqlite3"
    r = subprocess.run([sys.executable, "scripts/simulieren.py", "--db", str(db)], cwd=PROJEKT,
                       capture_output=True, text=True, check=False)  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert "50 Fahrer" in r.stdout
    klassen = lade_klassen(STANDARD_KLASSEN_DATEI)
    engine = erzeuge_engine(str(db))
    with erzeuge_sessionmaker(engine)() as s:
        v = s.scalars(select(Veranstaltung).where(Veranstaltung.aktiv.is_(True))).one()
        erg = k.ergebnis(s, v)
        assert v.regeln_json is not None  # eingefroren
    engine.dispose()
    soll = sollergebnis(rennplan(), klassen, strecken_dra_a1(klassen))
    assert erg.konflikte == [] and erg.offene_ziele == []  # Plausibilität je Strecke
    assert [s.name for s in erg.strecken] == ["5 km", "20 km"]
    for nr, sz in soll.items():
        z = erg.zeile(nr)
        assert (z.status, z.fahrzeit_ms, z.rang_gesamt, z.rang_klasse, erg.strecken[z.strecke].name) == (
            sz.status, sz.fahrzeit_ms, sz.rang_gesamt, sz.rang_klasse, sz.strecke)  # fmt: skip
    assert [z.startnummer for z in erg.vorlaeufige_dsq] == [9]
    assert erg.wertung is not None and any(z.stufe for z in erg.zeilen)


def test_skript_zweimal_legt_neue_aktive_veranstaltung_an(tmp_path):
    db = tmp_path / "sim.sqlite3"
    for seed in ("1", "2"):
        subprocess.run([sys.executable, "scripts/simulieren.py", "--db", str(db), "--seed", seed,
                        "--ohne-wertung"], cwd=PROJEKT, check=True, capture_output=True)  # fmt: skip
    engine = erzeuge_engine(str(db))
    with erzeuge_sessionmaker(engine)() as s:
        alle = s.scalars(select(Veranstaltung).order_by(Veranstaltung.id)).all()
        assert [(x.name, x.aktiv, x.wertungssatz_id) for x in alle] == [
            ("Simulation (Seed 1)", False, None),
            ("Simulation (Seed 2)", True, None),
        ]
    engine.dispose()
