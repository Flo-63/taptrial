"""Segmente 37: Orte mit drei Arten (Startort, Messpunkt, Zielort), Standardorte, Start-/Zielort je Strecke."""

from datetime import date

import pytest

from app import korrekturen as k
from app import strecken as st
from app import strecken_datei as sd
from app.db import erzeuge_engine, erzeuge_sessionmaker, schema_aktualisieren, tabellen_anlegen
from app.models import (
    GeloeschterToken,
    Messpunkt,
    Strecke,
    Teilnehmer,
    Veranstaltung,
    token_fingerabdruck,
)
from app.saetze import SatzFehler
from tests.unit import test_saetze
from tests.unit.test_saetze import MIN, T0, ereignis
from tests.unit.test_strecken import formular, veranstaltung

session = test_saetze.session  # Fixture


def zeile(m=None, name="", art="", entfernen=False):
    return {"id": str(m.id) if m else "", "name": name or (m.name if m else ""), "art": art,
            "entfernen": entfernen}  # fmt: skip


def orte_anlegen(session, v, *neu):
    """Bisherige Orte behalten und neue (Name, Art) dazu."""
    zeilen = [zeile(m) for m in st.orte_von(session, v)] + [zeile(name=n, art=a) for n, a in neu]
    return st.orte_speichern(session, v, zeilen)


def ort(session, v, name):
    return next(m for m in st.orte_von(session, v) if m.name == name)


def test_standardorte_je_veranstaltung(session):
    v = veranstaltung(session)
    orte = st.orte_von(session, v)
    assert [(m.name, m.art, m.standard) for m in orte] == [
        ("Start", "start", True),
        ("Ziel", "ziel", True),
    ]
    assert all(m.id for m in orte)  # gespeichert, nicht nur angezeigt
    for s in st.strecken_von(session, v):
        assert (st.startort_von(s, orte).name, st.zielort_von(s, orte).name) == ("Start", "Ziel")
        assert s.startort_id is None and s.zielort_id is None


def test_migration_vorher_gleich_nachher(tmp_path):
    """Ältere Datei ohne Ortsarten: Spalten werden nachgerüstet, Messpunkte bleiben Messpunkte, jede
    Veranstaltung bekommt Start und Ziel – das Ergebnis bleibt gleich."""
    from app import saetze as sz

    pfad = str(tmp_path / "alt.sqlite3")
    engine = erzeuge_engine(pfad)
    tabellen_anlegen(engine)
    with engine.begin() as conn:  # Schema vor Segmente 37 nachbauen
        for spalte in ("art", "standard", "rennleitung_getrennt", "token_rennleitung"):
            conn.exec_driver_sql(f"ALTER TABLE messpunkt DROP COLUMN {spalte}")
        conn.exec_driver_sql("DROP TABLE strecke_klasse")
        conn.exec_driver_sql("DROP TABLE strecke")
        conn.exec_driver_sql(
            "CREATE TABLE strecke (id INTEGER NOT NULL PRIMARY KEY, veranstaltung_id INTEGER NOT NULL "
            "REFERENCES veranstaltung(id), position INTEGER NOT NULL, name VARCHAR(100) NOT NULL, laenge_km FLOAT, "
            "teile_json TEXT, abschnitte_json TEXT, beschreibung TEXT, komoot_url VARCHAR(500), "
            "runden INTEGER NOT NULL DEFAULT 1, rundenlinie BOOLEAN NOT NULL DEFAULT 0)"
        )
        conn.exec_driver_sql(
            "CREATE TABLE strecke_klasse (id INTEGER NOT NULL PRIMARY KEY, strecke_id INTEGER NOT NULL "
            "REFERENCES strecke(id), klasse VARCHAR(100) NOT NULL)"
        )
        conn.exec_driver_sql(
            "INSERT INTO veranstaltung (id, name, datum, stichtag_alter, aktiv, token_start, token_ziel, "
            "token_rennleitung, rennleitung_getrennt, zustand, anonymisieren_ab, zwischenzeiten_oeffentlich, "
            "segmente_monitor, mindestrundenzeit_s) VALUES (1, 'Alt', '2026-10-10', '2026-12-31', 1, 'ts', 'tz', "
            "'tr', 0, 'laufend', '2027-04-10', 1, 0, 60)"
        )
        conn.exec_driver_sql("INSERT INTO strecke (id, veranstaltung_id, position, name, runden, rundenlinie) "
                             "VALUES (1, 1, 0, 'Strecke 1', 1, 0)")  # fmt: skip
        conn.exec_driver_sql("INSERT INTO messpunkt (id, veranstaltung_id, position, name, abschnitt, token) "
                             "VALUES (5, 1, 0, 'Kreisel', '', 'tk')")  # fmt: skip
        conn.exec_driver_sql("INSERT INTO teilnehmer (id, veranstaltung_id, startnummer, vorname, nachname, "
                             "geschlecht, geburtsjahr, abzeichen, ausser_konkurrenz) "
                             "VALUES (1, 1, 7, 'A', 'B', 'm', 1980, 0, 0)")  # fmt: skip
        for i, (art, m) in enumerate((("START", 0), ("ZIEL", 31))):
            conn.exec_driver_sql(
                "INSERT INTO ereignis (uuid, veranstaltung_id, art, zeit_ms, startnummer, geraet, erstellt_ms, "
                "empfangen_ms) VALUES (?, 1, ?, ?, 7, 'g', ?, ?)",
                (f"e{i}", art, T0 + m * MIN, T0 + m * MIN, T0),
            )
    with erzeuge_sessionmaker(engine)() as s:  # vorher: Ergebnis mit dem alten Stand
        sz.standardsaetze_anlegen(s)
        s.get(Veranstaltung, 1).klassensatz_id = sz.standard_klassensatz(s).id
        s.commit()
    schema_aktualisieren(engine)
    with erzeuge_sessionmaker(engine)() as s:
        v = s.get(Veranstaltung, 1)
        vorher = [
            (z.startnummer, z.fahrzeit_ms, z.rang_gesamt, z.strecke)
            for z in k.ergebnis(s, v).zeilen
        ]
        st.migrieren(s)
        orte = st.orte_von(s, v)
        assert [(m.name, m.art, m.standard) for m in orte] == [
            ("Start", "start", True), ("Kreisel", "punkt", False), ("Ziel", "ziel", True)]  # fmt: skip
        assert s.get(Messpunkt, 5).token == "tk" and s.get(Messpunkt, 5).token_rennleitung
        nachher = [
            (z.startnummer, z.fahrzeit_ms, z.rang_gesamt, z.strecke)
            for z in k.ergebnis(s, v).zeilen
        ]
        assert vorher == nachher == [(7, 31 * MIN, 1, 0)]
        st.migrieren(s)  # idempotent
        assert len(st.orte_von(s, v)) == 3
    engine.dispose()


def test_orte_anlegen_umbenennen_entfernen(session):
    v = veranstaltung(session)
    hinweise = orte_anlegen(session, v, ("Nord", "start"), ("Kreisel", ""), ("Hof", "ziel"))
    assert [(m.name, m.art) for m in st.orte_von(session, v)] == [
        ("Start", "start"), ("Nord", "start"), ("Kreisel", "punkt"), ("Ziel", "ziel"), ("Hof", "ziel")]  # fmt: skip
    assert "Start „Nord“ nutzt noch keine Strecke." in hinweise
    assert "Zwischenzeit „Kreisel“ kommt noch in keiner Strecke vor." in hinweise
    # Standard umbenennen geht, die Art bleibt
    zeilen = [
        zeile(m, name="Parkplatz" if m.name == "Start" else "", art="ziel")
        for m in st.orte_von(session, v)
    ]
    st.orte_speichern(session, v, zeilen)
    assert ort(session, v, "Parkplatz").art == "start" and ort(session, v, "Parkplatz").standard
    # ungenutzter Ort: entfernbar, Fingerabdruck der Tokens bleibt
    hof = ort(session, v, "Hof")
    abdruck = token_fingerabdruck(hof.token)
    st.orte_speichern(session, v, [zeile(hof, entfernen=True)])
    assert "Hof" not in [m.name for m in st.orte_von(session, v)]
    assert session.query(GeloeschterToken).filter_by(fingerabdruck=abdruck).count() == 1


@pytest.mark.parametrize(
    ("neu", "meldung"),
    [
        ([("Start", "punkt")], "nur für einen Messpunkt der Art „Start“"),
        ([("ziel", "start")], "nur für einen Messpunkt der Art „Ziel“"),
        ([("Nord", "start"), ("nord", "ziel")], "doppelt"),
        ([("", "start")], None),  # leere Zeile zählt nicht
    ],
)
def test_orte_pruefungen(session, neu, meldung):
    v = veranstaltung(session)
    if meldung is None:
        orte_anlegen(session, v, *neu)
        assert len(st.orte_von(session, v)) == 2
        return
    with pytest.raises(SatzFehler, match=meldung):
        orte_anlegen(session, v, *neu)


def test_genutzte_orte_nicht_entfernbar(session):
    """Segmente 65: genutzt (auch der Standard) = gesperrt; ungenutzt entfernbar, auch der Standard."""
    v = veranstaltung(session)
    orte_anlegen(session, v, ("Nord", "start"))
    with pytest.raises(SatzFehler, match="genutzt"):
        st.orte_speichern(session, v, [zeile(ort(session, v, "Start"), entfernen=True)])
    s = st.strecken_von(session, v)[0]
    st.strecke_orte_setzen(session, v, s, str(ort(session, v, "Nord").id), None)
    session.commit()
    with pytest.raises(SatzFehler, match="genutzt"):
        st.orte_speichern(session, v, [zeile(ort(session, v, "Nord"), entfernen=True)])


def test_standard_entfernen_und_wieder_anlegen(session):
    """Segmente 65: Beide Strecken starten an eigenen Startorten → „Start“ (Standard) lässt sich
    entfernen; er bleibt als Zeile (Tokens der Veranstaltung, alte Links), erscheint aber nirgends.
    Eine neue Strecke beginnt am ersten Start; gibt es keinen mehr, kommt der Standard zurück."""
    v = veranstaltung(session)  # Strecken „5 km“ und „20 km“
    orte_anlegen(session, v, ("Start 5k", "start"), ("Start 20k", "start"))
    a, b = st.strecken_von(session, v)
    st.strecke_orte_setzen(session, v, a, str(ort(session, v, "Start 5k").id), None)
    st.strecke_orte_setzen(session, v, b, str(ort(session, v, "Start 20k").id), None)
    session.commit()
    std = ort(session, v, "Start")
    hinweise = st.orte_speichern(session, v, [zeile(std, entfernen=True)])
    assert hinweise == []
    assert [m.name for m in st.orte_von(session, v, "start")] == ["Start 5k", "Start 20k"]
    assert st.standard_ort(session, v, "start").id == std.id and std.entfernt  # Zeile bleibt
    assert st.strecken_am_ort(session, v, ort(session, v, "Start 5k")) == [0]
    # neue Strecke: erster übriger Start, Standard-Ziel
    zeilen, zuordnung = formular(session, v)
    st.strecken_speichern(
        session, v, zeilen + [{"id": "", "name": "Neu", "laenge": "10,0"}], zuordnung, set()
    )
    neu = st.strecken_von(session, v)[-1]
    orte = st.orte_von(session, v)
    assert (
        st.startort_von(neu, orte).name == "Start 5k" and st.zielort_von(neu, orte).name == "Ziel"
    )
    # gibt es gar keinen Start mehr (nur über Altbestand denkbar), holt die neue Strecke „Start“ zurück
    std.entfernt = True
    for x in st.strecken_von(session, v):
        x.startort_id = None
    session.flush()
    for m in st.orte_von(session, v, "start"):
        session.delete(m)
    session.flush()
    assert st.orte_von(session, v, "start") == []
    s = Strecke(veranstaltung_id=v.id, position=9, name="Wieder")
    session.add(s)
    st.neue_strecke_orte(session, v, s)
    session.flush()
    assert s.startort_id is None and [m.name for m in st.orte_von(session, v, "start")] == ["Start"]
    assert not std.entfernt


def test_entfernter_standard_kommt_mit_gleichem_namen_zurueck(session):
    v = veranstaltung(session)
    orte_anlegen(session, v, ("Nord", "start"))
    for s in st.strecken_von(session, v):
        st.strecke_orte_setzen(session, v, s, str(ort(session, v, "Nord").id), None)
    session.commit()
    std = ort(session, v, "Start")
    st.orte_speichern(session, v, [zeile(std, entfernen=True)])
    orte_anlegen(session, v, ("Start", "start"))  # gleicher Name: kein zweiter „Start“
    starts = st.orte_von(session, v, "start")
    assert [(m.name, m.id) for m in starts] == [
        ("Start", std.id),
        ("Nord", ort(session, v, "Nord").id),
    ]


def test_kombinationen_start_und_ziel(session):
    """Gleicher Start/anderes Ziel, anderer Start/gleiches Ziel, beides verschieden."""
    v = veranstaltung(session)
    zeilen, zuordnung = formular(session, v)
    zeilen += [
        {"id": "", "name": "Nord-Ziel", "laenge": "10,0"},
        {"id": "", "name": "Nord-Hof", "laenge": "12,0"},
    ]
    st.strecken_speichern(session, v, zeilen, zuordnung, set())
    orte_anlegen(session, v, ("Nord", "start"), ("Hof", "ziel"))
    nord, hof = ort(session, v, "Nord"), ort(session, v, "Hof")
    _a, b, c, d = st.strecken_von(session, v)
    st.strecke_orte_setzen(session, v, b, None, str(hof.id))  # gleicher Start, anderes Ziel
    st.strecke_orte_setzen(session, v, c, str(nord.id), None)  # anderer Start, gleiches Ziel
    st.strecke_orte_setzen(session, v, d, str(nord.id), str(hof.id))  # beides verschieden
    session.commit()
    paare = [(x.name, y.name) for x, y in st.orte_der_strecken(session, v)]
    assert paare == [("Start", "Ziel"), ("Start", "Hof"), ("Nord", "Ziel"), ("Nord", "Hof")]
    assert st.strecken_am_ort(session, v, ort(session, v, "Start")) == [0, 1]
    assert st.strecken_am_ort(session, v, nord) == [2, 3]
    assert st.strecken_am_ort(session, v, ort(session, v, "Ziel")) == [0, 2]
    assert st.strecken_am_ort(session, v, hof) == [1, 3]
    defs = st.auswertungs_strecken(session, v)
    assert [(x.startort.name, x.zielort.name) for x in defs] == paare
    # Standard wird als „keine Angabe“ gespeichert
    st.strecke_orte_setzen(
        session, v, d, str(ort(session, v, "Start").id), str(ort(session, v, "Ziel").id)
    )
    assert d.startort_id is None and d.zielort_id is None


def test_start_und_zielort_frieren_ein(session):
    v = veranstaltung(session)
    orte_anlegen(session, v, ("Nord", "start"), ("Hof", "ziel"))
    session.add(Teilnehmer(veranstaltung_id=v.id, startnummer=9, vorname="X", nachname="Y", geschlecht="m",
                           geburtsjahr=1980))  # fmt: skip
    session.commit()
    ereignis(session, v, "START", startnummer=1, zeit_ms=T0)
    from app import saetze as sz

    sz.einfrieren(session, v)
    s = st.strecken_von(session, v)[0]
    with pytest.raises(SatzFehler, match="eingefroren"):
        st.strecke_orte_setzen(session, v, s, str(ort(session, v, "Nord").id), None)
    st.strecke_orte_setzen(
        session, v, s, str(ort(session, v, "Start").id), None
    )  # unverändert: kein Fehler
    with pytest.raises(SatzFehler, match="eingefroren"):
        orte_anlegen(session, v, ("Süd", "start"))
    with pytest.raises(SatzFehler, match="Unbekannter Messpunkt für „Ziel“"):
        st.strecke_orte_setzen(session, v, s, None, str(ort(session, v, "Nord").id))


def test_streckendatei_mit_start_und_zielort(session):
    v = veranstaltung(session)
    orte_anlegen(session, v, ("Nord", "start"), ("Hof", "ziel"))
    s = st.strecken_von(session, v)[1]
    st.strecke_orte_setzen(
        session, v, s, str(ort(session, v, "Nord").id), str(ort(session, v, "Hof").id)
    )
    session.commit()
    text = sd.strecke_toml(session, v, s)
    assert 'startort = "Nord"' in text and 'zielort = "Hof"' in text
    e = sd.lesen(text.encode())
    assert e.fehler == [] and (e.startort, e.zielort) == ("Nord", "Hof")
    w = k.veranstaltung_anlegen(session, "Zweite", date(2026, 10, 11), aktivieren=False)
    vs = sd.vorschau(session, w, e)
    assert vs.neu == ["Nord", "Hof"] and vs.wiederverwendet == [] and vs.abfolge == "Nord · Hof"
    neu = sd.uebernehmen(session, w, e, "Kopie", set())
    orte = st.orte_von(session, w)
    assert (st.startort_von(neu, orte).name, st.zielort_von(neu, orte).name) == ("Nord", "Hof")
    assert {(m.name, m.art) for m in orte} >= {("Nord", "start"), ("Hof", "ziel")}
    # gleicher Name, andere Art: Prüfhinweis statt stiller Umdeutung
    x = k.veranstaltung_anlegen(session, "Dritte", date(2026, 10, 12), aktivieren=False)
    orte_anlegen(session, x, ("Nord", ""))
    vs = sd.vorschau(session, x, e)
    assert vs.ortkonflikte == [
        "„Nord“ ist hier ein Messpunkt der Art „Zwischenzeit“, in der Datei „Start“."
    ]
    with pytest.raises(SatzFehler, match="Messpunkt"):
        sd.uebernehmen(session, x, e, "Kopie", set())
    # ältere Dateien ohne Start-/Zielort: Standardorte
    alt = sd.lesen(
        text.replace('startort = "Nord"\n', "").replace('zielort = "Hof"\n', "").encode()
    )
    assert alt.fehler == [] and (alt.startort, alt.zielort) == ("", "")


def test_strecke_mit_startort_bleibt_loeschbar(session):
    """Veranstaltung löschen: Strecken verweisen auf ihre Orte – beides verschwindet."""
    v = veranstaltung(session)
    orte_anlegen(session, v, ("Nord", "start"))
    s = st.strecken_von(session, v)[0]
    st.strecke_orte_setzen(session, v, s, str(ort(session, v, "Nord").id), None)
    session.commit()
    token = ort(session, v, "Nord").token
    k.loeschen(session, v)
    assert session.query(Strecke).count() == 0 and session.query(Messpunkt).count() == 0
    assert (
        session.query(GeloeschterToken).filter_by(fingerabdruck=token_fingerabdruck(token)).count()
        == 1
    )


def test_ort_id_migration_vorher_gleich_nachher(tmp_path):
    """Segmente 43: Bisher stand der Ort eines Start-/Zielgeräts in „wert“ (A144). Beim Nachrüsten
    von „ereignis.ort_id“ wird er übernommen – nur für weitere Orte passender Art; „wert“ bleibt."""
    pfad = str(tmp_path / "alt.sqlite3")
    engine = erzeuge_engine(pfad)
    tabellen_anlegen(engine)
    with erzeuge_sessionmaker(engine)() as s:
        v = k.veranstaltung_anlegen(s, "Alt", date(2026, 10, 10), aktivieren=True)
        st.orte_sicherstellen(s, v)
        for name, art in (("Nord", "start"), ("Hof", "ziel"), ("Kreisel", "punkt")):
            s.add(Messpunkt(veranstaltung_id=v.id, position=1, name=name, art=art))
        s.add(Teilnehmer(veranstaltung_id=v.id, startnummer=7, vorname="A", nachname="B",
                         geschlecht="m", geburtsjahr=1980))  # fmt: skip
        s.commit()
        o = {m.name: m.id for m in st.orte_von(s, v)}
        vid = v.id
    with engine.begin() as conn:  # Stand vor Segmente 43: Ort in „wert“, keine Spalte ort_id
        conn.exec_driver_sql("ALTER TABLE ereignis DROP COLUMN ort_id")
        zeilen = [("s", "START", 0, 7, o["Nord"]), ("z", "ZIEL", 31, 7, o["Hof"]),
                  ("o", "ZIEL", 40, None, o["Hof"]), ("x", "ZIEL", 41, None, o["Kreisel"]),
                  ("y", "ZIEL", 42, None, o["Ziel"]), ("n", "ZIEL", 43, None, None)]  # fmt: skip
        for uid, art, m, nr, wert in zeilen:
            conn.exec_driver_sql(
                "INSERT INTO ereignis (uuid, veranstaltung_id, art, zeit_ms, startnummer, wert, geraet, "
                "erstellt_ms, empfangen_ms) VALUES (?, ?, ?, ?, ?, ?, 'g', ?, ?)",
                (uid, vid, art, T0 + m * MIN, nr, None if wert is None else str(wert), T0, T0),
            )
    schema_aktualisieren(engine)
    with erzeuge_sessionmaker(engine)() as s:
        from app.models import Ereignis

        neu = {e.uuid: (e.ort_id, e.wert) for e in s.query(Ereignis)}
        assert neu["s"] == (o["Nord"], str(o["Nord"])) and neu["z"] == (o["Hof"], str(o["Hof"]))
        assert neu["x"][0] is None and neu["y"][0] is None and neu["n"] == (None, None)
        erg = k.ergebnis(s, s.get(Veranstaltung, vid))
        # vorher (aus „wert“) = nachher (aus „ort_id“): offene Zeit am Ziel Hof, Fahrzeit unverändert
        assert sorted((x.uuid, x.ort) for x in erg.offene_ziele) == [
            ("n", ""), ("o", str(o["Hof"])), ("x", ""), ("y", "")]  # fmt: skip
        assert erg.zeile(7).fahrzeit_ms == 31 * MIN
        assert [x.ort for x in erg.startstempel] == [str(o["Nord"])]
        with pytest.raises(Exception, match="append-only"):  # Trigger wieder da
            s.query(Ereignis).filter_by(uuid="s").update({"wert": "1"})
            s.flush()
    engine.dispose()
