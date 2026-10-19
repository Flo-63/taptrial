from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db import erzeuge_engine, erzeuge_sessionmaker, tabellen_anlegen
from app.einstellungen import STANDARD_KLASSEN_DATEI
from app.klassen import lade_klassen
from app.models import Teilnehmer, Veranstaltung
from app.teilnehmer_import import ImportFehler, importiere_teilnehmer, pruefe_csv

FIXTURE = Path(__file__).resolve().parents[2] / "config" / "teilnehmer_beispiel.csv"
STICHTAG = date(2026, 12, 31)
KOPF = "startnummer;vorname;nachname;geschlecht;geburtsjahr;verein\n"


@pytest.fixture(scope="module")
def klassen():
    return lade_klassen(STANDARD_KLASSEN_DATEI)


@pytest.fixture
def session(tmp_path):
    engine = erzeuge_engine(str(tmp_path / "t.sqlite3"))
    tabellen_anlegen(engine)
    with erzeuge_sessionmaker(engine)() as s:
        yield s
    engine.dispose()


@pytest.fixture
def veranstaltung(session):
    v = Veranstaltung(name="Kottenforst 2026", datum=date(2026, 10, 10))
    session.add(v)
    session.commit()
    return v


def test_stichtag_default_ist_31_12(veranstaltung):
    assert veranstaltung.stichtag_alter == date(2026, 12, 31)


def test_import_der_50er_datei(session, veranstaltung, klassen):
    anzahl = importiere_teilnehmer(session, veranstaltung, FIXTURE.read_bytes(), klassen)
    assert anzahl == 50
    tn = session.scalars(select(Teilnehmer).order_by(Teilnehmer.startnummer)).all()
    assert [t.startnummer for t in tn] == list(range(1, 51))
    assert {t.geschlecht for t in tn} == {"m", "w", "d"}
    assert any(t.verein is None for t in tn)
    assert all(t.veranstaltung_id == veranstaltung.id for t in tn)


def test_gueltige_zeilen(klassen):
    csv = KOPF + "1;Anna;Muster;W;1990;RC Bonn\n2;Ben;Beispiel;m;1985;\n"
    zeilen, fehler = pruefe_csv(csv.encode(), STICHTAG, klassen)
    assert fehler == []
    assert zeilen[0] == {
        "startnummer": 1,
        "vorname": "Anna",
        "nachname": "Muster",
        "geschlecht": "w",
        "geburtsjahr": 1990,
        "verein": "RC Bonn",
        "abzeichen": False,
        "ausser_konkurrenz": False,
    }
    assert zeilen[1]["verein"] is None


def test_komma_als_trenner_und_bom_und_cp1252(klassen):
    csv = "﻿startnummer,vorname,nachname,geschlecht,geburtsjahr\n1,Jörg,Müller,m,1970\n"
    zeilen, fehler = pruefe_csv(csv.encode("utf-8"), STICHTAG, klassen)
    assert fehler == [] and zeilen[0]["vorname"] == "Jörg"
    csv2 = KOPF + "1;Jörg;Müller;m;1970;Rad-Sport Köln\n"
    zeilen, fehler = pruefe_csv(csv2.encode("cp1252"), STICHTAG, klassen)
    assert fehler == [] and zeilen[0]["nachname"] == "Müller"


@pytest.mark.parametrize(
    ("csv", "erwartet"),
    [
        (
            KOPF + "1;A;B;m;1990;\n1;C;D;w;1990;\n",
            "Zeile 3: Startnummer 1 doppelt (bereits in Zeile 2)",
        ),
        (KOPF + "1;A;B;m;2022;\n", "Zeile 2: keine Altersklasse für Geschlecht m, Alter 4"),
        (KOPF + "1;A;B;x;1990;\n", "Zeile 2: ungültiges Geschlecht „x“ (erlaubt: m, w, d)"),
        (KOPF + "abc;A;B;m;1990;\n", "Zeile 2: ungültige Startnummer „abc“"),
        (KOPF + "0;A;B;m;1990;\n", "Zeile 2: ungültige Startnummer „0“"),
        (KOPF + "1;;B;m;1990;\n", "Zeile 2: Vorname fehlt"),
        (KOPF + "1;A;B;m;neunzehn;\n", "Zeile 2: ungültiges Geburtsjahr „neunzehn“"),
        ("startnummer;vorname;nachname;geburtsjahr\n1;A;B;1990\n", "Spalte „geschlecht“ fehlt"),
        ("", "Datei ist leer"),
    ],
)
def test_fehlerfaelle(klassen, csv, erwartet):
    _, fehler = pruefe_csv(csv.encode(), STICHTAG, klassen)
    assert erwartet in fehler


def test_alle_fehler_werden_gesammelt(klassen):
    csv = KOPF + "1;A;B;x;1990;\n2;A;B;m;2024;\n2;A;B;m;1990;\n"
    _, fehler = pruefe_csv(csv.encode(), STICHTAG, klassen)
    assert len(fehler) == 3


def test_fehlerhafte_datei_importiert_nichts(session, veranstaltung, klassen):
    csv = KOPF + "1;A;B;m;1990;\n2;C;D;m;1990;\n2;E;F;w;1990;\n"
    with pytest.raises(ImportFehler) as e:
        importiere_teilnehmer(session, veranstaltung, csv.encode(), klassen)
    assert "Startnummer 2 doppelt" in e.value.fehler[0]
    assert session.scalars(select(Teilnehmer)).all() == []


def test_startnummer_bereits_vorhanden(session, veranstaltung, klassen):
    importiere_teilnehmer(session, veranstaltung, (KOPF + "5;A;B;m;1990;\n").encode(), klassen)
    with pytest.raises(ImportFehler) as e:
        importiere_teilnehmer(
            session, veranstaltung, (KOPF + "6;C;D;m;1990;\n5;E;F;w;1990;\n").encode(), klassen
        )
    assert e.value.fehler == ["Zeile 3: Startnummer 5 ist bereits vergeben"]
    assert len(session.scalars(select(Teilnehmer)).all()) == 1


def test_ersetzen_loescht_vorhandene_teilnehmer(session, veranstaltung, klassen):
    importiere_teilnehmer(session, veranstaltung, (KOPF + "5;A;B;m;1990;\n").encode(), klassen)
    importiere_teilnehmer(
        session, veranstaltung, (KOPF + "5;C;D;w;1991;\n").encode(), klassen, ersetzen=True
    )
    tn = session.scalars(select(Teilnehmer)).all()
    assert [(t.startnummer, t.vorname) for t in tn] == [(5, "C")]


def test_klassenzuordnung_der_50er_datei(klassen):
    zeilen, fehler = pruefe_csv(FIXTURE.read_bytes(), STICHTAG, klassen)
    assert fehler == [] and len(zeilen) == 50


# --- Merkmale „abzeichen“ und „ausser_konkurrenz“, Groß-/Kleinschreibung ----------------


KOPF_MERKMALE = (
    "startnummer;vorname;nachname;geschlecht;geburtsjahr;verein;abzeichen;ausser_konkurrenz\n"
)


@pytest.mark.parametrize(
    ("wert", "erwartet"),
    [("ja", True), ("JA", True), ("Ja", True), (" ja ", True), ("1", True), ("x", True),
     ("X", True), ("nein", False), ("NEIN", False), ("0", False), ("", False), ("  ", False)],
)  # fmt: skip
def test_merkmal_werte(klassen, wert, erwartet):
    csv = KOPF_MERKMALE + f"1;A;B;m;1990;;{wert};{wert}\n"
    zeilen, fehler = pruefe_csv(csv.encode(), STICHTAG, klassen)
    assert fehler == []
    assert zeilen[0]["abzeichen"] is erwartet and zeilen[0]["ausser_konkurrenz"] is erwartet


def test_merkmal_spalten_fehlen_bedeutet_nein(klassen):
    zeilen, fehler = pruefe_csv((KOPF + "1;A;B;m;1990;\n").encode(), STICHTAG, klassen)
    assert fehler == [] and not zeilen[0]["abzeichen"] and not zeilen[0]["ausser_konkurrenz"]


def test_merkmal_unbekannter_wert_ist_fehler(klassen):
    csv = KOPF_MERKMALE + "1;A;B;m;1990;;vielleicht;\n2;C;D;w;1990;;ja;jein\n"
    _, fehler = pruefe_csv(csv.encode(), STICHTAG, klassen)
    assert fehler == [
        "Zeile 2: ungültiger Wert „vielleicht“ in Spalte abzeichen (erlaubt: ja/nein, 1/0, x/leer)",
        "Zeile 3: ungültiger Wert „jein“ in Spalte ausser_konkurrenz (erlaubt: ja/nein, 1/0, x/leer)",
    ]


def test_spaltennamen_und_werte_ohne_gross_klein_und_leerzeichen(klassen):
    csv = (
        " StartNummer ;VORNAME; Nachname ;Geschlecht;GEBURTSJAHR;Verein;ABZEICHEN;Ausser_Konkurrenz\n"
        " 1 ; Anna ;Muster; W ; 1990 ; RC Bonn ; Ja ; X \n"
        "2;Ben;B;M;1985;;NEIN;0\n3;Kim;K;D;1979;;;\n"
    )
    zeilen, fehler = pruefe_csv(csv.encode(), STICHTAG, klassen)
    assert fehler == []
    assert [(z["startnummer"], z["geschlecht"]) for z in zeilen] == [(1, "w"), (2, "m"), (3, "d")]
    assert zeilen[0]["vorname"] == "Anna" and zeilen[0]["verein"] == "RC Bonn"
    assert zeilen[0]["abzeichen"] and zeilen[0]["ausser_konkurrenz"]
    assert not zeilen[1]["abzeichen"] and not zeilen[2]["ausser_konkurrenz"]


def test_50er_datei_hat_merkmale(session, veranstaltung, klassen):
    importiere_teilnehmer(session, veranstaltung, FIXTURE.read_bytes(), klassen)
    tn = session.scalars(select(Teilnehmer)).all()
    assert sum(t.abzeichen for t in tn) >= 10 and sum(t.ausser_konkurrenz for t in tn) == 2
