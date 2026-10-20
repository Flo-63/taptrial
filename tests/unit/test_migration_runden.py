"""Migration Segmente: alte Datenbanken ohne Segmente bleiben gleich; Segment-Daten von phase-14
(Anlauf/Runde/Auslauf, Rundenzahl) werden ins Segment-Modell übertragen (vorher = nachher)."""

import hashlib
import shutil
import sqlite3

import pytest

from app import korrekturen as k
from app import saetze as sz
from app import strecken as st
from app.db import erzeuge_engine, erzeuge_sessionmaker, tabellen_anlegen
from app.models import Veranstaltung
from tests.unit.test_migration_saetze import BACKUPS, _kurz, alte_datenbank, ergebnis_vorher


def nachher(pfad):
    engine = erzeuge_engine(str(pfad))
    tabellen_anlegen(engine)
    with erzeuge_sessionmaker(engine)() as s:
        sz.standardsaetze_anlegen(s)
        sz.veranstaltungen_zuordnen(s)
        st.migrieren(s)
        ergebnisse = {v.id: _kurz(k.ergebnis(s, v)) for v in s.query(Veranstaltung)}
        aufbau = {(x.teile_json, len(st.messpunkte_von(s, v))) for v in s.query(Veranstaltung)
                  for x in st.strecken_von(s, v)}  # fmt: skip
        oeffentlich = {
            (v.zwischenzeiten_oeffentlich, v.mindestrundenzeit_s) for v in s.query(Veranstaltung)
        }
        ohne_durchfahrten = all(
            not k.ergebnis(s, v).mit_durchfahrten for v in s.query(Veranstaltung)
        )
    engine.dispose()
    return ergebnisse, aufbau, oeffentlich, ohne_durchfahrten


@pytest.mark.parametrize("mit_wertung", [False, True])
def test_alte_datenbank(tmp_path, mit_wertung):
    pfad = tmp_path / "alt.sqlite3"
    alte_datenbank(pfad, mit_wertung)
    vorher = ergebnis_vorher(pfad)
    ergebnisse, aufbau, oeffentlich, ohne = nachher(pfad)
    assert ergebnisse == vorher and aufbau == {(None, 0)} and oeffentlich == {(True, 60)} and ohne


def hat_segmente(pfad) -> bool:
    """Sicherung aus der Zeit mit Segment-Schema (Spalte strecke.teile_json, Tabelle messpunkt)."""
    con = sqlite3.connect(f"file:{pfad}?mode=ro", uri=True)
    try:
        return "teile_json" in {z[1] for z in con.execute("PRAGMA table_info(strecke)")}
    finally:
        con.close()


def aufbau_roh(pfad) -> tuple:
    """Strecken-Aufbau, Messpunkte und Ereignisse direkt aus der Datei (ohne App)."""
    con = sqlite3.connect(f"file:{pfad}?mode=ro", uri=True)
    try:
        strecken = {z[0]: z[1:] for z in con.execute(
            "SELECT id, veranstaltung_id, name, teile_json, abschnitte_json FROM strecke")}  # fmt: skip
        punkte = set(con.execute("SELECT id, veranstaltung_id, name, art, token FROM messpunkt"))
        ereignisse = sorted(
            con.execute("SELECT uuid, art, startnummer, zeit_ms, wert FROM ereignis")
        )
        return strecken, punkte, ereignisse
    finally:
        con.close()


@pytest.mark.skipif(not BACKUPS, reason="kein Backup unter ~/rcb-backups/")
@pytest.mark.parametrize("backup", BACKUPS, ids=lambda p: p.name)
def test_kopie_des_backups(tmp_path, backup):
    """Sicherungen von vor den Segmenten: danach ohne Teile, Messpunkte und Durchfahrten (vorher =
    nachher). Sicherungen, die schon mit der Segment-Version gezogen wurden (ab
    12.10. 19:30, mit Demo-Veranstaltung), enthalten Segment-Strecken, Messpunkte und Durchfahrten
    bereits – dort muss die Migration Aufbau, Messpunkte, Ereignisse und Ergebnisse unverändert
    lassen."""
    original = hashlib.sha256(backup.read_bytes()).hexdigest()
    kopie = tmp_path / backup.name
    shutil.copy2(backup, kopie)
    schon_segmente = hat_segmente(kopie)
    roh_vorher = aufbau_roh(kopie) if schon_segmente else None
    vorher = ergebnis_vorher(kopie)
    ergebnisse, aufbau, _, ohne = nachher(kopie)
    assert ergebnisse == vorher
    if schon_segmente:
        assert aufbau_roh(kopie) == roh_vorher  # nichts angelegt, geändert oder entfernt
    else:
        assert aufbau == {(None, 0)} and ohne
    assert hashlib.sha256(backup.read_bytes()).hexdigest() == original


def test_segment_daten_von_phase_14_vorher_gleich_nachher(tmp_path):
    """Strecke mit 3 Runden, Kreisel/Berg in der Runde, Brücke im Auslauf (Modell phase-14):
    gleiche Punktfolge, jede Durchfahrt eines sauberen Rennens landet an derselben Stelle wie
    vorher (Messpunkt, Runde), Fahrzeit und Rang unverändert."""
    from datetime import date

    from app import segmente as sg
    from app.models import Ereignis, Messpunkt, Teilnehmer

    engine = erzeuge_engine(str(tmp_path / "p14.sqlite3"))
    tabellen_anlegen(engine)
    T0, MIN = 1_791_000_000_000, 60_000
    wahr = {}
    with erzeuge_sessionmaker(engine)() as s:
        sz.standardsaetze_anlegen(s)
        v = Veranstaltung(name="Rund", datum=date(2026, 10, 10), aktiv=True,
                          klassensatz_id=sz.standard_klassensatz(s).id)  # fmt: skip
        s.add(v)
        s.flush()
        st.sicherstellen(s, v)
        strecke = st.strecken_von(s, v)[0]
        strecke.runden = 3
        mps = [Messpunkt(veranstaltung_id=v.id, strecke_id=strecke.id, position=i, name=n, abschnitt=a, km=km)
               for i, (n, a, km) in enumerate([("Kreisel", "runde", 3.0), ("Berg", "runde", 7.0),
                                               ("Brücke", "auslauf", 31.0)])]  # fmt: skip
        s.add_all(mps)
        s.flush()
        kr, berg, br = (str(m.id) for m in mps)
        n = 0

        def e(art, t, nr, **kw):
            nonlocal n
            n += 1
            x = Ereignis(uuid=f"u{n}", veranstaltung_id=v.id, art=art, geraet="g", erstellt_ms=t,
                         empfangen_ms=t, zeit_ms=t, startnummer=nr, **kw)  # fmt: skip
            s.add(x)
            return x

        for nr, lap in ((1, 10), (2, 11), (3, 9)):
            s.add(Teilnehmer(veranstaltung_id=v.id, startnummer=nr, vorname="V", nachname=f"N{nr}",
                             geschlecht="m", geburtsjahr=1980))  # fmt: skip
            start = T0 + nr * MIN
            e("START", start, nr)
            for r in (1, 2, 3):
                for mid, anteil in ((kr, 0.3), (berg, 0.7)):
                    wahr[
                        e(
                            "DURCHFAHRT", start + int((r - 1 + anteil) * lap * MIN), nr, wert=mid
                        ).uuid
                    ] = (mid, r)
            wahr[e("DURCHFAHRT", start + int(3.05 * lap * MIN), nr, wert=br).uuid] = (br, 0)
            e("ZIEL", start + int(3.1 * lap * MIN), nr)
        s.commit()
        st.migrieren(s)
        strecke = st.strecken_von(s, v)[0]
        teile = st.teile_von(strecke)
        assert sg.punkte(teile) == [sg.START, kr, berg, kr, berg, kr, berg, br, sg.ZIEL]
        erg = k.ergebnis(s, v)
        for z in erg.zeilen:
            assert z.fahrzeit_ms is not None and z.rang_gesamt is not None
            # vorher: (Messpunkt, Runde) je Durchfahrt; nachher: Position in der Abfolge
            vorher = [wahr[d.uuid] for d in z.durchfahrten]
            assert vorher == [(kr, 1), (berg, 1), (kr, 2), (berg, 2), (kr, 3), (berg, 3), (br, 0)]
            assert all(x.zeit_ms is not None for x in z.segmentzeiten)
        st.migrieren(s)  # ein zweites Mal ändert nichts
        assert st.teile_von(st.strecken_von(s, v)[0]) == teile
    engine.dispose()


def test_messpunkte_je_strecke_werden_messpunkte_der_veranstaltung(tmp_path):
    """Alte Tabelle (Messpunkt je Strecke, strecke_id Pflicht) → Messpunkt der
    Veranstaltung; IDs, Tokens und Teile bleiben, gleiche Namen werden eindeutig, Auswertung gleich."""
    from datetime import date

    from app import segmente as sg
    from app.models import Ereignis, Teilnehmer
    from app.simulation import beispiel_teile

    pfad = tmp_path / "s11.sqlite3"
    engine = erzeuge_engine(str(pfad))
    tabellen_anlegen(engine)
    T0, MIN = 1_791_000_000_000, 60_000
    with erzeuge_sessionmaker(engine)() as s:
        sz.standardsaetze_anlegen(s)
        v = Veranstaltung(name="Alt", datum=date(2026, 10, 10), aktiv=True,
                          klassensatz_id=sz.standard_klassensatz(s).id)  # fmt: skip
        s.add(v)
        s.flush()
        st.sicherstellen(s, v)
        strecke = st.strecken_von(s, v)[0]
        s.add(Teilnehmer(veranstaltung_id=v.id, startnummer=1, vorname="V", nachname="N", geschlecht="m",
                         geburtsjahr=1980))  # fmt: skip
        s.flush()
        vid, sid = v.id, strecke.id
        s.commit()
    with engine.begin() as conn:  # Schema des ersten Segment-Modells nachbauen
        conn.exec_driver_sql("DROP TABLE messpunkt")
        conn.exec_driver_sql(
            "CREATE TABLE messpunkt (id INTEGER NOT NULL PRIMARY KEY, strecke_id INTEGER NOT NULL "
            "REFERENCES strecke(id), position INTEGER NOT NULL, name VARCHAR(100) NOT NULL, km FLOAT, "
            "abschnitt VARCHAR(10) NOT NULL, token VARCHAR(64) NOT NULL UNIQUE)"
        )
    with engine.begin() as conn:
        for mid, name, tok in ((11, "Kreisel", "t-k"), (12, "Berg", "t-b")):
            conn.exec_driver_sql(
                "INSERT INTO messpunkt (id, strecke_id, position, name, km, abschnitt, token) VALUES (?, ?, ?, ?, NULL, '', ?)",
                (mid, sid, mid - 11, name, tok),
            )
        conn.exec_driver_sql("UPDATE strecke SET teile_json = ? WHERE id = ?",
                             (sg.nach_json(beispiel_teile({"P1": "11", "P2": "12"})), sid))  # fmt: skip
    with erzeuge_sessionmaker(engine)() as s:
        for i, (mp, m) in enumerate([("11", 3), ("12", 8), ("11", 13), ("12", 18), ("11", 23)]):
            s.add(Ereignis(uuid=f"d{i}", veranstaltung_id=vid, art="DURCHFAHRT", geraet="g", erstellt_ms=T0 + m * MIN,
                           empfangen_ms=T0, zeit_ms=T0 + m * MIN, startnummer=1, wert=mp))  # fmt: skip
        for art, m in (("START", 0), ("ZIEL", 25)):
            s.add(Ereignis(uuid=art, veranstaltung_id=vid, art=art, geraet="g", erstellt_ms=T0 + m * MIN,
                           empfangen_ms=T0, zeit_ms=T0 + m * MIN, startnummer=1))  # fmt: skip
        s.commit()
    engine.dispose()
    engine = erzeuge_engine(str(pfad))
    tabellen_anlegen(engine)  # baut die Tabelle um
    with erzeuge_sessionmaker(engine)() as s:
        st.migrieren(s)
        v = s.get(Veranstaltung, vid)
        mps = st.messpunkte_von(s, v)
        assert [(m.id, m.name, m.token, m.veranstaltung_id) for m in mps] == [
            (11, "Kreisel", "t-k", vid), (12, "Berg", "t-b", vid)]  # fmt: skip
        z = k.ergebnis(s, v).zeile(1)
        assert [x.zeit_ms // MIN for x in z.segmentzeiten] == [
            3,
            5,
            5,
            5,
            5,
            2,
        ] and z.fahrzeit_ms == 25 * MIN
        assert client_token_gueltig(s, "t-k") == (11, vid)
    engine.dispose()


def client_token_gueltig(session, token):
    m, v, _rolle = st.messpunkt_fuer_token(session, token)
    return m.id, v.id


def test_gleiche_messpunktnamen_mehrerer_strecken_werden_eindeutig(tmp_path):
    from datetime import date

    from app.models import Messpunkt

    engine = erzeuge_engine(str(tmp_path / "x.sqlite3"))
    tabellen_anlegen(engine)
    with erzeuge_sessionmaker(engine)() as s:
        sz.standardsaetze_anlegen(s)
        v = Veranstaltung(
            name="X", datum=date(2026, 10, 10), klassensatz_id=sz.standard_klassensatz(s).id
        )
        s.add(v)
        s.flush()
        st.anlegen(s, v, [st.Vorschlag("Kurz", None, []), st.Vorschlag("Lang", None, [])])
        kurz, lang = st.strecken_von(s, v)
        s.add_all([Messpunkt(veranstaltung_id=v.id, strecke_id=lang.id, position=0, name="Kreisel"),
                   Messpunkt(veranstaltung_id=v.id, strecke_id=kurz.id, position=0, name="Kreisel")])  # fmt: skip
        s.commit()
        st.migrieren(s)
        assert [m.name for m in st.messpunkte_von(s, v)] == ["Kreisel (Kurz)", "Kreisel (Lang)"]
    engine.dispose()
