"""S 5: Versionierte, atomare Migrationen; ohne Append-only-Trigger kein Start."""

import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db, migrationen, saetze, strecken
from app import korrekturen as k
from app.db import erzeuge_engine, erzeuge_sessionmaker, tabellen_anlegen
from app.einstellungen import Einstellungen
from app.main import create_app
from app.migrationen import AKTUELL, SCHEMA, MigrationsFehler, migrieren
from app.models import Veranstaltung
from tests.unit.test_migration_saetze import BACKUPS, _kurz, alte_datenbank, ergebnis_vorher


def stand(pfad: Path) -> tuple[int, set[str], set[str], set[str]]:
    """(Version, Tabellen, Trigger, Spalten von ereignis) direkt aus der Datei."""
    con = sqlite3.connect(pfad)
    try:
        return (
            con.execute("PRAGMA user_version").fetchone()[0],
            {z[0] for z in con.execute("SELECT name FROM sqlite_master WHERE type='table'")},
            {z[0] for z in con.execute("SELECT name FROM sqlite_master WHERE type='trigger'")},
            {z[1] for z in con.execute("PRAGMA table_info(ereignis)")},
        )
    finally:
        con.close()


TRIGGER = set(migrationen.TRIGGER)


@pytest.fixture
def engine_von():
    engines = []

    def machen(pfad):
        engines.append(erzeuge_engine(str(pfad)))
        return engines[-1]

    yield machen
    for e in engines:
        e.dispose()


def test_neue_datenbank_steht_auf_dem_aktuellen_stand(tmp_path, engine_von):
    pfad = tmp_path / "neu.sqlite3"
    migrieren(engine_von(pfad))
    version, tabellen, trigger, spalten = stand(pfad)
    assert version == AKTUELL and "ereignis" in tabellen and TRIGGER <= trigger
    assert "zugang" in spalten
    with erzeuge_sessionmaker(engine_von(pfad))() as s:
        assert saetze.standard_klassensatz(s) is not None


def test_tabellen_anlegen_reicht_bis_zum_schema(tmp_path, engine_von):
    pfad = tmp_path / "t.sqlite3"
    tabellen_anlegen(engine_von(pfad))
    assert stand(pfad)[0] == SCHEMA


def test_daten_nachzug_laeuft_nur_einmal(tmp_path, engine_von, monkeypatch):
    pfad = tmp_path / "d.sqlite3"
    aufrufe = []
    echt = strecken.migrieren
    monkeypatch.setattr(strecken, "migrieren", lambda s: (aufrufe.append(1), echt(s)))
    migrieren(engine_von(pfad))
    migrieren(engine_von(pfad))
    assert aufrufe == [1]


def _abbruch(*_a, **_k):
    raise RuntimeError("Strom weg")


@pytest.mark.parametrize("mit_wertung", [False, True], ids=["vor-phase-6", "phase-11"])
def test_abbruch_im_schema_laesst_die_datei_unveraendert(
    tmp_path, engine_von, monkeypatch, mit_wertung
):
    pfad = tmp_path / "alt.sqlite3"
    alte_datenbank(pfad, mit_wertung)
    vorher = stand(pfad)
    echt = db.schema_nachruesten

    def bis_zum_ende_dann_abbruch(conn):
        echt(conn)
        _abbruch()

    monkeypatch.setattr(db, "schema_nachruesten", bis_zum_ende_dann_abbruch)
    with pytest.raises(MigrationsFehler, match=r"Migration 1 .*abgebrochen.*Strom weg"):
        migrieren(engine_von(pfad))
    assert (
        stand(pfad) == vorher
    )  # auch neu angelegte Tabellen, Spalten und Trigger sind zurückgerollt
    monkeypatch.undo()
    erwartet = ergebnis_vorher(pfad)
    migrieren(engine_von(pfad))
    assert stand(pfad)[0] == AKTUELL and TRIGGER <= stand(pfad)[2]
    with erzeuge_sessionmaker(engine_von(pfad))() as s:
        assert {v.id: _kurz(k.ergebnis(s, v)) for v in s.query(Veranstaltung)} == erwartet


def test_abbruch_im_daten_schritt_bleibt_auf_dem_stand_davor(tmp_path, engine_von, monkeypatch):
    pfad = tmp_path / "alt.sqlite3"
    alte_datenbank(pfad, True)
    monkeypatch.setattr(strecken, "migrieren", _abbruch)
    with pytest.raises(MigrationsFehler, match="Migration 2"):
        migrieren(engine_von(pfad))
    assert stand(pfad)[0] == SCHEMA and TRIGGER <= stand(pfad)[2]
    con = sqlite3.connect(pfad)  # die Sätze aus demselben Schritt sind auch zurückgerollt
    assert con.execute("SELECT COUNT(*) FROM klassensatz").fetchone()[0] == 0
    con.close()
    monkeypatch.undo()
    migrieren(engine_von(pfad))
    assert stand(pfad)[0] == AKTUELL


@pytest.mark.skipif(not BACKUPS, reason="kein Backup unter ~/rcb-backups/")
@pytest.mark.parametrize("backup", BACKUPS[:1] + BACKUPS[-1:], ids=lambda p: p.name)
def test_abbruch_mit_kopie_einer_echten_sicherung(tmp_path, engine_von, monkeypatch, backup):
    pfad = tmp_path / backup.name
    shutil.copy2(backup, pfad)
    for rest in ("-wal", "-shm"):
        (Path(str(pfad) + rest)).unlink(missing_ok=True)
    vorher = stand(pfad)
    echt = db.schema_nachruesten

    def abbruch(conn):
        echt(conn)
        _abbruch()

    monkeypatch.setattr(db, "schema_nachruesten", abbruch)
    with pytest.raises(MigrationsFehler):
        migrieren(engine_von(pfad))
    assert stand(pfad) == vorher
    monkeypatch.undo()
    migrieren(engine_von(pfad))
    assert stand(pfad)[0] == AKTUELL and TRIGGER <= stand(pfad)[2]


def test_verlorener_trigger_einer_alten_datei_wird_wiederhergestellt(tmp_path, engine_von):
    pfad = tmp_path / "alt.sqlite3"
    alte_datenbank(pfad, True)
    con = sqlite3.connect(pfad)
    con.execute("DROP TRIGGER IF EXISTS ereignis_kein_update")
    con.commit()
    con.close()
    migrieren(engine_von(pfad))
    assert TRIGGER <= stand(pfad)[2]


@pytest.mark.parametrize("trigger", sorted(TRIGGER))
def test_ohne_trigger_startet_die_app_nicht(tmp_path, trigger, caplog):
    pfad = tmp_path / "t.sqlite3"
    einstellungen = Einstellungen.fuer_tests(db_pfad=str(pfad))
    with TestClient(create_app(einstellungen)):
        pass
    con = sqlite3.connect(pfad)
    con.execute(f"DROP TRIGGER {trigger}")
    con.commit()
    con.close()
    with (
        pytest.raises(MigrationsFehler, match=f"Schutz der Ereignisse.*{trigger}.*App startet"),
        TestClient(create_app(einstellungen)),
    ):
        pass
    assert "Schutz der Ereignisse" in caplog.text


def test_trigger_fehlt_nach_dem_schritt_rollt_zurueck(tmp_path, engine_von, monkeypatch):
    pfad = tmp_path / "t.sqlite3"
    monkeypatch.setattr(migrationen, "_trigger_anlegen", lambda _c: None)
    alte_datenbank(pfad, True)
    with pytest.raises(MigrationsFehler, match="Schutz der Ereignisse"):
        migrieren(engine_von(pfad))
    assert stand(pfad)[0] == 0


def test_neuere_datenbank_startet_trotzdem_mit_warnung(tmp_path, engine_von, caplog):
    """Ein Rollback auf eine ältere Version muss starten können."""
    pfad = tmp_path / "t.sqlite3"
    migrieren(engine_von(pfad))
    con = sqlite3.connect(pfad)
    con.execute(f"PRAGMA user_version = {AKTUELL + 5}")
    con.commit()
    con.close()
    migrieren(engine_von(pfad))
    assert "neuer" in caplog.text and stand(pfad)[0] == AKTUELL + 5


def test_append_only_bleibt_wirksam(tmp_path, engine_von):
    pfad = tmp_path / "t.sqlite3"
    migrieren(engine_von(pfad))
    con = sqlite3.connect(pfad)
    con.execute("PRAGMA foreign_keys=OFF")
    con.execute("INSERT INTO ereignis (uuid, veranstaltung_id, art, geraet, erstellt_ms, empfangen_ms) "
                "VALUES ('u', 1, 'START', 'g', 1, 1)")  # fmt: skip
    con.commit()
    for befehl in ("UPDATE ereignis SET art = 'ZIEL'", "DELETE FROM ereignis"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            con.execute(befehl)
    con.close()
