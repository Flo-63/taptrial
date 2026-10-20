import tomllib
from dataclasses import dataclass
from datetime import date

import pytest

from app.einstellungen import STANDARD_KLASSEN_DATEI
from app.klassen import (
    OFFENE_KLASSE,
    KeineKlasseFehler,
    KlassenConfigFehler,
    alter_am_stichtag,
    klassifizieren,
    lade_klassen,
    pruefe_klassen,
)

STICHTAG = date(2026, 12, 31)


@dataclass
class T:
    geschlecht: str
    geburtsjahr: int


def _roh_klassen():
    with open(STANDARD_KLASSEN_DATEI, "rb") as f:
        return tomllib.load(f)["klasse"]


def _grenzfaelle():
    for k in _roh_klassen():
        for alter, wo in ((k["alter_von"], "erstes"), (k["alter_bis"], "letztes")):
            yield pytest.param(
                k["geschlecht"], STICHTAG.year - alter, k["name"], id=f"{k['name']}-{wo}"
            )


@pytest.fixture(scope="module")
def klassen():
    return lade_klassen(STANDARD_KLASSEN_DATEI)


@pytest.mark.parametrize(("geschlecht", "jahrgang", "erwartet"), list(_grenzfaelle()))
def test_klassengrenzen(klassen, geschlecht, jahrgang, erwartet):
    assert klassifizieren(T(geschlecht, jahrgang), STICHTAG, klassen) == erwartet


def test_nachbarjahre_wechseln_klasse(klassen):
    assert klassifizieren(T("m", 2026 - 30), STICHTAG, klassen) == "Herren bis 30"
    assert klassifizieren(T("m", 2026 - 31), STICHTAG, klassen) == "Herren 31-40"
    assert klassifizieren(T("w", 2026 - 17), STICHTAG, klassen) == "Jugend weiblich"
    assert klassifizieren(T("w", 2026 - 18), STICHTAG, klassen) == "Damen bis 30"


def test_jahrgangsprinzip_unabhaengig_vom_tag(klassen):
    # Nur das Jahr des Stichtags zählt
    assert alter_am_stichtag(1990, date(2026, 1, 1)) == 36
    assert alter_am_stichtag(1990, date(2026, 12, 31)) == 36


def test_divers_ohne_eigene_klassen_wird_offen(klassen):
    # Eigene Wertung "offen", da die Konfiguration keine d-Klassen enthält
    assert klassifizieren(T("d", 1980), STICHTAG, klassen) == OFFENE_KLASSE


def test_divers_mit_eigenen_klassen():
    klassen = pruefe_klassen(
        [{"name": "Divers alle", "geschlecht": "d", "alter_von": 18, "alter_bis": 99}]
    )
    assert klassifizieren(T("d", 1980), STICHTAG, klassen) == "Divers alle"
    with pytest.raises(KeineKlasseFehler):
        klassifizieren(T("d", 2020), STICHTAG, klassen)


def test_zu_jung_ist_fehler(klassen):
    with pytest.raises(KeineKlasseFehler, match="7"):
        klassifizieren(T("m", 2019), STICHTAG, klassen)


def test_zu_alt_ist_fehler(klassen):
    with pytest.raises(KeineKlasseFehler):
        klassifizieren(T("w", 1900), STICHTAG, klassen)


def test_ungueltiges_geschlecht_ist_fehler(klassen):
    with pytest.raises(KeineKlasseFehler):
        klassifizieren(T("x", 1980), STICHTAG, klassen)


@pytest.mark.parametrize(
    ("roh", "meldung"),
    [
        ([{"name": "A", "geschlecht": "m", "alter_von": 10}], "alter_bis"),
        ([{"name": "A", "geschlecht": "q", "alter_von": 1, "alter_bis": 2}], "Geschlecht"),
        ([{"name": "A", "geschlecht": "m", "alter_von": 5, "alter_bis": 2}], "alter_von"),
        ([{"name": "A", "geschlecht": "m", "alter_von": "x", "alter_bis": 2}], "Ganzzahl"),
        (
            [
                {"name": "A", "geschlecht": "m", "alter_von": 10, "alter_bis": 20},
                {"name": "B", "geschlecht": "m", "alter_von": 20, "alter_bis": 30},
            ],
            "überschneid",
        ),
        (
            [
                {"name": "A", "geschlecht": "m", "alter_von": 10, "alter_bis": 20},
                {"name": "A", "geschlecht": "w", "alter_von": 10, "alter_bis": 20},
            ],
            "doppelt",
        ),
        ([], "keine Klassen"),
    ],
)
def test_konfiguration_wird_validiert(roh, meldung):
    with pytest.raises(KlassenConfigFehler, match=meldung):
        pruefe_klassen(roh)


def test_gleiche_altersspanne_bei_anderem_geschlecht_ist_erlaubt():
    klassen = pruefe_klassen(
        [
            {"name": "A", "geschlecht": "m", "alter_von": 10, "alter_bis": 20},
            {"name": "B", "geschlecht": "w", "alter_von": 10, "alter_bis": 20},
        ]
    )
    assert len(klassen) == 2


def test_kaputte_toml_datei(tmp_path):
    p = tmp_path / "k.toml"
    p.write_text("[[klasse]\nname=")
    with pytest.raises(KlassenConfigFehler):
        lade_klassen(str(p))
