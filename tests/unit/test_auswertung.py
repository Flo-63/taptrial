import itertools
from dataclasses import dataclass
from datetime import date

import pytest

from app.auswertung import (
    AuswertungsConfig,
    KonfliktArt,
    Status,
    auswerten,
    format_zehntel,
    zehntel,
)
from app.einstellungen import STANDARD_KLASSEN_DATEI
from app.klassen import lade_klassen

T0 = 1_760_000_000_000  # Epoch-ms
MIN = 60_000


@dataclass
class TN:
    startnummer: int
    vorname: str
    nachname: str
    geschlecht: str
    geburtsjahr: int
    verein: str | None = None
    abzeichen: bool = False
    ausser_konkurrenz: bool = False


@dataclass
class E:
    uuid: str
    art: str
    erstellt_ms: int
    zeit_ms: int | None = None
    startnummer: int | None = None
    bezug_uuid: str | None = None
    wert: str | None = None
    geraet: str = "test"
    empfangen_ms: int = 0
    notiz: str | None = None
    delta_ms: int | None = None


_zaehler = itertools.count(1)


def _e(art, **kw):
    n = next(_zaehler)
    kw.setdefault("erstellt_ms", kw.get("zeit_ms") or T0 + n)
    return E(uuid=kw.pop("uuid", f"u{n}"), art=art, **kw)


def start(nr, t, **kw):
    return _e("START", startnummer=nr, zeit_ms=t, **kw)


def ziel(nr, t, **kw):
    return _e("ZIEL", startnummer=nr, zeit_ms=t, **kw)


TEILNEHMER = [
    TN(1, "Anna", "A", "w", 1990),  # Damen 31-40
    TN(2, "Ben", "B", "m", 1990),  # Herren 31-40
    TN(3, "Carl", "C", "m", 1991),  # Herren 31-40
    TN(4, "Dora", "D", "w", 2000),  # Damen bis 30
    TN(5, "Eli", "E", "d", 1980),  # Offen
]


@pytest.fixture(scope="module")
def config():
    return AuswertungsConfig(
        klassen=lade_klassen(STANDARD_KLASSEN_DATEI), stichtag=date(2026, 12, 31)
    )


def zeile(ergebnis, nr):
    return next(z for z in ergebnis.zeilen if z.startnummer == nr)


def konfliktarten(ergebnis):
    return sorted(k.art for k in ergebnis.konflikte)


# --- Grundlagen -----------------------------------------------------------


def test_fahrzeit_und_status(config):
    ev = [start(1, T0), ziel(1, T0 + 20 * MIN + 12_345)]
    erg = auswerten(TEILNEHMER, ev, config)
    z = zeile(erg, 1)
    assert z.status == Status.OK
    assert z.fahrzeit_ms == 20 * MIN + 12_345
    assert z.fahrzeit_zehntel == (20 * MIN + 12_345) // 100
    assert z.klasse == "Damen 31-40"
    assert zeile(erg, 2).status == Status.OFFEN
    assert zeile(erg, 5).klasse == "Offen"


def test_rundung_schneidet_ab():
    # E4: 12:34,567 -> 12:34,5
    ms = 12 * MIN + 34_567
    assert zehntel(ms) == 7545
    assert format_zehntel(zehntel(ms)) == "12:34,5"
    assert format_zehntel(zehntel(12 * MIN + 34_599)) == "12:34,5"
    assert format_zehntel(zehntel(3_723_400)) == "1:02:03,4"
    assert format_zehntel(zehntel(9_999)) == "0:09,9"


def test_unvollstaendig(config):
    ev = [start(1, T0), ziel(2, T0 + 5 * MIN)]
    erg = auswerten(TEILNEHMER, ev, config)
    assert zeile(erg, 1).status == Status.UNVOLLSTAENDIG
    assert zeile(erg, 2).status == Status.UNVOLLSTAENDIG
    assert zeile(erg, 1).fahrzeit_ms is None


# --- Korrekturen ----------------------------------------------------------


def test_storno(config):
    s = start(1, T0)
    z1 = ziel(1, T0 + 10 * MIN)
    z2 = ziel(1, T0 + 11 * MIN)
    storno = _e("STORNO", bezug_uuid=z1.uuid, erstellt_ms=T0 + 12 * MIN)
    erg = auswerten(TEILNEHMER, [s, z1, z2, storno], config)
    assert zeile(erg, 1).fahrzeit_ms == 11 * MIN
    assert erg.konflikte == []


def test_storno_eines_stornos_stellt_wieder_her(config):
    s = start(1, T0)
    z = ziel(1, T0 + 10 * MIN)
    st1 = _e("STORNO", bezug_uuid=z.uuid, erstellt_ms=T0 + 11 * MIN)
    st2 = _e("STORNO", bezug_uuid=st1.uuid, erstellt_ms=T0 + 12 * MIN)
    erg = auswerten(TEILNEHMER, [s, z, st1, st2], config)
    assert zeile(erg, 1).fahrzeit_ms == 10 * MIN


def test_umbuchen(config):
    ev = [start(1, T0), start(2, T0 + MIN)]
    z = ziel(1, T0 + 12 * MIN)  # falsch angetippt, war Nr. 2
    um = _e("ZUORDNUNG", bezug_uuid=z.uuid, startnummer=2, erstellt_ms=T0 + 13 * MIN)
    erg = auswerten(TEILNEHMER, [*ev, z, um], config)
    assert zeile(erg, 2).fahrzeit_ms == 11 * MIN
    assert zeile(erg, 1).status == Status.UNVOLLSTAENDIG


def test_letzte_zuordnung_gewinnt_und_storno_der_zuordnung(config):
    ev = [start(1, T0), start(2, T0 + MIN), start(3, T0 + 2 * MIN)]
    z = ziel(None, T0 + 12 * MIN)
    a = _e("ZUORDNUNG", bezug_uuid=z.uuid, startnummer=2, erstellt_ms=T0 + 13 * MIN)
    b = _e("ZUORDNUNG", bezug_uuid=z.uuid, startnummer=3, erstellt_ms=T0 + 14 * MIN)
    erg = auswerten(TEILNEHMER, [*ev, z, a, b], config)
    assert zeile(erg, 3).fahrzeit_ms == 10 * MIN
    storno_b = _e("STORNO", bezug_uuid=b.uuid, erstellt_ms=T0 + 15 * MIN)
    erg = auswerten(TEILNEHMER, [*ev, z, a, b, storno_b], config)
    assert zeile(erg, 2).fahrzeit_ms == 11 * MIN
    assert zeile(erg, 3).status == Status.UNVOLLSTAENDIG


def test_reihenfolge_nach_erstellt_ms_nicht_listenreihenfolge(config):
    z = ziel(None, T0 + 12 * MIN)
    spaet = _e("ZUORDNUNG", bezug_uuid=z.uuid, startnummer=2, erstellt_ms=T0 + 20 * MIN)
    frueh = _e("ZUORDNUNG", bezug_uuid=z.uuid, startnummer=1, erstellt_ms=T0 + 13 * MIN)
    ev = [start(1, T0), start(2, T0 + MIN), z, spaet, frueh]
    assert zeile(auswerten(TEILNEHMER, ev, config), 2).fahrzeit_ms == 11 * MIN


def test_tauschen(config):
    s1, s2 = start(1, T0), start(2, T0 + MIN)
    z1 = ziel(1, T0 + 10 * MIN)  # eigentlich Nr. 2
    z2 = ziel(2, T0 + 10 * MIN + 500)  # eigentlich Nr. 1
    t1 = _e("ZUORDNUNG", bezug_uuid=z1.uuid, startnummer=2, erstellt_ms=T0 + 20 * MIN)
    t2 = _e("ZUORDNUNG", bezug_uuid=z2.uuid, startnummer=1, erstellt_ms=T0 + 20 * MIN)
    erg = auswerten(TEILNEHMER, [s1, s2, z1, z2, t1, t2], config)
    assert zeile(erg, 1).fahrzeit_ms == 10 * MIN + 500
    assert zeile(erg, 2).fahrzeit_ms == 9 * MIN
    assert erg.konflikte == []


def test_manuelle_zielzeit(config):
    ev = [
        start(1, T0),
        _e("MANUELLE_ZEIT", startnummer=1, zeit_ms=T0 + 15 * MIN, notiz="Handstoppuhr"),
    ]
    z = zeile(auswerten(TEILNEHMER, ev, config), 1)
    assert z.fahrzeit_ms == 15 * MIN
    assert z.manuell is True
    assert z.status == Status.OK


def test_manuelle_zeit_ersetzt_gestempelte_ohne_konflikt(config):
    ev = [
        start(1, T0),
        ziel(1, T0 + 14 * MIN),
        _e("MANUELLE_ZEIT", startnummer=1, zeit_ms=T0 + 15 * MIN, notiz="Video"),
    ]
    erg = auswerten(TEILNEHMER, ev, config)
    assert zeile(erg, 1).fahrzeit_ms == 15 * MIN
    assert erg.konflikte == []


def test_manuelle_startzeit(config):
    ev = [
        _e("MANUELLE_ZEIT", startnummer=1, zeit_ms=T0, wert="START", notiz="vergessen"),
        ziel(1, T0 + 15 * MIN),
    ]
    z = zeile(auswerten(TEILNEHMER, ev, config), 1)
    assert z.fahrzeit_ms == 15 * MIN and z.manuell


@pytest.mark.parametrize("wert", ["DNS", "DNF", "DSQ"])
def test_status_ereignis(config, wert):
    ev = [start(1, T0), ziel(1, T0 + 10 * MIN), _e("STATUS", startnummer=1, wert=wert)]
    z = zeile(auswerten(TEILNEHMER, ev, config), 1)
    assert z.status == Status(wert)
    assert z.rang_gesamt is None and z.rang_klasse is None


def test_status_ok_hebt_dnf_auf(config):
    dnf = _e("STATUS", startnummer=1, wert="DNF", erstellt_ms=T0 + 1)
    ok = _e("STATUS", startnummer=1, wert="OK", erstellt_ms=T0 + 2)
    ev = [start(1, T0), ziel(1, T0 + 10 * MIN), ok, dnf]
    assert zeile(auswerten(TEILNEHMER, ev, config), 1).status == Status.OK


# --- Konflikte ------------------------------------------------------------


def test_konflikt_mehrere_starts(config):
    ev = [start(1, T0), start(1, T0 + MIN), ziel(1, T0 + 10 * MIN)]
    erg = auswerten(TEILNEHMER, ev, config)
    assert konfliktarten(erg) == [KonfliktArt.MEHRERE_STARTS]
    assert erg.konflikte[0].startnummer == 1
    assert len(erg.konflikte[0].uuids) == 2
    assert zeile(erg, 1).status == Status.KONFLIKT
    assert zeile(erg, 1).fahrzeit_ms is None


def test_konflikt_mehrere_ziele(config):
    ev = [start(1, T0), ziel(1, T0 + 9 * MIN), ziel(1, T0 + 10 * MIN)]
    erg = auswerten(TEILNEHMER, ev, config)
    assert konfliktarten(erg) == [KonfliktArt.MEHRERE_ZIELE]
    assert zeile(erg, 1).status == Status.KONFLIKT


def test_konflikt_ziel_vor_start(config):
    ev = [start(1, T0 + 10 * MIN), ziel(1, T0 + 5 * MIN)]
    erg = auswerten(TEILNEHMER, ev, config)
    assert konfliktarten(erg) == [KonfliktArt.ZIEL_VOR_START]
    assert zeile(erg, 1).fahrzeit_ms is None


def test_konflikt_unplausible_fahrzeit(config):
    ev = []
    for nr, dauer in ((1, 20), (2, 21), (3, 22), (4, 9)):  # 9 < 50 % von Median 20,5
        ev += [start(nr, T0 + nr * MIN), ziel(nr, T0 + nr * MIN + dauer * MIN)]
    ev += [start(5, T0 + 5 * MIN), ziel(5, T0 + 5 * MIN + 45 * MIN)]  # > 200 %
    erg = auswerten(TEILNEHMER, ev, config)
    unplausibel = [k for k in erg.konflikte if k.art == KonfliktArt.UNPLAUSIBEL]
    assert sorted(k.startnummer for k in unplausibel) == [4, 5]
    # unplausibel ist nur ein Hinweis: Zeit bleibt gewertet
    assert zeile(erg, 4).status == Status.OK and zeile(erg, 4).rang_gesamt == 1


def test_plausibilitaetsgrenzen_konfigurierbar(config):
    from dataclasses import replace

    ev = []
    for nr, dauer in ((1, 20), (2, 20), (3, 26)):
        ev += [start(nr, T0), ziel(nr, T0 + dauer * MIN)]
    assert auswerten(TEILNEHMER, ev, config).konflikte == []
    eng = replace(config, plausibel_max_prozent=120)
    assert konfliktarten(auswerten(TEILNEHMER, ev, eng)) == [KonfliktArt.UNPLAUSIBEL]


def test_konflikt_zielstempel_ohne_zuordnung(config):
    z = ziel(None, T0 + 10 * MIN)
    erg = auswerten(TEILNEHMER, [start(1, T0), z], config)
    assert konfliktarten(erg) == [KonfliktArt.OHNE_ZUORDNUNG]
    assert erg.konflikte[0].uuids == [z.uuid]
    assert [o.uuid for o in erg.offene_ziele] == [z.uuid]


def test_nachtraegliche_zuordnung_loest_konflikt(config):
    z = ziel(None, T0 + 10 * MIN)
    zu = _e("ZUORDNUNG", bezug_uuid=z.uuid, startnummer=1, erstellt_ms=T0 + 11 * MIN)
    erg = auswerten(TEILNEHMER, [start(1, T0), z, zu], config)
    assert erg.konflikte == [] and erg.offene_ziele == []
    assert zeile(erg, 1).fahrzeit_ms == 10 * MIN


def test_konflikt_unbekannte_startnummer(config):
    erg = auswerten(TEILNEHMER, [start(99, T0)], config)
    assert konfliktarten(erg) == [KonfliktArt.UNBEKANNTE_STARTNUMMER]


# --- Platzierung ----------------------------------------------------------


def test_gleichstand_gleicher_rang(config):
    ev = []
    # Nr. 2 und 3: gleiche Zehntel (10:00,04 und 10:00,09), Nr. 1 schneller als beide
    for nr, ms in ((1, 9 * MIN), (2, 10 * MIN + 40), (3, 10 * MIN + 90), (4, 11 * MIN)):
        ev += [start(nr, T0), ziel(nr, T0 + ms)]
    erg = auswerten(TEILNEHMER, ev, config)
    assert [zeile(erg, n).rang_gesamt for n in (1, 2, 3, 4)] == [1, 2, 2, 4]
    assert zeile(erg, 2).rang_klasse == 1 and zeile(erg, 3).rang_klasse == 1


def test_rang_je_klasse(config):
    ev = []
    for nr, ms in ((1, 12 * MIN), (2, 10 * MIN), (3, 11 * MIN), (4, 13 * MIN)):
        ev += [start(nr, T0), ziel(nr, T0 + ms)]
    erg = auswerten(TEILNEHMER, ev, config)
    assert zeile(erg, 2).rang_klasse == 1 and zeile(erg, 3).rang_klasse == 2
    assert zeile(erg, 1).rang_klasse == 1 and zeile(erg, 4).rang_klasse == 1
    assert zeile(erg, 1).rang_gesamt == 3


def test_sortierung_der_zeilen(config):
    ev = [start(3, T0), ziel(3, T0 + 10 * MIN), start(1, T0), ziel(1, T0 + 11 * MIN)]
    ev.append(_e("STATUS", startnummer=2, wert="DNS"))
    erg = auswerten(TEILNEHMER, ev, config)
    assert [z.startnummer for z in erg.zeilen][:2] == [3, 1]
    assert erg.zeilen[-1].status == Status.DNS


def test_zielstempel_mit_effektiver_nummer(config):
    z1 = ziel(1, T0 + 10 * MIN)
    z2 = ziel(None, T0 + 11 * MIN)
    z3 = ziel(2, T0 + 12 * MIN)
    zu = _e("ZUORDNUNG", bezug_uuid=z2.uuid, startnummer=3, erstellt_ms=T0 + 13 * MIN)
    st = _e("STORNO", bezug_uuid=z3.uuid, erstellt_ms=T0 + 14 * MIN)
    erg = auswerten(TEILNEHMER, [start(1, T0), z1, z2, z3, zu, st], config)
    assert [(s.uuid, s.startnummer) for s in erg.zielstempel] == [(z1.uuid, 1), (z2.uuid, 3)]


# --- DSQ zweistufig (Feedback Gate B) --------------------------------------


def test_dsq_vorlaeufig_behaelt_zeit_und_rang(config):
    ev = [start(1, T0), ziel(1, T0 + 10 * MIN), start(3, T0), ziel(3, T0 + 11 * MIN)]
    ev.append(_e("STATUS", startnummer=1, wert="DSQ_VORLAEUFIG", notiz="Windschatten"))
    erg = auswerten(TEILNEHMER, ev, config)
    z = zeile(erg, 1)
    assert z.status == Status.OK and z.rang_gesamt == 1 and z.fahrzeit_ms == 10 * MIN
    assert z.dsq_vorlaeufig == "Windschatten"
    assert zeile(erg, 3).dsq_vorlaeufig is None and zeile(erg, 3).rang_gesamt == 2


def test_dsq_vorlaeufig_dann_bestaetigt(config):
    ev = [start(1, T0), ziel(1, T0 + 10 * MIN)]
    ev.append(_e("STATUS", startnummer=1, wert="DSQ_VORLAEUFIG", notiz="x", erstellt_ms=T0 + 1))
    ev.append(_e("STATUS", startnummer=1, wert="DSQ", notiz="bestätigt", erstellt_ms=T0 + 2))
    z = zeile(auswerten(TEILNEHMER, ev, config), 1)
    assert z.status == Status.DSQ and z.rang_gesamt is None and z.dsq_vorlaeufig is None


def test_dsq_vorlaeufig_verworfen(config):
    ev = [start(1, T0), ziel(1, T0 + 10 * MIN)]
    ev.append(_e("STATUS", startnummer=1, wert="DSQ_VORLAEUFIG", notiz="x", erstellt_ms=T0 + 1))
    ev.append(_e("STATUS", startnummer=1, wert="OK", notiz="verworfen", erstellt_ms=T0 + 2))
    z = zeile(auswerten(TEILNEHMER, ev, config), 1)
    assert z.status == Status.OK and z.dsq_vorlaeufig is None and z.rang_gesamt == 1


# --- außer Konkurrenz und Abzeichen-Merkmal ---------------------------------------


def test_ausser_konkurrenz_ohne_rang_und_ohne_einfluss(config):
    from dataclasses import replace

    tn = [replace(t, ausser_konkurrenz=(t.startnummer == 2)) for t in TEILNEHMER]
    ev = []
    for nr, ms in ((1, 12 * MIN), (2, 9 * MIN), (3, 11 * MIN), (4, 13 * MIN)):
        ev += [start(nr, T0), ziel(nr, T0 + ms)]
    erg = auswerten(tn, ev, config)
    ak = zeile(erg, 2)
    assert ak.status == Status.OK and ak.fahrzeit_ms == 9 * MIN and ak.ausser_konkurrenz
    assert ak.rang_gesamt is None and ak.rang_klasse is None
    # Ränge der anderen wie ohne Nr. 2
    assert [zeile(erg, n).rang_gesamt for n in (3, 1, 4)] == [1, 2, 3]
    assert zeile(erg, 3).rang_klasse == 1  # Herren 31-40: Nr. 2 zählt nicht
    # nach Zeit einsortiert (schnellste Zeit zuerst), aber ohne Rangnummer
    assert [z.startnummer for z in erg.zeilen][:4] == [2, 3, 1, 4]


def test_abzeichen_nur_mit_merkmal(config):
    from dataclasses import replace

    from app.einstellungen import STANDARD_KLASSEN_DATEI, STANDARD_WERTUNGEN
    from app.klassen import lade_klassen
    from app.wertung import lade_wertungen

    wertung = lade_wertungen(STANDARD_WERTUNGEN, lade_klassen(STANDARD_KLASSEN_DATEI))[
        "radsportabzeichen-a1-flach.toml"
    ]
    mit = replace(config, wertung=wertung, strecke_zehntel_km=200)
    tn = [replace(t, abzeichen=(t.startnummer == 2)) for t in TEILNEHMER]
    ev = []
    for nr in (2, 3):
        ev += [start(nr, T0), ziel(nr, T0 + 30 * MIN)]  # Herren 31-40, beide schnell genug
    erg = auswerten(tn, ev, mit)
    assert zeile(erg, 2).stufe == "GOLD" and zeile(erg, 2).abzeichen
    assert zeile(erg, 3).stufe is None  # will kein Abzeichen
