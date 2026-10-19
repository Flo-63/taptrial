"""Vorabprüfung der Dateien in config/wertungen/ (FRAGEN A32).

Der eigentliche Loader mit Validierung beim Start folgt in Phase 7 (KONZEPT 7a).
"""

import tomllib
from pathlib import Path

import pytest

from app.einstellungen import STANDARD_KLASSEN_DATEI

WERTUNGEN = sorted((Path(STANDARD_KLASSEN_DATEI).parent / "wertungen").glob("*.toml"))


def _sekunden(text: str) -> int:
    ergebnis = 0
    for teil in text.split(":"):
        ergebnis = ergebnis * 60 + int(teil)
    return ergebnis


def test_drei_dateien_vorhanden():
    assert [p.name for p in WERTUNGEN] == [
        "radsportabzeichen-a1-flach.toml",
        "radsportabzeichen-a2-berg-strasse.toml",
        "radsportabzeichen-a3-berg-gelaende.toml",
    ]


@pytest.mark.parametrize("pfad", WERTUNGEN, ids=lambda p: p.stem)
def test_wertungsdatei_gueltig(pfad):
    with open(STANDARD_KLASSEN_DATEI, "rb") as f:
        klassen = {k["name"] for k in tomllib.load(f)["klasse"]}
    with open(pfad, "rb") as f:
        daten = tomllib.load(f)
    assert daten["name"]
    assert daten["art"] in ("gesamtzeit", "pro_km")
    if daten["art"] == "pro_km":
        assert 0 < daten["strecke_km_min"] <= daten["strecke_km_max"]
    namen = [s["klasse"] for s in daten["stufe"]]
    assert len(namen) == len(set(namen))
    for stufe in daten["stufe"]:
        assert stufe["klasse"] in klassen
        assert _sekunden(stufe["gold"]) < _sekunden(stufe["silber"]) < _sekunden(stufe["bronze"])
        if daten["art"] == "gesamtzeit":
            assert stufe["strecke_km"] > 0


def test_konzept_regel_streckenlaenge_gesamtzeit():
    """FRAGEN A34: bei „gesamtzeit“ gilt Streckenlänge ≥ strecke_km (bis Phase 7 nur Doku)."""
    pfad = Path(STANDARD_KLASSEN_DATEI).parents[1] / "docs" / "KONZEPT.md"
    if not pfad.exists():
        pytest.skip("KONZEPT.md ist ein internes Arbeitsdokument (nicht im Export)")
    konzept = pfad.read_text("utf-8")
    assert "Länge der **eigenen Strecke** des Teilnehmers **≥** `strecke_km` der Stufe" in konzept
