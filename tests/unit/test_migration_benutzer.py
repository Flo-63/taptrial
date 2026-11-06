"""Migration 3: Verein, Benutzer, Rollen, Protokoll der Admin-Aktionen."""

import sqlite3
from datetime import date

from sqlalchemy import select

from app.db import erzeuge_engine, erzeuge_sessionmaker, tabellen_anlegen
from app.migrationen import AKTUELL, migrieren
from app.models import Veranstaltung, Verein
from tests.unit.test_migration_saetze import _kurz, alte_datenbank, ergebnis_vorher
from tests.unit.test_migrationen import stand

NEU = {"verein", "benutzer", "rollenzuweisung", "admin_aktion"}


def _spalten(pfad, tabelle):
    con = sqlite3.connect(pfad)
    try:
        return {z[1] for z in con.execute(f"PRAGMA table_info({tabelle})")}
    finally:
        con.close()


def test_aktuell_ist_drei():
    assert AKTUELL == 3


def test_alte_datei_bekommt_verein_und_benutzer(tmp_path):
    pfad = tmp_path / "alt.sqlite3"
    alte_datenbank(pfad, True)
    engine = erzeuge_engine(str(pfad))
    migrieren(engine, bis=2)
    con = sqlite3.connect(pfad)
    con.execute("INSERT INTO app_einstellung VALUES ('vereinsname', 'RC Beispiel')")
    con.commit()
    con.close()
    vorher = ergebnis_vorher(pfad)
    migrieren(engine)
    version, tabellen, _, spalten = stand(pfad)
    assert version == 3 and NEU <= tabellen and "benutzer" in spalten
    assert "verein_id" in _spalten(pfad, "veranstaltung")
    with erzeuge_sessionmaker(engine)() as s:
        vereine = s.scalars(select(Verein)).all()
        assert [x.name for x in vereine] == ["RC Beispiel"]
        assert {v.verein_id for v in s.scalars(select(Veranstaltung))} == {vereine[0].id}
        from app import korrekturen as k

        assert {v.id: _kurz(k.ergebnis(s, v)) for v in s.scalars(select(Veranstaltung))} == vorher
    engine.dispose()


def test_ohne_vereinsnamen_heisst_der_verein_verein(tmp_path):
    pfad = tmp_path / "neu.sqlite3"
    engine = erzeuge_engine(str(pfad))
    migrieren(engine)
    with erzeuge_sessionmaker(engine)() as s:
        assert [x.name for x in s.scalars(select(Verein))] == ["Verein"]
    engine.dispose()


def test_migration_drei_laeuft_nur_einmal(tmp_path):
    pfad = tmp_path / "zweimal.sqlite3"
    engine = erzeuge_engine(str(pfad))
    migrieren(engine)
    migrieren(engine)
    with erzeuge_sessionmaker(engine)() as s:
        assert len(s.scalars(select(Verein)).all()) == 1
    engine.dispose()


def test_neue_veranstaltung_gehoert_dem_verein(tmp_path):
    """Auch ohne Angabe (Tests, Demo, Kommandozeile): der erste Verein, notfalls neu angelegt."""
    engine = erzeuge_engine(str(tmp_path / "t.sqlite3"))
    tabellen_anlegen(engine)
    with erzeuge_sessionmaker(engine)() as s:
        v = Veranstaltung(name="X", datum=date(2026, 5, 1))
        s.add(v)
        s.commit()
        verein = s.scalars(select(Verein)).one()
        assert v.verein_id == verein.id
        w = Veranstaltung(name="Y", datum=date(2026, 5, 2))
        s.add(w)
        s.commit()
        assert w.verein_id == verein.id and len(s.scalars(select(Verein)).all()) == 1
    engine.dispose()


def test_vereinsname_aus_dem_branding_gilt_fuer_den_verein(tmp_path):
    from app import installation

    engine = erzeuge_engine(str(tmp_path / "b.sqlite3"))
    migrieren(engine)
    with erzeuge_sessionmaker(engine)() as s:
        installation.speichern(s, {"vereinsname": " RCB "})
        assert s.scalars(select(Verein)).one().name == "RCB"
        installation.speichern(s, {"vereinsname": ""})
        assert s.scalars(select(Verein)).one().name == "Verein"
    engine.dispose()
