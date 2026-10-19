"""Simulation Beispielstrecke (Segmente): Zuordnung zur Abfolge, Gesamtzeit und Rang unberührt."""

import itertools
from datetime import date

import pytest

from app.auswertung import AuswertungsConfig, MesspunktDef, StreckenDef, auswerten
from app.einstellungen import STANDARD_KLASSEN_DATEI
from app.klassen import lade_klassen
from app.simulation import (
    BEISPIEL_MESSPUNKTE,
    SimEreignis,
    beispiel_teile,
    rennplan,
    rundkurs,
    strecken_dra_a1,
)

KLASSEN = lade_klassen(STANDARD_KLASSEN_DATEI)
ZUORDNUNG = strecken_dra_a1(KLASSEN)
_n = itertools.count()


def e(art, t, **kw):
    return SimEreignis(f"e{next(_n)}", art, t, zeit_ms=t, **kw)


def config(mit_segmenten=True):
    lang = frozenset(k for k, s in ZUORDNUNG.items() if s == "20 km")
    kurz = frozenset(k for k, s in ZUORDNUNG.items() if s == "5 km")
    mps = tuple(MesspunktDef(k, n) for k, n in BEISPIEL_MESSPUNKTE) if mit_segmenten else ()
    teile = beispiel_teile() if mit_segmenten else ()
    return AuswertungsConfig(klassen=KLASSEN, stichtag=date(2026, 12, 31), strecken=(
        StreckenDef("5 km", 50, kurz), StreckenDef("20 km", 200, lang, mps, teile)))  # fmt: skip


def lauf(seed, vergessen=0.0, doppelt=0.0):
    plan = rennplan(seed=seed)
    lange = {f.startnummer for f in plan.fahrer if ZUORDNUNG.get(_klasse(f, plan)) == "20 km"}
    durch = rundkurs(plan, lange, vergessen=vergessen, doppelt=doppelt, seed=seed)
    ev = [e("START", t, startnummer=nr) for nr, t in plan.start_ms.items()]
    ev += [e("ZIEL", t, startnummer=nr) for nr, t in plan.ziel_ms.items()]
    wahr = {}
    for d in durch:
        x = e("DURCHFAHRT", d.zeit_ms, startnummer=d.startnummer, wert=d.ort)
        ev.append(x)
        wahr[x.uuid] = d.position
    return plan, ev, wahr


@pytest.mark.parametrize("seed", [2026, 7, 99])
def test_ohne_fehler_jede_durchfahrt_an_ihrer_stelle(seed):
    plan, ev, wahr = lauf(seed)
    erg = auswerten(plan.fahrer, ev, config())
    belegt = [(d.uuid, d.position) for z in erg.zeilen for d in z.durchfahrten if d.uuid]
    assert len(belegt) == len(wahr) > 100
    assert all(wahr[u] == p for u, p in belegt)
    vollstaendig = [z for z in erg.zeilen if z.durchfahrten and z.fahrzeit_ms is not None]
    assert vollstaendig and all(
        all(x.zeit_ms is not None for x in z.segmentzeiten) for z in vollstaendig
    )
    for z in vollstaendig:  # Summe der Segmentzeiten = Fahrzeit
        assert sum(x.zeit_ms for x in z.segmentzeiten) == z.fahrzeit_ms


@pytest.mark.parametrize("seed", [2026, 7])
def test_vergessene_tipps_beruehren_gesamtzeit_und_rang_nicht(seed):
    plan, ev, _ = lauf(seed, vergessen=0.15)
    ohne = auswerten(plan.fahrer, [x for x in ev if x.art != "DURCHFAHRT"], config(False))
    mit = auswerten(plan.fahrer, ev, config())
    soll = {
        z.startnummer: (z.fahrzeit_ms, z.rang_gesamt, z.rang_klasse, z.status) for z in ohne.zeilen
    }
    assert {
        z.startnummer: (z.fahrzeit_ms, z.rang_gesamt, z.rang_klasse, z.status) for z in mit.zeilen
    } == soll
    assert [k.art for k in mit.konflikte] == [k.art for k in ohne.konflikte]
    assert not hasattr(mit, "durchfahrten_pruefen")  # Segmente erzeugen keine Fälle


def _klasse(f, plan):
    from app.klassen import klassifizieren

    return klassifizieren(f, date(plan.datum.year, 12, 31), KLASSEN)


@pytest.mark.parametrize("seed", [2026, 7, 99])
def test_durchfahrtsanzeige_stimmt_nach_vergessenen_klicks(seed):
    """Segmente 53: Mitten im Rennen, 20 % der Klicks vergessen – sobald die nächste Zeit eines
    Fahrers eingegangen ist, zeigt jeder Posten die richtige nächste Durchfahrt (verpasste zählen
    als vorbei)."""
    import random

    from app.segmente import innere_punkte

    plan, ev, wahr = lauf(seed)
    innen = innere_punkte(beispiel_teile())
    durch = [x for x in ev if x.art == "DURCHFAHRT"]
    zufall = random.Random(seed)
    vergessen = {x.uuid for x in durch if zufall.random() < 0.2}
    for anteil in (0.3, 0.5, 0.7):
        bis = sorted(x.zeit_ms for x in durch)[int(len(durch) * anteil)]
        erg = auswerten(
            plan.fahrer, [x for x in ev if x.zeit_ms <= bis and x.uuid not in vergessen], config()
        )
        geprueft = 0
        for nr in {x.startnummer for x in durch}:
            meine = sorted(
                (x for x in durch if x.startnummer == nr and x.zeit_ms <= bis),
                key=lambda x: x.zeit_ms,
            )
            if not meine or plan.ziel_ms.get(nr, bis + 1) <= bis or meine[-1].uuid in vergessen:
                continue  # noch keine Zeit, schon im Ziel, oder die nächste Zeit fehlt noch
            pos = wahr[meine[-1].uuid]
            erwartet = {
                mp: sum(1 for j in range(pos + 1) if innen[j] == mp) + 1
                for mp, _ in BEISPIEL_MESSPUNKTE
            }
            assert erg.zeile(nr).naechste_runde == erwartet, (nr, anteil)
            geprueft += 1
        assert geprueft >= 5
