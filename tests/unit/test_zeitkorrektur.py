"""Schnellkorrektur am Ziel: ZEITKORREKTUR-Ereignisse (Phase 14a)."""

from datetime import date

from app.auswertung import MAX_KORREKTUR_MS, AuswertungsConfig, auswerten, korrektur_text
from app.einstellungen import STANDARD_KLASSEN_DATEI
from app.klassen import lade_klassen
from tests.unit.test_auswertung import MIN, T0, TN, _e, start, ziel

CONFIG = AuswertungsConfig(
    klassen=lade_klassen(STANDARD_KLASSEN_DATEI), stichtag=date(2026, 12, 31)
)
TEILNEHMER = [TN(1, "Anna", "A", "w", 1990), TN(2, "Ben", "B", "m", 1985)]


def korrektur(bezug, delta, **kw):
    return _e("ZEITKORREKTUR", bezug_uuid=bezug, delta_ms=delta, **kw)


def zeile(erg, nr):
    return next(z for z in erg.zeilen if z.startnummer == nr)


def test_summe_der_deltas():
    z1 = ziel(1, T0 + 30 * MIN)
    ev = [
        start(1, T0),
        z1,
        korrektur(z1.uuid, 200),
        korrektur(z1.uuid, 200),
        korrektur(z1.uuid, -200),
    ]
    erg = auswerten(TEILNEHMER, ev, CONFIG)
    z = zeile(erg, 1)
    assert z.ziel_ms == T0 + 30 * MIN + 200 and z.fahrzeit_ms == 30 * MIN + 200
    assert z.korrektur_ms == 200 and z.korrigiert
    stempel = next(s for s in erg.zielstempel if s.uuid == z1.uuid)
    assert stempel.zeit_ms == T0 + 30 * MIN + 200 and stempel.korrektur_ms == 200
    assert korrektur_text(200) == "+0,2 s" and korrektur_text(-1400) == "−1,4 s"


def test_grenze_5000_ms():
    z1 = ziel(1, T0 + 30 * MIN)
    ev = [start(1, T0), z1] + [korrektur(z1.uuid, 200) for _ in range(30)]
    z = zeile(auswerten(TEILNEHMER, ev, CONFIG), 1)
    assert z.korrektur_ms == MAX_KORREKTUR_MS == 5000  # 25 wirken, 5 ohne Wirkung
    ev.append(korrektur(z1.uuid, -200))  # zurück geht wieder
    assert zeile(auswerten(TEILNEHMER, ev, CONFIG), 1).korrektur_ms == 4800


def test_korrektur_bleibt_nach_zuordnung_am_stempel():
    ohne = _e("ZIEL", zeit_ms=T0 + 31 * MIN)  # Zeit ohne Nummer
    ev = [start(1, T0), start(2, T0 + MIN), ohne, korrektur(ohne.uuid, -200)]
    erg = auswerten(TEILNEHMER, ev, CONFIG)
    assert erg.offene_ziele[0].zeit_ms == T0 + 31 * MIN - 200
    ev.append(_e("ZUORDNUNG", bezug_uuid=ohne.uuid, startnummer=1))
    assert zeile(auswerten(TEILNEHMER, ev, CONFIG), 1).fahrzeit_ms == 31 * MIN - 200
    ev.append(_e("ZUORDNUNG", bezug_uuid=ohne.uuid, startnummer=2))  # umbuchen
    erg = auswerten(TEILNEHMER, ev, CONFIG)
    assert zeile(erg, 2).fahrzeit_ms == 30 * MIN - 200 and zeile(erg, 2).korrigiert
    assert zeile(erg, 1).fahrzeit_ms is None and not zeile(erg, 1).korrigiert


def test_manuelle_zeit_ersetzt_korrektur():
    z1 = ziel(1, T0 + 30 * MIN)
    ev = [start(1, T0), z1, korrektur(z1.uuid, 400),
          _e("MANUELLE_ZEIT", startnummer=1, zeit_ms=T0 + 29 * MIN, notiz="Video")]  # fmt: skip
    z = zeile(auswerten(TEILNEHMER, ev, CONFIG), 1)
    assert z.fahrzeit_ms == 29 * MIN and not z.korrigiert and z.manuell


def test_ohne_wirkung_auf_start_unbekannt_und_storniert():
    s1, z1 = start(1, T0), ziel(1, T0 + 30 * MIN)
    k = korrektur(z1.uuid, 200)
    ev = [s1, z1, korrektur(s1.uuid, 200), korrektur("gibtsnicht", 200), k,
          _e("STORNO", bezug_uuid=k.uuid)]  # fmt: skip
    z = zeile(auswerten(TEILNEHMER, ev, CONFIG), 1)
    assert z.start_ms == T0 and z.fahrzeit_ms == 30 * MIN and not z.korrigiert


def test_alte_und_neue_schritte_gemischt():
    """Bis 10/2026 ±200 ms je Tipp, danach ±1000 ms: beides wird verrechnet, Grenze ±5000 ms."""
    z1 = ziel(1, T0 + 30 * MIN)
    ev = [start(1, T0), z1, korrektur(z1.uuid, 200)] + [korrektur(z1.uuid, 1000) for _ in range(5)]
    z = zeile(auswerten(TEILNEHMER, ev, CONFIG), 1)
    assert z.korrektur_ms == 4200  # die fünfte Sekunde hätte 5200 ergeben → ohne Wirkung
