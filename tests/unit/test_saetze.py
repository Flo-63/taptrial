"""Klassen- und Wertungssätze in der Datenbank."""

import uuid
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select

from app import korrekturen as k
from app import saetze as sz
from app import strecken
from app.db import erzeuge_engine, erzeuge_sessionmaker, tabellen_anlegen
from app.einstellungen import STANDARD_KLASSEN_DATEI, STANDARD_WERTUNGEN
from app.klassen import KlassenConfigFehler, lade_klassen
from app.models import Ereignis, Klassensatz, Teilnehmer, Veranstaltung, Wertungssatz
from app.wertung import lade_wertungen

T0 = 1_791_000_000_000
MIN = 60_000


@pytest.fixture
def session(tmp_path):
    engine = erzeuge_engine(str(tmp_path / "s.sqlite3"))
    tabellen_anlegen(engine)
    with erzeuge_sessionmaker(engine)() as s:
        sz.standardsaetze_anlegen(s)
        yield s
    engine.dispose()


def dra(session):
    return sz.nach_name(session, Klassensatz, "DRA 2026")


def ws(session, name="DRA A.1 flach"):
    return sz.nach_name(session, Wertungssatz, name)


# --- Standardsätze ---------------------------------------------------------------------


def test_standardsaetze_wie_dateien(session):
    assert [s.name for s in sz.klassensaetze(session)] == ["DRA 2026"]
    assert [w.name for w in sz.wertungssaetze(session)] == [
        "DRA A.1 flach", "DRA A.2 Berg Straße", "DRA A.3 Berg Gelände",
    ]  # fmt: skip
    klassen = lade_klassen(STANDARD_KLASSEN_DATEI)
    assert sz.klassen_von(dra(session)) == klassen
    dateien = lade_wertungen(STANDARD_WERTUNGEN, klassen)
    for datei, name in sz.WERTUNG_NAMEN.items():
        aus_db = sz.wertung_von(ws(session, name))
        assert aus_db.stufen == dateien[datei].stufen and aus_db.art == dateien[datei].art
        assert aus_db.name == dateien[datei].name  # Beschreibung wie bisher angezeigt
        assert aus_db.strecke_min_zehntel_km == dateien[datei].strecke_min_zehntel_km
    sz.standardsaetze_anlegen(session)  # idempotent
    assert len(sz.klassensaetze(session)) == 1


def test_ungueltige_datei_beim_ersten_start(tmp_path):
    engine = erzeuge_engine(str(tmp_path / "x.sqlite3"))
    tabellen_anlegen(engine)
    kaputt = tmp_path / "k.toml"
    kaputt.write_text('[[klasse]]\nname = "A"\ngeschlecht = "m"\nalter_von = 5\nalter_bis = 1\n')
    with erzeuge_sessionmaker(engine)() as s, pytest.raises(KlassenConfigFehler):
        sz.standardsaetze_anlegen(s, str(kaputt))
    engine.dispose()


# --- Validierung -----------------------------------------------------------------------


def zeile(name, g, von, bis):
    return {"name": name, "geschlecht": g, "alter_von": str(von), "alter_bis": str(bis)}


def test_klassensatz_ueberschneidung_ist_fehler(session):
    with pytest.raises(sz.SatzFehler, match="überschneiden"):
        sz.klassensatz_speichern(
            session, None, "X", [zeile("A", "m", 10, 20), zeile("B", "m", 20, 30)]
        )


def test_klassensatz_luecke_ist_warnung(session):
    satz, warnungen = sz.klassensatz_speichern(
        session,
        None,
        "Mit Lücke",
        [zeile("A", "m", 10, 20), zeile("B", "m", 25, 30), zeile("C", "w", 1, 99)],
    )
    assert satz.id and warnungen == ["Lücke bei Geschlecht m: Alter 21–24 hat keine Klasse"]


def test_klassensatz_name_eindeutig_und_pflicht(session):
    with pytest.raises(sz.SatzFehler, match="bereits vergeben"):
        sz.klassensatz_speichern(session, None, "dra 2026", [zeile("A", "m", 1, 2)])
    with pytest.raises(sz.SatzFehler, match="Name fehlt"):
        sz.klassensatz_speichern(session, None, " ", [zeile("A", "m", 1, 2)])


def test_klassensatz_gross_klein_und_leere_zeilen(session):
    satz, _ = sz.klassensatz_speichern(
        session,
        None,
        "Groß",
        [zeile("A", " M ", 1, 50), {"name": "", "geschlecht": ""}, zeile("B", "W", 1, 50)],
    )
    assert [(x.name, x.geschlecht) for x in satz.klassen] == [("A", "m"), ("B", "w")]


def test_klasse_in_wertung_nicht_entfernbar(session):
    zeilen = [z for z in sz.klassen_zeilen(dra(session)) if z["name"] != "Herren 31-40"]
    with pytest.raises(sz.SatzFehler, match="Herren 31-40"):
        sz.klassensatz_speichern(session, dra(session), "DRA 2026", zeilen)


def stufe(klasse, gold, silber, bronze, km=""):
    return {"klasse": klasse, "gold": gold, "silber": silber, "bronze": bronze, "strecke_km": km}


def test_wertungssatz_validierung(session):
    ks = dra(session).id
    with pytest.raises(sz.SatzFehler, match="gold < silber < bronze"):
        sz.wertungssatz_speichern(session, None, "W", "gesamtzeit", None, None, ks,
                                  [stufe("Herren 31-40", "0:50:00", "0:43:00", "0:46:00", "20")])  # fmt: skip
    with pytest.raises(sz.SatzFehler, match="gibt es nicht"):
        sz.wertungssatz_speichern(session, None, "W", "gesamtzeit", None, None, ks,
                                  [stufe("Senioren", "0:40:00", "0:43:00", "0:46:00", "20")])  # fmt: skip
    with pytest.raises(sz.SatzFehler, match="art"):
        sz.wertungssatz_speichern(session, None, "W", "falsch", None, None, ks,
                                  [stufe("Herren 31-40", "0:40:00", "0:43:00", "0:46:00", "20")])  # fmt: skip
    with pytest.raises(sz.SatzFehler, match="strecke_km_min"):
        sz.wertungssatz_speichern(session, None, "W", "pro_km", None, None, ks,
                                  [stufe("Herren 31-40", "2:00", "3:00", "4:00")])  # fmt: skip


def test_wertungssatz_klassen_ohne_gross_klein_und_warnung(session):
    satz, warnungen = sz.wertungssatz_speichern(
        session, None, "Mini", "GESAMTZEIT", None, None, dra(session).id,
        [stufe("herren 31-40", "0:40:00", "0:43:00", "0:46:00", "20,0")],
    )  # fmt: skip
    assert satz.art == "gesamtzeit" and satz.stufen[0].klasse == "Herren 31-40"
    assert satz.stufen[0].strecke_km == 20.0 and "Ohne Stufen" in warnungen[0]


# --- Kopieren, Löschen ----------------------------------------------------------------


def test_kopieren_und_loeschen(session):
    kopie = sz.kopieren(session, dra(session))
    assert kopie.name == "Kopie von DRA 2026" and sz.klassen_von(kopie) == sz.klassen_von(
        dra(session)
    )
    assert sz.kopieren(session, dra(session)).name == "Kopie 2 von DRA 2026"
    wkopie = sz.kopieren(session, ws(session))
    assert (
        wkopie.name == "Kopie von DRA A.1 flach"
        and sz.wertung_von(wkopie).stufen == sz.wertung_von(ws(session)).stufen
    )
    sz.loeschen(session, wkopie)
    sz.loeschen(session, kopie)
    with pytest.raises(sz.SatzFehler, match="Wertungssatz"):
        sz.loeschen(session, dra(session))  # von Wertungssätzen benutzt
    v = k.veranstaltung_anlegen(session, "Rennen", date(2026, 10, 10), aktivieren=True)
    sz.regeln_setzen(session, v, v.klassensatz_id, ws(session).id, 20.0)
    with pytest.raises(sz.SatzFehler, match="Rennen"):
        sz.loeschen(session, ws(session))


# --- Import/Export --------------------------------------------------------------------


def test_klassen_toml_und_csv_rundreise(session):
    satz = dra(session)
    for inhalt, datei in (
        (sz.klassen_toml(satz).encode(), "k.toml"),
        (sz.klassen_csv(satz), "k.csv"),
    ):
        _, zeilen = sz.klassen_import(inhalt, datei)
        neu, _ = sz.klassensatz_speichern(session, None, f"Import {datei}", zeilen)
        assert sz.klassen_von(neu) == sz.klassen_von(satz)
    assert sz.klassen_import(sz.klassen_toml(satz).encode(), "k.toml")[0] == "DRA 2026"


def test_klassen_import_bisherige_datei_und_gross_klein(session):
    name, zeilen = sz.klassen_import(
        Path(STANDARD_KLASSEN_DATEI).read_bytes(), "altersklassen.toml"
    )
    assert name is None and len(zeilen) == 20
    csv = b"NAME;Geschlecht;ALTER_VON;Alter_Bis\nSenioren;M;60;99\nSeniorinnen;w;60;99\n"
    _, zeilen = sz.klassen_import(csv, "x.CSV")
    satz, _ = sz.klassensatz_speichern(session, None, "Senioren", zeilen)
    assert [(x.name, x.geschlecht) for x in satz.klassen] == [
        ("Senioren", "m"),
        ("Seniorinnen", "w"),
    ]
    toml = b'[[KLASSE]]\nName = "A"\nGESCHLECHT = "W"\nalter_von = 1\nalter_bis = 9\n'
    _, zeilen = sz.klassen_import(toml, "x.toml")
    assert sz.klassensatz_speichern(session, None, "T", zeilen)[0].klassen[0].geschlecht == "w"


def test_wertung_toml_und_csv_rundreise(session):
    for name in ("DRA A.1 flach", "DRA A.2 Berg Straße"):
        satz = ws(session, name)
        d = sz.wertung_import(sz.wertung_toml(satz).encode(), "w.toml")
        neu, _ = sz.wertungssatz_speichern(session, None, f"T {name}", d["art"], d["strecke_km_min"],
                                           d["strecke_km_max"], dra(session).id, d["zeilen"],
                                           beschreibung=d["beschreibung"])  # fmt: skip
        assert sz.wertung_von(neu).stufen == sz.wertung_von(satz).stufen
        assert neu.beschreibung == satz.beschreibung and neu.strecke_km_min == satz.strecke_km_min
        d = sz.wertung_import(sz.wertung_csv(satz), "w.csv")
        neu, _ = sz.wertungssatz_speichern(session, None, f"C {name}", satz.art, satz.strecke_km_min,
                                           satz.strecke_km_max, dra(session).id, d["zeilen"])  # fmt: skip
        assert sz.wertung_von(neu).stufen == sz.wertung_von(satz).stufen


def test_wertung_import_bisherige_datei(session):
    d = sz.wertung_import(
        (Path(STANDARD_WERTUNGEN) / "radsportabzeichen-a1-flach.toml").read_bytes(), "a.toml"
    )
    assert d["art"] == "gesamtzeit" and len(d["zeilen"]) == 20


# --- Veranstaltung: Regeln, Einfrieren ------------------------------------------------


def veranstaltung_mit_fahrer(session):
    v = k.veranstaltung_anlegen(session, "Rennen", date(2026, 10, 10), aktivieren=True)
    session.add(Teilnehmer(veranstaltung_id=v.id, startnummer=1, vorname="A", nachname="B",
                           geschlecht="m", geburtsjahr=1990, abzeichen=True))  # fmt: skip
    session.commit()
    return v


def ereignis(session, v, art, **kw):
    session.add(Ereignis(uuid=str(uuid.uuid4()), veranstaltung_id=v.id, art=art, geraet="t",
                         erstellt_ms=kw.get("zeit_ms", T0), empfangen_ms=T0, **kw))  # fmt: skip
    session.commit()


def test_regeln_setzen_und_validieren(session):
    v = veranstaltung_mit_fahrer(session)
    assert v.klassensatz_id == dra(session).id and sz.regeln(session, v).wertung is None
    a2 = sz.nach_name(session, Wertungssatz, "DRA A.2 Berg Straße")
    with pytest.raises(sz.SatzFehler, match="Streckenlänge"):  # pro_km: keine Länge vorgeschlagen
        sz.regeln_setzen(session, v, v.klassensatz_id, a2.id, None)
    sz.regeln_setzen(session, v, v.klassensatz_id, a2.id, 4.0)
    assert [(x.name, x.laenge_km) for x in strecken.strecken_von(session, v)] == [
        ("Strecke 1", 4.0)
    ]
    # A.1 (gesamtzeit): keine Strecken still angelegt, die vorhandene bleibt
    hinweise = sz.regeln_setzen(session, v, v.klassensatz_id, ws(session).id, None)
    assert [(x.name, x.laenge_km) for x in strecken.strecken_von(session, v)] == [
        ("Strecke 1", 4.0)
    ]
    assert not any("vorgeschlagen" in h for h in hinweise)
    assert sz.regeln(session, v).wertung.name.startswith("Deutsches Radsportabzeichen A.1")
    mini, _ = sz.klassensatz_speichern(
        session, None, "Nur Herren", [zeile("Herren 31-40", "m", 18, 99)]
    )
    # Klassensatz wechseln: die nicht mehr passende Wertung wird mit Hinweis zurückgesetzt
    hinweise = sz.regeln_setzen(session, v, mini.id, ws(session).id, 20.0)
    assert v.wertungssatz_id is None and "zurückgesetzt" in hinweise[0]
    assert [x.name for x in sz.regeln(session, v).klassen] == ["Herren 31-40"]
    # ausdrücklich gewählte unpassende Wertung: Fehler
    with pytest.raises(sz.SatzFehler, match="passt nicht"):
        sz.regeln_setzen(session, v, mini.id, ws(session).id, 20.0)
    # passende Wertung; Klasse ohne Eintrag ergibt einen Hinweis
    eigene, _ = sz.wertungssatz_speichern(
        session, None, "Nur Herren Wertung", "gesamtzeit", None, None, mini.id,
        [stufe("Herren 31-40", "0:40:00", "0:43:00", "0:46:00", "20")],
    )  # fmt: skip
    # „Strecke 1“ (20,0 km) bleibt, auch wenn der Platz im Satz „20 km“ heißt
    assert sz.regeln_setzen(session, v, mini.id, eigene.id, 20.0) == []
    assert [(x.name, x.laenge_km) for x in strecken.strecken_von(session, v)] == [
        ("Strecke 1", 20.0)
    ]
    sz.klassensatz_speichern(session, mini, "Nur Herren",
                             [zeile("Herren 31-40", "m", 18, 99), zeile("Damen", "w", 18, 99)])  # fmt: skip
    hinweise = sz.regeln_setzen(session, v, mini.id, eigene.id, 20.0)
    assert hinweise == ["Ohne Wertungseintrag (keine Stufe möglich): Damen"]
    assert [w.name for w in sz.passende_wertungssaetze(session, mini.id)] == ["Nur Herren Wertung"]


def test_einfrieren_ab_erstem_ereignis(session):
    v = veranstaltung_mit_fahrer(session)
    eigener = dra(session)
    sz.regeln_setzen(session, v, eigener.id, ws(session).id, 20.0)
    assert sz.regeln(session, v).eingefroren_ms is None
    ereignis(session, v, "START", startnummer=1, zeit_ms=T0)
    k.status_setzen(session, v, 1, "OK", "Test")  # Admin-Ereignis friert ein
    assert sz.regeln(session, v).eingefroren_ms is not None
    vorher = k.ergebnis(session, v).zeile(1)
    ereignis(session, v, "ZIEL", startnummer=1, zeit_ms=T0 + 35 * MIN)
    stufe_vorher = k.ergebnis(session, v).zeile(1)
    # Sätze ändern: Klasse umbenennen, Gold-Schwelle verschärfen → wirkt nicht
    zeilen = sz.klassen_zeilen(eigener)
    for z in zeilen:
        if z["name"] == "Herren 31-40":
            z["alter_von"], z["alter_bis"] = 31, 39
    zeilen.append(zeile("Herren 40", "m", 40, 40))
    sz.klassensatz_speichern(session, eigener, "DRA 2026", zeilen)
    wz = sz.wertung_zeilen(ws(session))
    for z in wz:
        if z["klasse"] == "Herren 31-40":
            z["gold"] = "0:20:00"
    sz.wertungssatz_speichern(session, ws(session), "DRA A.1 flach", "gesamtzeit", None, None,
                              dra(session).id, wz)  # fmt: skip
    nachher = k.ergebnis(session, v).zeile(1)
    assert (
        (nachher.klasse, nachher.stufe)
        == (stufe_vorher.klasse, stufe_vorher.stufe)
        == ("Herren 31-40", "GOLD")
    )
    assert vorher.klasse == nachher.klasse
    anderer = sz.kopieren(session, dra(session))
    with pytest.raises(sz.SatzFehler, match="eingefroren"):
        sz.regeln_setzen(session, v, anderer.id, None, 20.0)
    # neue Veranstaltung ohne Ereignisse sieht die Änderungen
    w = k.veranstaltung_anlegen(
        session, "Neu", date(2026, 11, 1), aktivieren=False, klassensatz_id=eigener.id
    )
    assert "Herren 40" in [x.name for x in sz.regeln(session, w).klassen]


def test_wertung_nach_einfrieren_bewusst_umstellbar(session):
    v = veranstaltung_mit_fahrer(session)
    ereignis(session, v, "START", startnummer=1, zeit_ms=T0)
    sz.einfrieren_wenn_ereignisse(session, v)
    session.commit()
    sz.regeln_setzen(session, v, v.klassensatz_id, ws(session).id, 20.0)
    r = sz.regeln(session, v)
    assert r.eingefroren_ms and r.wertungssatz == "DRA A.1 flach" and r.wertung is not None
    sz.regeln_setzen(session, v, v.klassensatz_id, None, None)
    assert sz.regeln(session, v).wertung is None


def test_migration_bestehender_veranstaltungen(session):
    v = Veranstaltung(name="Alt", datum=date(2026, 10, 10), aktiv=True,
                      wertung="radsportabzeichen-a2-berg-strasse.toml", strecke_km=4.0)  # fmt: skip
    leer = Veranstaltung(name="Ohne Zeiten", datum=date(2026, 11, 1), aktiv=False)
    session.add_all([v, leer])
    session.commit()
    ereignis(session, v, "START", startnummer=1, zeit_ms=T0)
    sz.veranstaltungen_zuordnen(session)
    assert (
        v.klassensatz_id == dra(session).id
        and v.wertungssatz_id == ws(session, "DRA A.2 Berg Straße").id
    )
    assert v.regeln_json is not None and leer.regeln_json is None
    assert leer.klassensatz_id == dra(session).id
    assert session.scalars(
        select(Veranstaltung).where(Veranstaltung.regeln_json.is_not(None))
    ).all() == [v]


def test_einfrieren_uebernimmt_keine_unpassende_wertung(session):
    v = veranstaltung_mit_fahrer(session)
    mini, _ = sz.klassensatz_speichern(session, None, "Mini", [zeile("Herren 31-40", "m", 18, 99)])
    v.klassensatz_id, v.wertungssatz_id = mini.id, ws(session).id  # an der Prüfung vorbei gesetzt
    session.commit()
    ereignis(session, v, "START", startnummer=1, zeit_ms=T0)
    sz.einfrieren_wenn_ereignisse(session, v)
    r = sz.regeln(session, v)
    assert r.eingefroren_ms and r.wertung is None and r.klassensatz == "Mini"


def test_klassensatz_eines_benutzten_wertungssatzes_nicht_wechselbar(session):
    v = veranstaltung_mit_fahrer(session)
    sz.regeln_setzen(session, v, dra(session).id, ws(session).id, 20.0)
    kopie = sz.kopieren(session, dra(session))
    w = ws(session)
    with pytest.raises(sz.SatzFehler, match="nicht gewechselt"):
        sz.wertungssatz_speichern(
            session, w, w.name, w.art, None, None, kopie.id, sz.wertung_zeilen(w)
        )
