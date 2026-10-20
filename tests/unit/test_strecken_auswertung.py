"""Auswertung mit mehreren Strecken: alles getrennt je Strecke."""

from datetime import date
from pathlib import Path

import pytest

from app.auswertung import AuswertungsConfig, KonfliktArt, Status, StreckenDef, auswerten
from app.einstellungen import STANDARD_KLASSEN_DATEI
from app.klassen import lade_klassen
from app.wertung import lade_wertungen
from tests.unit.test_auswertung import MIN, T0, TN, start, ziel

KLASSEN = lade_klassen(STANDARD_KLASSEN_DATEI)
WERTUNGEN = lade_wertungen(str(Path(STANDARD_KLASSEN_DATEI).parent / "wertungen"), KLASSEN)
A1 = WERTUNGEN["radsportabzeichen-a1-flach.toml"]
A2 = WERTUNGEN["radsportabzeichen-a2-berg-strasse.toml"]
JUGEND = frozenset(k.name for k in KLASSEN if k.alter_bis <= 17)
ERWACHSENE = frozenset(k.name for k in KLASSEN if k.alter_bis > 17) | {"Offen"}


def config(kurz=50, lang=200, wertung=None):
    return AuswertungsConfig(klassen=KLASSEN, stichtag=date(2026, 12, 31), wertung=wertung,
                             strecken=(StreckenDef("5 km", kurz, JUGEND),
                                       StreckenDef("20 km", lang, ERWACHSENE)))  # fmt: skip


# Nr. 1–3 Jugend (5 km), Nr. 4–7 Erwachsene (20 km)
TEILNEHMER = [
    TN(1, "Kim", "K", "m", 2012, abzeichen=True),  # Jungen 12-14
    TN(2, "Lea", "L", "w", 2013, abzeichen=True),  # Mädchen 12-14
    TN(3, "Max", "M", "m", 2011, ausser_konkurrenz=True),  # Jugend männlich, a. K.
    TN(4, "Anna", "A", "w", 1990, abzeichen=True),  # Damen 31-40
    TN(5, "Ben", "B", "m", 1985, abzeichen=True),  # Herren 41-50
    TN(6, "Cem", "C", "m", 1987),  # Herren 31-40
    TN(7, "Dan", "D", "d", 1980),  # Offen
]
FAHRZEIT = {
    1: 11 * MIN,
    2: 13 * MIN,
    3: 10 * MIN,
    4: 36 * MIN,
    5: 34 * MIN,
    6: 38 * MIN,
    7: 40 * MIN,
}


def ereignisse():
    liste = []
    for i, (nr, fz) in enumerate(FAHRZEIT.items()):
        t = T0 + i * MIN
        liste += [start(nr, t), ziel(nr, t + fz)]
    return liste


def test_raenge_getrennt_je_strecke():
    erg = auswerten(TEILNEHMER, ereignisse(), config())
    z = {x.startnummer: x for x in erg.zeilen}
    assert [s.name for s in erg.strecken] == ["5 km", "20 km"]
    assert {nr: z[nr].strecke for nr in z} == {1: 0, 2: 0, 3: 0, 4: 1, 5: 1, 6: 1, 7: 1}
    # 5 km: Nr. 3 (a. K.) ist am schnellsten, bekommt aber keinen Rang und verdrängt niemanden
    assert (z[1].rang_gesamt, z[2].rang_gesamt, z[3].rang_gesamt) == (1, 2, None)
    assert (z[1].rang_klasse, z[2].rang_klasse) == (1, 1)
    # 20 km: eigene Rangliste ab 1, Jugendzeiten zählen nicht mit
    assert [z[nr].rang_gesamt for nr in (5, 4, 6, 7)] == [1, 2, 3, 4]
    assert z[6].rang_klasse == 1 and z[7].klasse == "Offen" and z[7].rang_klasse == 1
    assert all(x.status == Status.OK for x in erg.zeilen)


def test_plausibilitaet_je_strecke():
    """Kurze 5-km-Zeiten sind auf der 20-km-Strecke kein Ausreißer (und umgekehrt)."""
    erg = auswerten(TEILNEHMER, ereignisse(), config())
    assert not [k for k in erg.konflikte if k.art == KonfliktArt.UNPLAUSIBEL]
    # zum Vergleich: alle auf einer Strecke → die Jugendzeiten wären unplausibel
    eine = AuswertungsConfig(klassen=KLASSEN, stichtag=date(2026, 12, 31))
    hinweise = [k.startnummer for k in auswerten(TEILNEHMER, ereignisse(), eine).konflikte
                if k.art == KonfliktArt.UNPLAUSIBEL]  # fmt: skip
    assert set(hinweise) >= {1, 3}


def test_stufe_gegen_eigene_strecke_gesamtzeit():
    erg = auswerten(TEILNEHMER, ereignisse(), config(wertung=A1))
    z = {x.startnummer: x for x in erg.zeilen}
    assert z[1].stufe is not None and z[2].stufe is not None  # 5 km gemessen 5,0 ≥ 5
    assert z[4].stufe is not None and z[5].stufe is not None  # 20 km gemessen 20,0 ≥ 20
    # Strecke zu kurz: 4,9 km bzw. 19,9 km → keine Stufe auf der jeweiligen Strecke
    erg = auswerten(TEILNEHMER, ereignisse(), config(kurz=49, wertung=A1))
    z = {x.startnummer: x for x in erg.zeilen}
    assert z[1].stufe is None and z[4].stufe is not None
    erg = auswerten(TEILNEHMER, ereignisse(), config(lang=199, wertung=A1))
    z = {x.startnummer: x for x in erg.zeilen}
    assert z[1].stufe is not None and z[4].stufe is None
    # ohne gemessene Länge auf einer Strecke: dort keine Stufe
    erg = auswerten(TEILNEHMER, ereignisse(), config(kurz=None, wertung=A1))
    assert {x.startnummer: x.stufe for x in erg.zeilen}[1] is None


def test_stufe_pro_km_mit_eigener_laenge():
    """pro_km: Zeit je km mit der Länge der eigenen Strecke (hier 4,0 und 5,0 km erlaubt)."""
    tn = [
        TN(1, "Anna", "A", "w", 1990, abzeichen=True),
        TN(2, "Ben", "B", "w", 1991, abzeichen=True),
    ]
    schwelle = A2.stufen["Damen 31-40"].gold_ms  # Gold-Zeit je km
    ev = [
        start(1, T0),
        ziel(1, T0 + 5 * schwelle),
        start(2, T0 + MIN),
        ziel(2, T0 + MIN + 5 * schwelle),
    ]
    cfg = AuswertungsConfig(klassen=KLASSEN, stichtag=date(2026, 12, 31), wertung=A2,
                            strecken=(StreckenDef("A", 50, frozenset({"Damen 31-40"})),
                                      StreckenDef("B", 40, frozenset({"Damen bis 30"}))))  # fmt: skip
    z = {x.startnummer: x for x in auswerten(tn, ev, cfg).zeilen}
    assert z[1].klasse == "Damen 31-40" and z[1].stufe == "GOLD"  # 5 × Schwelle auf 5,0 km
    assert z[2].klasse == "Damen 31-40"  # Jahrgang 1991 → 35 Jahre
    tn2 = [TN(2, "Ben", "B", "w", 2000, abzeichen=True)]  # Damen bis 30 auf 4,0 km
    ev2 = [start(2, T0), ziel(2, T0 + 5 * A2.stufen["Damen bis 30"].gold_ms)]
    assert (
        auswerten(tn2, ev2, cfg).zeilen[0].stufe != "GOLD"
    )  # 5 × Schwelle auf nur 4 km zu langsam


def test_ohne_strecken_wie_bisher():
    """Ohne Streckenangabe eine Strecke für alle (bisheriges Verhalten, strecke_zehntel_km)."""
    cfg = AuswertungsConfig(klassen=KLASSEN, stichtag=date(2026, 12, 31), wertung=A1,
                            strecke_zehntel_km=200)  # fmt: skip
    erg = auswerten(TEILNEHMER, ereignisse(), cfg)
    assert len(erg.strecken) == 1 and {z.strecke for z in erg.zeilen} == {0}
    z = {x.startnummer: x for x in erg.zeilen}
    assert z[3].rang_gesamt is None and z[5].rang_gesamt == 3  # gemeinsame Rangliste (1, 2, dann 5)


@pytest.mark.parametrize("klasse_fehlt", [True])
def test_klasse_ohne_strecke_landet_auf_erster(klasse_fehlt):
    cfg = AuswertungsConfig(klassen=KLASSEN, stichtag=date(2026, 12, 31),
                            strecken=(StreckenDef("A", 50, frozenset({"Damen 31-40"})),
                                      StreckenDef("B", 200, frozenset())))  # fmt: skip
    erg = auswerten([TN(5, "Ben", "B", "m", 1985)], [], cfg)
    assert erg.zeilen[0].strecke == 0
