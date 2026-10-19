"""Migration auf Strecken: genau eine Strecke aus strecke_km, Ergebnisse unverändert (Phase 13)."""

import hashlib
import shutil
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from app import korrekturen as k
from app import saetze as sz
from app import strecken as st
from app.db import erzeuge_engine, erzeuge_sessionmaker, tabellen_anlegen
from app.models import Ereignis, Strecke, StreckeKlasse, Teilnehmer, Veranstaltung, Wertungssatz
from app.simulation import rennplan
from tests.unit.test_migration_saetze import (
    BACKUPS,
    _kurz,
    alte_datenbank,
    ergebnis_vorher,
    hat_strecken,
)


def nachher(pfad: Path) -> tuple[dict, dict]:
    """Start der App nachstellen (Sätze, Zuordnung, Strecken) und Ergebnisse berechnen."""
    engine = erzeuge_engine(str(pfad))
    tabellen_anlegen(engine)
    with erzeuge_sessionmaker(engine)() as s:
        sz.standardsaetze_anlegen(s)
        sz.veranstaltungen_zuordnen(s)
        st.migrieren(s)
        st.migrieren(s)  # zweimal: idempotent
        ergebnisse = {v.id: _kurz(k.ergebnis(s, v)) for v in s.query(Veranstaltung)}
        strecken = {
            v.id: [(x.name, x.laenge_km, len(x.klassen)) for x in st.strecken_von(s, v)]
            for v in s.query(Veranstaltung)
        }
    engine.dispose()
    return ergebnisse, strecken


@pytest.mark.parametrize("mit_wertung", [False, True], ids=["schema-vor-phase-6", "stand-phase-11"])
def test_migration_alte_datenbank(tmp_path, mit_wertung):
    pfad = tmp_path / "alt.sqlite3"
    alte_datenbank(pfad, mit_wertung)
    vorher = ergebnis_vorher(pfad)
    ergebnisse, strecken = nachher(pfad)
    assert ergebnisse == vorher
    alle = 20 + 1  # Klassen von DRA 2026 + „Offen“
    assert strecken == {1: [("20 km", 20.0, alle)] if mit_wertung else [("Strecke 1", None, alle)]}
    if mit_wertung:
        assert vorher[1][3].startswith("Deutsches Radsportabzeichen A.1")  # Wertung aktiv


def test_migration_stand_phase_12(tmp_path):
    """Datenbank nach Phase 12: Sätze zugeordnet, eingefroren, eine Länge in strecke_km."""
    pfad = tmp_path / "p12.sqlite3"
    engine = erzeuge_engine(str(pfad))
    tabellen_anlegen(engine)
    plan = rennplan(seed=3)
    with erzeuge_sessionmaker(engine)() as s:
        sz.standardsaetze_anlegen(s)
        v = Veranstaltung(name="P12", datum=date(2026, 10, 10), aktiv=True, strecke_km=19.9,
                          klassensatz_id=sz.standard_klassensatz(s).id,
                          wertungssatz_id=sz.nach_name(s, Wertungssatz, "DRA A.1 flach").id)  # fmt: skip
        s.add(v)
        s.flush()
        for f in plan.fahrer:
            s.add(Teilnehmer(veranstaltung_id=v.id, startnummer=f.startnummer, vorname=f.vorname,
                             nachname=f.nachname, geschlecht=f.geschlecht, geburtsjahr=f.geburtsjahr,
                             abzeichen=True))  # fmt: skip
        for i, (zeit, art, nr) in enumerate(plan.ereignisse()):
            if art in ("START", "ZIEL"):
                s.add(Ereignis(uuid=f"e{i}", veranstaltung_id=v.id, art=art, zeit_ms=zeit,
                               startnummer=nr, geraet="g", erstellt_ms=zeit, empfangen_ms=zeit))  # fmt: skip
        sz.einfrieren(s, v)
        s.commit()
        assert s.query(Strecke).count() == 0 and s.query(StreckeKlasse).count() == 0
        vorher = _kurz(k.ergebnis(s, v))  # Phase-12-Weg: eine Länge aus strecke_km
    engine.dispose()
    ergebnisse, strecken = nachher(pfad)
    assert ergebnisse == {1: vorher}
    assert strecken == {1: [("19,9 km", 19.9, 21)]}
    assert vorher[3] is not None  # Wertung aktiv


@pytest.mark.skipif(not BACKUPS, reason="kein Backup unter ~/rcb-backups/")
@pytest.mark.parametrize("backup", BACKUPS, ids=lambda p: p.name)
def test_migration_kopie_des_backups(tmp_path, backup):
    original_hash = hashlib.sha256(backup.read_bytes()).hexdigest()
    kopie = tmp_path / backup.name
    shutil.copy2(backup, kopie)  # nur eine KOPIE migrieren, das Original bleibt unverändert
    schon_strecken = hat_strecken(kopie)
    vorher = ergebnis_vorher(kopie)
    ergebnisse, strecken = nachher(kopie)
    assert ergebnisse == vorher
    if schon_strecken:  # Datei ab Phase 13: Strecken bleiben, wie sie sind
        assert strecken and all(liste for liste in strecken.values())
        assert hashlib.sha256(backup.read_bytes()).hexdigest() == original_hash
        return
    assert strecken and all(len(liste) == 1 for liste in strecken.values())
    con = sqlite3.connect(backup)
    laengen = dict(con.execute("SELECT id, strecke_km FROM veranstaltung")) if "strecke_km" in {
        r[1] for r in con.execute("PRAGMA table_info(veranstaltung)")} else {}  # fmt: skip
    con.close()
    for vid, liste in strecken.items():
        assert liste[0][1] == (round(laengen[vid], 1) if laengen.get(vid) else None)
    assert hashlib.sha256(backup.read_bytes()).hexdigest() == original_hash
