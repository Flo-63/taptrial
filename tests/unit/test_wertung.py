"""Leistungswertung Deutsches Radsportabzeichen (KONZEPT 7a, Phase 7)."""

from pathlib import Path

import pytest

from app.einstellungen import STANDARD_KLASSEN_DATEI
from app.klassen import lade_klassen
from app.wertung import (
    WertungsConfigFehler,
    lade_wertungen,
    pruefe_wertung,
    stufe_berechnen,
    zeit_zu_ms,
)

VERZEICHNIS = Path(STANDARD_KLASSEN_DATEI).parent / "wertungen"
A1 = "radsportabzeichen-a1-flach.toml"
A2 = "radsportabzeichen-a2-berg-strasse.toml"
MIN = 60_000


@pytest.fixture(scope="module")
def klassen():
    return lade_klassen(STANDARD_KLASSEN_DATEI)


@pytest.fixture(scope="module")
def wertungen(klassen):
    return lade_wertungen(str(VERZEICHNIS), klassen)


def test_alle_drei_dateien_gueltig(wertungen):
    assert sorted(wertungen) == sorted(p.name for p in VERZEICHNIS.glob("*.toml"))
    a1 = wertungen[A1]
    assert a1.art == "gesamtzeit" and a1.name.startswith("Deutsches Radsportabzeichen A.1")
    herren = a1.stufen["Herren 31-40"]
    assert (herren.gold_ms, herren.silber_ms, herren.bronze_ms) == (38 * MIN, 43 * MIN, 46 * MIN)
    assert herren.strecke_zehntel_km == 200
    a2 = wertungen[A2]
    assert (a2.art, a2.strecke_min_zehntel_km, a2.strecke_max_zehntel_km) == ("pro_km", 30, 60)
    assert a2.stufen["Damen bis 30"].gold_ms == 3 * MIN


def test_zeitformat():
    assert zeit_zu_ms("0:34:00") == 34 * MIN
    assert zeit_zu_ms("1:07:00") == 67 * MIN
    assert zeit_zu_ms("2:45") == 165_000
    with pytest.raises(ValueError):
        zeit_zu_ms("abc")


def _roh(**ueber):
    roh = {"name": "Test", "art": "gesamtzeit",
           "stufe": [{"klasse": "Herren 31-40", "strecke_km": 20, "gold": "0:38:00",
                      "silber": "0:43:00", "bronze": "0:46:00"}]}  # fmt: skip
    roh.update(ueber)
    return roh


@pytest.mark.parametrize(
    ("roh", "meldung"),
    [
        (_roh(art="quatsch"), "art"),
        (_roh(stufe=[{**_roh()["stufe"][0], "klasse": "Gibt es nicht"}]), "Gibt es nicht"),
        (_roh(stufe=[{**_roh()["stufe"][0], "gold": "0:44:00"}]), "gold < silber < bronze"),
        (_roh(stufe=[_roh()["stufe"][0], _roh()["stufe"][0]]), "doppelt"),
        (
            _roh(stufe=[{k: v for k, v in _roh()["stufe"][0].items() if k != "strecke_km"}]),
            "strecke_km",
        ),
        (_roh(art="pro_km"), "strecke_km_min"),
        (_roh(stufe=[{**_roh()["stufe"][0], "bronze": "x"}]), "Zeit"),
        (_roh(name=""), "name"),
    ],
)
def test_validierung(klassen, roh, meldung):
    with pytest.raises(WertungsConfigFehler, match=meldung):
        pruefe_wertung("test.toml", roh, klassen)


def test_kaputte_datei_verhindert_laden(tmp_path, klassen):
    (tmp_path / "x.toml").write_text('name = "X"\nart = "gesamtzeit"\n[[stufe]]\nklasse = "Nix"\n')
    with pytest.raises(WertungsConfigFehler, match="x.toml"):
        lade_wertungen(str(tmp_path), klassen)


# --- gesamtzeit (A.1, Herren 31-40: Gold 38, Silber 43, Bronze 46 min auf 20 km) -----


@pytest.mark.parametrize(
    ("fahrzeit_ms", "erwartet"),
    [
        (38 * MIN, "GOLD"),  # genau auf der Schwelle
        (38 * MIN + 99, "GOLD"),  # 38:00,099 → abgeschnitten 38:00,0 (A33)
        (38 * MIN + 100, "SILBER"),  # 38:00,1
        (43 * MIN, "SILBER"),
        (43 * MIN + 100, "BRONZE"),
        (46 * MIN, "BRONZE"),
        (46 * MIN + 100, None),
    ],
)
def test_gesamtzeit_schwellen(wertungen, fahrzeit_ms, erwartet):
    assert stufe_berechnen(wertungen[A1], "Herren 31-40", fahrzeit_ms, 200) == erwartet


@pytest.mark.parametrize(("strecke", "erwartet"), [(199, None), (200, "GOLD"), (202, "GOLD")])
def test_gesamtzeit_streckenlaenge_mindestens(wertungen, strecke, erwartet):
    # A34: Streckenlänge der Veranstaltung >= strecke_km der Stufe, keine Toleranz nach unten
    assert stufe_berechnen(wertungen[A1], "Herren 31-40", 30 * MIN, strecke) == erwartet


# --- pro_km (A.2, Herren 31-40: Gold 2:45, Silber 3:00, Bronze 4:00 je km; 3–6 km) ---------


@pytest.mark.parametrize(
    ("fahrzeit_ms", "erwartet"),
    [
        (4 * 165_000, "GOLD"),  # 4 km genau 2:45/km
        (4 * 165_000 + 100, "SILBER"),
        (4 * 180_000, "SILBER"),
        (4 * 240_000, "BRONZE"),
        (4 * 240_000 + 100, None),
    ],
)
def test_pro_km_schwellen(wertungen, fahrzeit_ms, erwartet):
    assert stufe_berechnen(wertungen[A2], "Herren 31-40", fahrzeit_ms, 40) == erwartet


@pytest.mark.parametrize(
    ("strecke", "erwartet"), [(29, None), (30, "GOLD"), (60, "GOLD"), (61, None)]
)
def test_pro_km_streckengrenzen(wertungen, strecke, erwartet):
    zeit = strecke * 16_500  # 2:45 pro km bei strecke/10 km
    assert stufe_berechnen(wertungen[A2], "Herren 31-40", zeit, strecke) == erwartet


def test_pro_km_mit_nachkommastelle(wertungen):
    # 4,5 km in 12:22,5 → 2:45,0/km genau Gold; 0,1 s langsamer → Silber
    assert stufe_berechnen(wertungen[A2], "Herren 31-40", 742_500, 45) == "GOLD"
    assert stufe_berechnen(wertungen[A2], "Herren 31-40", 742_600, 45) == "SILBER"


def test_klasse_ohne_eintrag(wertungen):
    assert stufe_berechnen(wertungen[A2], "Jungen 8-11", 60_000, 40) is None
    assert stufe_berechnen(wertungen[A1], "Offen", 60_000, 200) is None
    assert stufe_berechnen(wertungen[A1], None, 60_000, 200) is None
