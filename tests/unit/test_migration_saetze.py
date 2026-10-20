"""Migration zu Klassen-/Wertungssätzen: Ergebnisse vorher und nachher identisch."""

import hashlib
import shutil
import sqlite3
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import korrekturen as k
from app import saetze as sz
from app import umgebung
from app.auswertung import AuswertungsConfig, auswerten
from app.db import erzeuge_engine, erzeuge_sessionmaker, tabellen_anlegen
from app.einstellungen import STANDARD_KLASSEN_DATEI, STANDARD_WERTUNGEN
from app.klassen import lade_klassen
from app.models import Veranstaltung
from app.simulation import rennplan
from app.wertung import Stufe, Wertung, lade_wertungen, zehntel_km

# Backups des echten Rennens (nur lokal); im Release-Export zeigt TAPTRIAL_BACKUP_VERZEICHNIS ins Leere
BACKUP_VERZEICHNIS = Path(umgebung.wert("backup_verzeichnis") or Path.home() / "rcb-backups")
BACKUPS = sorted(BACKUP_VERZEICHNIS.glob("backup-*.sqlite3"))


def hat_strecken(pfad: Path) -> bool:
    con = sqlite3.connect(f"file:{pfad}?mode=ro", uri=True)
    try:
        tabellen = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return (
            "strecke" in tabellen and con.execute("SELECT COUNT(*) FROM strecke").fetchone()[0] > 0
        )
    finally:
        con.close()


def ergebnis_ohne_migration(pfad: Path) -> dict:
    """Datei schon im Schema mit mehreren Strecken: Referenz = heutige Auswertung ohne Migrationsschritte
    (auf einer eigenen Kopie)."""
    kopie = pfad.with_name(pfad.stem + "-referenz.sqlite3")
    shutil.copy2(pfad, kopie)
    engine = erzeuge_engine(str(kopie))
    tabellen_anlegen(engine)
    with erzeuge_sessionmaker(engine)() as s:
        ergebnisse = {v.id: _kurz(k.ergebnis(s, v)) for v in s.query(Veranstaltung)}
    engine.dispose()
    return ergebnisse


def ergebnis_vorher(pfad: Path) -> dict:
    """Ergebnisse wie bisher (vor den Klassen- und Wertungssätzen): Klassen/Wertung aus den Dateien in config/."""
    if hat_strecken(pfad):
        return ergebnis_ohne_migration(pfad)
    klassen = lade_klassen(STANDARD_KLASSEN_DATEI)
    wertungen = lade_wertungen(STANDARD_WERTUNGEN, klassen)
    con = sqlite3.connect(pfad)
    con.row_factory = sqlite3.Row
    ergebnisse = {}
    for v in con.execute("SELECT * FROM veranstaltung"):
        v = dict(v)
        tn = [SimpleNamespace(**{"abzeichen": False, "ausser_konkurrenz": False, "klasse_fest": None,
                                 **dict(t)})
              for t in con.execute("SELECT * FROM teilnehmer WHERE veranstaltung_id = ?", (v["id"],))]  # fmt: skip
        ev = [SimpleNamespace(**dict(e))
              for e in con.execute("SELECT * FROM ereignis WHERE veranstaltung_id = ?", (v["id"],))]  # fmt: skip
        wertung = (
            wertungen.get(v.get("wertung")) if v.get("wertung") and v.get("strecke_km") else None
        )
        if v.get("wertungssatz_id") and v.get("strecke_km"):  # Datei im Schema mit Sätzen
            wertung = _wertung_aus_db(con, v["wertungssatz_id"])
        config = AuswertungsConfig(
            klassen=klassen, stichtag=date.fromisoformat(v["stichtag_alter"]), wertung=wertung,
            strecke_zehntel_km=zehntel_km(v["strecke_km"]) if wertung else None,
        )  # fmt: skip
        ergebnisse[v["id"]] = _kurz(auswerten(tn, ev, config))
    con.close()
    return ergebnisse


def _wertung_aus_db(con, wid: int) -> Wertung:
    """Wertungssatz direkt aus den Tabellen (Schema mit Sätzen), unabhängig von app.saetze."""
    w = con.execute("SELECT * FROM wertungssatz WHERE id = ?", (wid,)).fetchone()
    stufen = {
        st["klasse"]: Stufe(st["klasse"], st["gold_ms"], st["silber_ms"], st["bronze_ms"],
                            zehntel_km(st["strecke_km"]) if st["strecke_km"] else None)
        for st in con.execute("SELECT * FROM wertungssatz_stufe WHERE wertungssatz_id = ?", (wid,))
    }  # fmt: skip
    return Wertung(w["name"], w["beschreibung"] or w["name"], w["art"], stufen,
                   zehntel_km(w["strecke_km_min"]) if w["strecke_km_min"] else None,
                   zehntel_km(w["strecke_km_max"]) if w["strecke_km_max"] else None)  # fmt: skip


def ergebnis_nachher(pfad: Path) -> dict:
    engine = erzeuge_engine(str(pfad))
    tabellen_anlegen(engine)
    with erzeuge_sessionmaker(engine)() as s:
        sz.standardsaetze_anlegen(s)
        sz.veranstaltungen_zuordnen(s)
        ergebnisse = {v.id: _kurz(k.ergebnis(s, v)) for v in s.query(Veranstaltung)}
        eingefroren = {v.id: v.regeln_json is not None for v in s.query(Veranstaltung)}
    engine.dispose()
    return ergebnisse, eingefroren


def _kurz(erg) -> tuple:
    zeilen = tuple(
        (z.startnummer, z.status.value, z.fahrzeit_ms, z.klasse, z.rang_gesamt, z.rang_klasse, z.stufe,
         z.dsq_vorlaeufig)
        for z in erg.zeilen
    )  # fmt: skip
    return (
        zeilen,
        len(erg.konflikte),
        erg.offiziell_seit_ms,
        erg.wertung.name if erg.wertung else None,
    )


def alte_datenbank(pfad: Path, mit_wertung: bool) -> None:
    """Datei im Schema vor der Admin-Oberfläche (wie das Backup) bzw. mit Wertungsspalten."""
    plan = rennplan(seed=5)
    con = sqlite3.connect(pfad)
    extra = ", wertung VARCHAR(100), strecke_km FLOAT" if mit_wertung else ""
    con.executescript(f"""
        CREATE TABLE veranstaltung (id INTEGER PRIMARY KEY, name VARCHAR(200) NOT NULL,
            datum DATE NOT NULL, stichtag_alter DATE NOT NULL, aktiv BOOLEAN NOT NULL{extra});
        CREATE TABLE teilnehmer (id INTEGER PRIMARY KEY, veranstaltung_id INTEGER NOT NULL,
            startnummer INTEGER NOT NULL, vorname VARCHAR(100) NOT NULL, nachname VARCHAR(100) NOT NULL,
            geschlecht VARCHAR(1) NOT NULL, geburtsjahr INTEGER NOT NULL, verein VARCHAR(200));
        CREATE TABLE ereignis (uuid VARCHAR(64) PRIMARY KEY, veranstaltung_id INTEGER NOT NULL,
            art VARCHAR(20) NOT NULL, zeit_ms BIGINT, startnummer INTEGER, bezug_uuid VARCHAR(64),
            wert VARCHAR(20), geraet VARCHAR(50) NOT NULL, erstellt_ms BIGINT NOT NULL,
            empfangen_ms BIGINT NOT NULL, notiz VARCHAR(500));
    """)  # fmt: skip
    if mit_wertung:
        con.execute("INSERT INTO veranstaltung VALUES (1, 'Alt', '2026-10-10', '2026-12-31', 1, "
                    "'radsportabzeichen-a1-flach.toml', 20.0)")  # fmt: skip
    else:
        con.execute("INSERT INTO veranstaltung VALUES (1, 'Alt', '2026-10-10', '2026-12-31', 1)")
    for f in plan.fahrer:
        con.execute("INSERT INTO teilnehmer VALUES (?, 1, ?, ?, ?, ?, ?, ?)",
                    (f.startnummer, f.startnummer, f.vorname, f.nachname, f.geschlecht, f.geburtsjahr, f.verein))  # fmt: skip
    n = 0
    for zeit, art, nr in plan.ereignisse():
        if art in ("START", "ZIEL", "ZIEL_OHNE_NUMMER"):
            n += 1
            con.execute("INSERT INTO ereignis VALUES (?, 1, ?, ?, ?, NULL, NULL, 'g', ?, ?, NULL)",
                        (f"e{n}", "START" if art == "START" else "ZIEL", zeit, nr, zeit, zeit))  # fmt: skip
        elif art in ("DNS", "DNF"):
            n += 1
            con.execute("INSERT INTO ereignis VALUES (?, 1, 'STATUS', NULL, ?, NULL, ?, 'g', ?, ?, NULL)",
                        (f"e{n}", nr, art, zeit, zeit))  # fmt: skip
    con.commit()
    con.close()


@pytest.mark.parametrize("mit_wertung", [False, True], ids=["schema-vor-phase-6", "stand-phase-11"])
def test_migration_ergebnisse_unveraendert(tmp_path, mit_wertung):
    pfad = tmp_path / "alt.sqlite3"
    alte_datenbank(pfad, mit_wertung)
    vorher = ergebnis_vorher(pfad)
    nachher, eingefroren = ergebnis_nachher(pfad)
    assert nachher == vorher
    assert eingefroren == {1: True}
    if mit_wertung:  # die Stufen sind wirklich berechnet worden (sonst wäre der Vergleich zahnlos)
        assert vorher[1][3].startswith("Deutsches Radsportabzeichen A.1")
    assert any(z[2] for z in vorher[1][0])  # es gibt Fahrzeiten


@pytest.mark.skipif(not BACKUPS, reason="kein Backup unter ~/rcb-backups/")
@pytest.mark.parametrize("backup", BACKUPS, ids=lambda p: p.name)
def test_migration_kopie_des_backups(tmp_path, backup):
    original_hash = hashlib.sha256(backup.read_bytes()).hexdigest()
    kopie = tmp_path / backup.name
    shutil.copy2(backup, kopie)  # nur eine KOPIE migrieren, das Original bleibt unverändert
    con = sqlite3.connect(kopie)
    mit_ereignissen = {r[0] for r in con.execute("SELECT DISTINCT veranstaltung_id FROM ereignis")}
    con.close()
    vorher = ergebnis_vorher(kopie)
    nachher, eingefroren = ergebnis_nachher(kopie)
    assert nachher == vorher
    assert {vid for vid, ja in eingefroren.items() if ja} == mit_ereignissen
    assert hashlib.sha256(backup.read_bytes()).hexdigest() == original_hash
    assert vorher and all(isinstance(v, tuple) for v in vorher.values())
