from pathlib import Path

import pytest
from sqlalchemy import select

from app.db import erzeuge_engine, erzeuge_sessionmaker
from app.models import Teilnehmer, Veranstaltung
from app.verwaltung import main

FIXTURE = Path(__file__).resolve().parents[2] / "config" / "teilnehmer_beispiel.csv"


@pytest.fixture
def db(tmp_path, monkeypatch):
    pfad = str(tmp_path / "v.sqlite3")
    monkeypatch.setenv("RCB_DB_PFAD", pfad)
    monkeypatch.setenv("RCB_TOKEN_ADMIN", "a1")
    return pfad


def _alle(db, modell):
    engine = erzeuge_engine(db)
    with erzeuge_sessionmaker(engine)() as s:
        ergebnis = s.scalars(select(modell)).all()
    engine.dispose()
    return ergebnis


def test_veranstaltung_anlegen_macht_sie_aktiv(db, capsys):
    assert main(["veranstaltung", "Probe", "2026-09-01"]) == 0
    assert main(["veranstaltung", "Kottenforst", "2026-10-10"]) == 0
    v = {x.name: x for x in _alle(db, Veranstaltung)}
    assert v["Kottenforst"].aktiv and not v["Probe"].aktiv
    assert "aktiv" in capsys.readouterr().out


def test_import_in_aktive_veranstaltung(db, capsys):
    main(["veranstaltung", "Kottenforst", "2026-10-10"])
    assert main(["import", str(FIXTURE)]) == 0
    assert len(_alle(db, Teilnehmer)) == 50
    assert main(["import", str(FIXTURE)]) == 1  # Startnummern bereits vergeben
    assert "bereits vergeben" in capsys.readouterr().err
    assert main(["import", str(FIXTURE), "--ersetzen"]) == 0
    assert len(_alle(db, Teilnehmer)) == 50


def test_import_ohne_veranstaltung(db, capsys):
    assert main(["import", str(FIXTURE)]) == 1
    assert "Keine aktive Veranstaltung" in capsys.readouterr().err


def test_links_aus_der_db(db, capsys):
    main(["veranstaltung", "Kottenforst", "2026-10-10"])
    v = _alle(db, Veranstaltung)[0]
    capsys.readouterr()
    assert main(["links", "https://zeitfahren.example.org/"]) == 0
    out = capsys.readouterr().out
    assert f"https://zeitfahren.example.org/start?token={v.token_start}" in out
    assert f"https://zeitfahren.example.org/ziel?token={v.token_ziel}" in out
    assert f"https://zeitfahren.example.org/ziel?token={v.token_ziel}&sicht=rennleitung" in out
    assert "https://zeitfahren.example.org/admin?token=a1" in out
    assert v.token_start != v.token_ziel and len(v.token_start) >= 24


def test_links_ohne_veranstaltung(db, capsys):
    assert main(["links", "https://x"]) == 1
    assert "Keine aktive Veranstaltung" in capsys.readouterr().err


def test_veranstaltung_ohne_tokens_moeglich(db, monkeypatch):
    for name in ("RCB_TOKEN_ADMIN",):
        monkeypatch.delenv(name)
    assert main(["veranstaltung", "Probe", "2026-09-01"]) == 0


def test_backup_ist_konsistente_kopie(db, tmp_path, capsys):
    import sqlite3

    main(["veranstaltung", "Kottenforst", "2026-10-10"])
    main(["import", str(FIXTURE)])
    ziel = tmp_path / "sicherung" / "backup.sqlite3"
    assert main(["backup", str(ziel)]) == 0
    assert str(ziel) in capsys.readouterr().out
    con = sqlite3.connect(ziel)
    assert con.execute("SELECT count(*) FROM teilnehmer").fetchone()[0] == 50
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.close()


def test_backup_ueberschreibt_nicht(db, tmp_path, capsys):
    main(["veranstaltung", "Kottenforst", "2026-10-10"])
    ziel = tmp_path / "b.sqlite3"
    ziel.write_text("alt")
    assert main(["backup", str(ziel)]) == 1
    assert ziel.read_text() == "alt"


def test_wertung_setzen_und_ausschalten(db, capsys):
    main(["veranstaltung", "Kottenforst", "2026-10-10"])
    assert main(["wertung", "radsportabzeichen-a1-flach.toml", "20,0"]) == 0
    v = _alle(db, Veranstaltung)[0]
    assert v.wertungssatz_id is not None  # bisheriger Dateiname geht weiter
    # Segmente 49: keine Strecken still angelegt – die eine Strecke bekommt die Länge
    assert "Strecken: Strecke 1 (20,0 km)" in capsys.readouterr().out
    assert main(["wertung", "DRA A.2 Berg Straße", "4,0"]) == 0
    assert "Strecken: Strecke 1 (4,0 km)" in capsys.readouterr().out
    assert main(["wertung", "aus"]) == 0  # „Strecke 1“ bleibt „Strecke 1“: Länge übernommen
    capsys.readouterr()
    assert main(["wertung", "DRA A.2 Berg Straße"]) == 0  # gemessene 4,0 km der „Strecke 1“ bleiben
    assert "Strecken: Strecke 1 (4,0 km)" in capsys.readouterr().out
    assert main(["wertung", "gibtsnicht.toml", "20"]) == 1
    assert main(["wertung", "aus"]) == 0
    assert _alle(db, Veranstaltung)[0].wertungssatz_id is None


def test_cli_verdraengt_keine_laufende_veranstaltung(db, capsys):
    """Segmente 55: „veranstaltung“ legt keine neue aktive an, solange die aktive Startzeiten hat."""
    main(["veranstaltung", "Kottenforst", "2026-10-10"])
    from app.db import erzeuge_engine, erzeuge_sessionmaker
    from app.models import Ereignis

    with erzeuge_sessionmaker(erzeuge_engine(db))() as s:
        v = s.query(Veranstaltung).one()
        s.add(Ereignis(uuid="s1", veranstaltung_id=v.id, art="START", zeit_ms=1, startnummer=1,
                       geraet="g", erstellt_ms=1, empfangen_ms=1))  # fmt: skip
        s.commit()
    capsys.readouterr()
    assert main(["veranstaltung", "Herbst", "2026-11-01"]) == 1
    assert "läuft (Startzeiten vorhanden). Erst abschließen." in capsys.readouterr().err
    assert [x.name for x in _alle(db, Veranstaltung)] == ["Kottenforst"]
