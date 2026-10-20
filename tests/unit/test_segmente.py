"""Segment-Modell: Streckendefinition, Abfolge, Prüfung, Migration, Zuordnung."""

import pytest

from app import segmente as sg
from app.segmente import START, ZIEL, Fahrt, SegmentDef, TeilDef, Tipp

MP = {"1": "P1", "2": "P2"}


def beispiel(vergleich=False):
    """Startsegment Start→P1 1,5 km; Rundenblock „Runde“ P1→P2 3,5 km, P2→P1 3,5 km ×2; Ziel P1→Ziel 0,5 km."""
    return (
        TeilDef((SegmentDef("Start→P1", START, "1", 1.5),)),
        TeilDef((SegmentDef("P1→P2", "1", "2", 3.5, vergleich), SegmentDef("P2→P1", "2", "1", 3.5)), 2, "Runde"),
        TeilDef((SegmentDef("P1→Ziel", "1", ZIEL, 0.5),)),
    )  # fmt: skip


def test_abfolge_und_erwartete_anzahl():
    t = beispiel()
    assert sg.abfolge_text(t, MP) == "Start · P1 · P2 · P1 · P2 · P1 · Ziel"
    assert sg.erwartete_anzahl(t) == {"1": 3, "2": 2}
    assert [d.label for d in sg.abfolge(t)] == [
        "Start→P1", "P1→P2 (1)", "P2→P1 (1)", "P1→P2 (2)", "P2→P1 (2)", "P1→Ziel"]  # fmt: skip
    assert sg.pruefen(t, MP) == [] and sg.mit_messpunkten(t)
    assert sg.aus_json(sg.nach_json(beispiel(True))) == beispiel(True)


@pytest.mark.parametrize(
    "teile, meldung",
    [
        ((TeilDef((SegmentDef("P1→Ziel", "1", ZIEL),)),), "muss am Start beginnen"),
        ((TeilDef((SegmentDef("Start→P1", START, "1"),)),), "muss am Ziel enden"),
        ((TeilDef((SegmentDef("Start→P1", START, "1"),)), TeilDef((SegmentDef("P2→Ziel", "2", ZIEL),))), "Lücke"),
        ((TeilDef((SegmentDef("Start→P1", START, "1"),)),
          TeilDef((SegmentDef("P1→P2", "1", "2"),), 2, "Runde"), TeilDef((SegmentDef("P2→Ziel", "2", ZIEL),))), "Lücke"),
        ((TeilDef((SegmentDef("Start→Ziel", START, ZIEL, vergleich=True),)),), "Vergleich"),
        ((TeilDef((SegmentDef("Start→P9", START, "9"),)), TeilDef((SegmentDef("P9→Ziel", "9", ZIEL),))), "unbekannter Messpunkt"),
        ((TeilDef((SegmentDef("Start→P1", START, "1"),)), TeilDef((SegmentDef("P1→Ziel", "1", ZIEL),))), "„P2“ kommt in keinem Segment vor"),
        ((TeilDef((SegmentDef("Start→P1", START, "1"),)), TeilDef((), 2, "Runde")), "keine Segmente"),
    ],
)  # fmt: skip
def test_pruefung(teile, meldung):
    assert any(meldung in f for f in sg.pruefen(teile, MP)), sg.pruefen(teile, MP)


def test_rundenblock_mit_einem_messpunkt():
    t = (TeilDef((SegmentDef("Start→P1", START, "1"),)), TeilDef((SegmentDef("Runde", "1", "1", 7.0, True),), 3, "Runde"),
         TeilDef((SegmentDef("P1→Ziel", "1", ZIEL),)))  # fmt: skip
    assert sg.pruefen(t, {"1": "P1"}) == []
    assert sg.abfolge_text(t, {"1": "P1"}) == "Start · P1 · P1 · P1 · P1 · Ziel"


def test_migration_aus_altem_modell_gleiche_punktfolge():
    alt = [("1", "Kreisel", "runde", 3.0), ("2", "Berg", "runde", 7.0), ("3", "Brücke", "auslauf", 31.0),
           ("4", "Abzweig", "anlauf", 1.0)]  # fmt: skip
    t = sg.aus_altem_modell(alt, 3)
    assert sg.punkte(t) == [START, "4", "1", "2", "1", "2", "1", "2", "3", ZIEL]
    assert sg.pruefen(t, {m[0]: m[1] for m in alt}) == []
    assert [x.name for x in t if x.block] == ["Runde"] and t[2].wdh == 2
    assert t[0].segmente[0].km == 1.0 and t[1].segmente[0].km == 2.0  # aus km-Positionen
    assert sg.punkte(sg.aus_altem_modell([("1", "K", "runde", None)], 3)) == [
        START,
        "1",
        "1",
        "1",
        ZIEL,
    ]
    assert sg.punkte(sg.aus_altem_modell([("1", "K", "anlauf", None)], 1)) == [START, "1", ZIEL]


def zeiten(*tipps):
    return {t.uuid: t.zeit_ms for t in tipps}


def test_zuordnung_und_segmentzeiten():
    t = beispiel()
    tipps = [
        Tipp(f"u{i}", mp, 100 + i * 10) for i, mp in enumerate(["1", "2", "1", "2", "1"], start=1)
    ]
    f = Fahrt(100, 160, tipps)
    z = sg.zuordnen(t, f)
    assert z.positionen == ["u1", "u2", "u3", "u4", "u5"] and z.fehlend_ab is None
    assert sg.segmentzeiten(t, f, z, zeiten(*tipps)) == [10, 10, 10, 10, 10, 10]


def test_fehlende_durchfahrt_an_der_reihenfolge_keine_weiteren_segmentzeiten():
    t = beispiel()
    tipps = [
        Tipp("a", "1", 110),
        Tipp("c", "1", 130),
        Tipp("d", "2", 140),
        Tipp("e", "1", 150),
    ]  # P2 (1) fehlt
    f = Fahrt(100, 170, tipps)
    z = sg.zuordnen(t, f)
    assert z.positionen == ["a", None, "c", "d", "e"] and z.fehlend_ab == 1
    assert sg.segmentzeiten(t, f, z, zeiten(*tipps)) == [10, None, None, None, None, None]
    # unterwegs (ohne Ziel): Lücken am Ende sind noch nicht „fehlend“
    teil = Fahrt(100, None, tipps[:1])
    assert sg.zuordnen(t, teil).fehlend_ab is None
    # im Ziel fehlt die letzte Durchfahrt
    f = Fahrt(
        100,
        170,
        [Tipp("a", "1", 110), Tipp("b", "2", 120), Tipp("c", "1", 130), Tipp("d", "2", 140)],
    )
    assert sg.zuordnen(t, f).fehlend_ab == 4


# --- Abstandsregel ---------------------------------------------------------------


def einzelrunde(wdh=4):
    """Start→P1 2 min, Rundenblock P1→P1 10 min × wdh, P1→Ziel 1 min (ein Messpunkt)."""
    return (TeilDef((SegmentDef("Start→P1", START, "1"),)), TeilDef((SegmentDef("Runde", "1", "1"),), wdh, "Runde"),
            TeilDef((SegmentDef("P1→Ziel", "1", ZIEL),)))  # fmt: skip


MED = {"0.0": 120, "1.0": 600, "2.0": 60}


def test_abstandsregel_bei_rundenblock_mit_einem_messpunkt():
    t = einzelrunde()
    # P1-Durchfahrten bei 120, 720, 1320, 1920, 2520; Ziel 2580. Die zweite (720) fehlt.
    tipps = [Tipp("a", "1", 120), Tipp("c", "1", 1320), Tipp("d", "1", 1920), Tipp("e", "1", 2520)]
    f = Fahrt(0, 2580, tipps)
    assert sg.zuordnen(t, f).positionen == ["a", "c", "d", "e", None]  # nur der Reihe nach: falsch
    z = sg.zuordnen(t, f, MED)
    assert z.positionen == ["a", None, "c", "d", "e"] and z.fehlend_ab == 1
    zeiten = {x.uuid: x.zeit_ms for x in tipps}
    assert sg.segmentzeiten(t, f, z, zeiten) == [120, None, None, None, None, None]
    # die erste fehlt: Start→P1 dauert dann 2 min + eine Runde
    z = sg.zuordnen(t, Fahrt(0, 2580, tipps[1:]), MED)
    assert z.positionen[:3] == [None, None, "c"]
    # die letzte fehlt: erst am Ziel erkannt, alle Segmente davor bleiben
    z = sg.zuordnen(t, Fahrt(0, 2580, [Tipp("a", "1", 120), Tipp("b", "1", 720), Tipp("c", "1", 1320),
                                        Tipp("d", "1", 1920)]), MED)  # fmt: skip
    assert z.positionen == ["a", "b", "c", "d", None] and z.fehlend_ab == 4


def test_abstandsregel_mit_tempo_des_fahrers():
    """Ein Fahrer mit doppelter Rundenzeit ist kein Fall für die Abstandsregel."""
    t = einzelrunde(2)
    tipps = [Tipp("a", "1", 240), Tipp("b", "1", 1440), Tipp("c", "1", 2640)]
    z = sg.zuordnen(t, Fahrt(0, 2760, tipps), MED)
    assert z.positionen == ["a", "b", "c"] and z.fehlend_ab is None


def test_mediane_ueber_alle_fahrer():
    t = einzelrunde(2)
    fahrten = [Fahrt(0, 1330 + d, [Tipp(f"{d}a", "1", 120 + d), Tipp(f"{d}b", "1", 720 + d), Tipp(f"{d}c", "1", 1270 + d)])
               for d in (0, 10, 20)]  # fmt: skip
    # je Segment (alle Wiederholungen zusammen): Runde 600 und 550 → 575
    assert sg.mediane(t, fahrten) == {"0.0": 130, "1.0": 575, "2.0": 60}


# --- Doppeltipp, Ziel-Toleranz, keine Fälle (Auswertung) -------------------------


def _auswerten(ereignisse, abschnitte=()):
    from datetime import date

    from app.auswertung import AuswertungsConfig, MesspunktDef, StreckenDef, auswerten
    from app.einstellungen import STANDARD_KLASSEN_DATEI
    from app.klassen import lade_klassen
    from tests.unit.test_auswertung import TN

    klassen = lade_klassen(STANDARD_KLASSEN_DATEI)
    alle = frozenset(k.name for k in klassen) | {"Offen"}
    cfg = AuswertungsConfig(klassen=klassen, stichtag=date(2026, 12, 31), strecken=(
        StreckenDef("Rund", 160, alle, (MesspunktDef("1", "P1"), MesspunktDef("2", "P2")), beispiel(True),
                    tuple(sg.abschnitte(beispiel(True))) + tuple(abschnitte)),))  # fmt: skip
    return auswerten([TN(1, "A", "B", "m", 1980), TN(2, "C", "D", "m", 1981)], ereignisse, cfg)


def _e(art, t, uuid, geraet="g", nr=1, **kw):
    from tests.unit.test_auswertung import E

    return E(uuid=uuid, art=art, erstellt_ms=t, zeit_ms=t, startnummer=nr, geraet=geraet, **kw)


MIN = 60_000


def rennen(*extra):
    """Nr. 1 und Nr. 2 (1 min später) fahren gleich schnell; Nr. 1: Durchfahrten d0 … d4."""
    folge = [("1", 3), ("2", 8), ("1", 13), ("2", 18), ("1", 23)]
    ev = []
    for nr, pre, ab in ((1, "d", 0), (2, "n", MIN)):
        ev += [_e("START", ab, f"s{nr}", nr=nr), _e("ZIEL", ab + 25 * MIN, f"z{nr}", nr=nr)]
        ev += [
            _e("DURCHFAHRT", ab + m * MIN, f"{pre}{i}", nr=nr, wert=mp)
            for i, (mp, m) in enumerate(folge)
        ]
    return ev + list(extra)


def test_doppeltipp_automatisch_verworfen_und_zuruecknehmbar():
    doppelt = _e("DURCHFAHRT", 3 * MIN + 4_000, "x", wert="1")
    erg = _auswerten(rennen(doppelt))
    z = erg.zeile(1)
    assert erg.nicht_gezaehlt == {"x": "Doppeltipp"} and [d.uuid for d in z.durchfahrten] == [
        "d0",
        "d1",
        "d2",
        "d3",
        "d4",
    ]
    assert all(x.zeit_ms is not None for x in z.segmentzeiten) and erg.konflikte == []
    # anderes Gerät: kein Doppeltipp (zwei Zeitnehmer am selben Messpunkt) → Tipp ohne Platz
    erg = _auswerten(rennen(_e("DURCHFAHRT", 3 * MIN + 4_000, "y", geraet="h", wert="1")))
    assert erg.nicht_gezaehlt.get("y") != "Doppeltipp"
    # über 10 s: kein Doppeltipp
    assert "x" not in _auswerten(
        rennen(_e("DURCHFAHRT", 3 * MIN + 11_000, "x", wert="1"))
    ).nicht_gezaehlt.keys() - {"x"}
    # zurückgenommen (RUNDE „GILT“): zählt wieder
    gilt = _e("RUNDE", 10**12, "r", bezug_uuid="x", wert="GILT", notiz="doch")
    erg = _auswerten(rennen(doppelt, gilt))
    assert "x" not in erg.nicht_gezaehlt


def test_tipp_um_die_zielzeit_zaehlt_nicht_und_keine_faelle():
    am_ziel = _e("DURCHFAHRT", 25 * MIN - 3_000, "a", wert="1")  # Messpunkt auf der Ziellinie
    erg = _auswerten(rennen(am_ziel))
    assert erg.nicht_gezaehlt == {"a": "am Ziel"}
    z = erg.zeile(1)
    assert z.fahrzeit_ms == 25 * MIN and z.segmentzeiten[-1].zeit_ms == 2 * MIN
    # vergessene Durchfahrten: keine Konflikte, Gesamtzeit und Rang unberührt
    erg = _auswerten([x for x in rennen() if x.uuid not in ("d1", "d2")])
    z = erg.zeile(1)
    assert erg.konflikte == [] and z.fahrzeit_ms == 25 * MIN and z.rang_gesamt == 1
    assert [d.uuid for d in z.durchfahrten] == ["d0", None, None, "d3", "d4"]  # Abstandsregel
    assert [x.zeit_ms for x in z.segmentzeiten] == [3 * MIN, None, None, None, None, None]


def test_vergleich_mit_vorheriger_durchfahrt():
    ev = [
        x if x.uuid != "d3" else _e("DURCHFAHRT", 17 * MIN, "d3", wert="2") for x in rennen()
    ]  # 4 statt 5 min
    z = _auswerten(ev).zeile(1)
    zweite = next(x for x in z.segmentzeiten if x.label == "P1→P2 (2)")
    assert zweite.zeit_ms == 4 * MIN and zweite.vergleich_ms == -MIN
    # Die Differenz gibt es bei jeder Wiederholung; ob sie erscheint, entscheidet das Monitor-Feld
    assert all(x.vergleich_ms is None for x in z.segmentzeiten if x.k == 1)


# --- Auswertungsabschnitte ------------------------------------------------------

RUNDE = sg.AbschnittDef("a1", "Runde", "1", "1", sg.WIEDERKEHREND, vergleich=True)


def test_segmente_sind_abschnitte_und_eigener_abschnitt_runde():
    t = beispiel(True)
    alle = sg.abschnitte(t, {"segmente": {"2.0": {"aus": True}},
                             "eigene": [{"id": "a1", "name": "Runde", "von": "1", "nach": "1", "art": "wiederkehrend",
                                         "vergleich": True}]})  # fmt: skip
    assert [(a.name, a.art, a.aus, a.vergleich) for a in alle] == [
        ("Start→P1", "einmalig", False, False), ("P1→P2", "wiederkehrend", False, True),
        ("P2→P1", "wiederkehrend", False, False), ("P1→Ziel", "einmalig", True, False),
        ("Runde", "wiederkehrend", False, True)]  # fmt: skip
    assert sg.paare(alle[-1], t) == [(1, 3), (3, 5)]  # P1 → nächstes P1, zweimal
    assert sg.paare(alle[1], t) == [(1, 2), (3, 4)]
    assert sg.abschnitt_pruefen(alle[-1], t, MP) == []


def test_abschnittszeiten_und_fehlende_durchfahrt():
    t = beispiel()
    tipps = [
        Tipp(f"u{i}", mp, 100 + i * 10) for i, mp in enumerate(["1", "2", "1", "2", "1"], start=1)
    ]
    zeiten = {x.uuid: x.zeit_ms for x in tipps}
    f = Fahrt(100, 160, tipps)
    assert sg.abschnittszeiten(RUNDE, t, f, sg.zuordnen(t, f), zeiten) == [20, 20]
    gesamt = sg.AbschnittDef("g", "Gesamt", START, ZIEL, sg.EINMALIG)
    assert sg.abschnittszeiten(gesamt, t, f, sg.zuordnen(t, f), zeiten) == [60]
    zweite = sg.AbschnittDef("z", "P1 (2) bis Ziel", "1", ZIEL, sg.EINMALIG, von_nr=2)
    assert sg.abschnittszeiten(zweite, t, f, sg.zuordnen(t, f), zeiten) == [30]
    # P2 (1) fehlt: erste Runde ohne Zeit, zweite auch (nach der fehlenden Durchfahrt), Gesamt bleibt
    f = Fahrt(100, 160, [x for x in tipps if x.uuid != "u2"])
    z = sg.zuordnen(t, f)
    assert sg.abschnittszeiten(RUNDE, t, f, z, zeiten) == [None, None]
    assert sg.abschnittszeiten(gesamt, t, f, z, zeiten) == [60]


@pytest.mark.parametrize("a, meldung", [
    (sg.AbschnittDef("x", "X", "9", "1"), "liegt nicht auf der Strecke"),
    (sg.AbschnittDef("x", "X", ZIEL, "1", sg.EINMALIG), "liegt nicht nach"),
    (sg.AbschnittDef("x", "X", "1", "2", sg.EINMALIG, von_nr=3), "liegt nicht nach"),
    (sg.AbschnittDef("x", "X", START, ZIEL, sg.EINMALIG, vergleich=True), "nur bei „jede Durchfahrt“"),
    (sg.AbschnittDef("x", " ", START, ZIEL, sg.EINMALIG), "ohne Namen"),
])  # fmt: skip
def test_abschnitt_pruefung(a, meldung):
    assert any(meldung in f for f in sg.abschnitt_pruefen(a, beispiel(), MP))


def test_abschnittszeiten_in_der_auswertung():
    """Strecke aus Schritt 7 mit Abschnitt „Runde“ = P1 bis nächstes P1, wiederkehrend, Vergleich an."""
    from dataclasses import replace

    ev = [
        x if x.uuid != "d4" else _e("DURCHFAHRT", 22 * MIN, "d4", wert="1") for x in rennen()
    ]  # 2. Runde 9 min
    erg = _auswerten(ev, abschnitte=(RUNDE,))
    z = erg.zeile(1)
    runde = [x for x in z.abschnittszeiten if x.schluessel == "a1"]
    assert [(x.label, x.zeit_ms, x.vergleich_ms) for x in runde] == [
        ("Runde (1)", 10 * MIN, None), ("Runde (2)", 9 * MIN, -MIN)]  # fmt: skip
    assert replace(runde[0], zeit_ms=0).k == 1


# --- Verlauf als Punktfolge -----------------------------------------------------


def test_punktfolge_beispiel_abfolge():
    """Start · P1 · [Runde ×2: P2 · P1] · Ziel → Abfolge Start·P1·P2·P1·P2·P1·Ziel."""
    folge = ["P1", sg.VerlaufBlock("Runde", 2, ("P2", "P1"))]
    teile = sg.teile_aus_verlauf(folge)
    assert [(t.name, t.wdh, [(s.von, s.nach) for s in t.segmente]) for t in teile] == [
        ("", 1, [(START, "P1")]),
        ("Runde", 2, [("P1", "P2"), ("P2", "P1")]),
        ("", 1, [("P1", ZIEL)]),
    ]
    assert sg.punkte(teile) == [START, "P1", "P2", "P1", "P2", "P1", ZIEL]
    assert sg.erwartete_anzahl(teile) == {"P1": 3, "P2": 2}
    assert sg.verlauf_pruefen(folge, {"P1": "P1", "P2": "P2"}) == []
    assert sg.teile_aus_verlauf([]) == (TeilDef((SegmentDef("", START, ZIEL),)),)


def strukturgleich(teile):
    return [(t.name, t.wdh, [(s.von, s.nach) for s in t.segmente]) for t in teile]


@pytest.mark.parametrize(
    "teile",
    [
        (),
        (TeilDef((SegmentDef("Gesamt", START, ZIEL, 20.0),)),),
        sg.aus_altem_modell(
            [
                ("1", "A", "anlauf", 1.0),
                ("2", "K", "runde", 2.0),
                ("3", "B", "runde", 4.0),
                ("4", "Z", "auslauf", 9.0),
            ],
            3,
        ),
        (  # Block direkt nach dem Start, zwei Blöcke hintereinander, Block endet am Ziel
            TeilDef((SegmentDef("a", START, "1"), SegmentDef("b", "1", "2")), 1, "Anfang"),
            TeilDef((SegmentDef("c", "2", "3"), SegmentDef("d", "3", "2")), 3, "Runde"),
            TeilDef((SegmentDef("e", "2", "1"), SegmentDef("f", "1", "2")), 2, "Runde"),
            TeilDef((SegmentDef("g", "2", "4"), SegmentDef("h", "4", ZIEL)), 1, "Schluss"),
        ),
    ],
)
def test_bestehende_definition_verlustfrei_als_punktfolge(teile):
    folge = sg.verlauf_aus_teilen(teile)
    zurueck = sg.teile_aus_verlauf(folge) if teile else ()
    assert strukturgleich(zurueck) == strukturgleich(teile)


def test_punktfolge_pruefung():
    namen = {"1": "Kreisel", "2": "Berg"}
    assert (
        "muss dort auch enden"
        in sg.verlauf_pruefen(["1", sg.VerlaufBlock("Runde", 2, ("2",))], namen)[0]
    )
    assert sg.verlauf_pruefen(["1", sg.VerlaufBlock("Runde", 1, ("2",))], namen) == []
    assert "jeden Punkt wählen" in sg.verlauf_pruefen([sg.VerlaufBlock("R", 2, ())], namen)[0]
    assert "Messpunkt wählen" in sg.verlauf_pruefen([""], namen)[0]


def test_abschnitt_vorschau_in_worten():
    """„Runde: P1 → P2 → P1, 2-mal, mit Vergleich“."""
    teile = sg.teile_aus_verlauf(["1", sg.VerlaufBlock("Runde", 2, ("2", "1"))])
    namen = {"1": "P1", "2": "P2"}
    runde = sg.AbschnittDef("a1", "Runde", "1", "1", sg.WIEDERKEHREND, vergleich=True)
    assert sg.abschnitt_text(runde, teile, namen) == "Runde: P1 → P2 → P1, 2-mal"
    erste = sg.AbschnittDef("a2", "1. Runde", "1", "1", sg.EINMALIG, 1, 2)
    assert (
        sg.abschnitt_text(erste, teile, namen)
        == "1. Runde: P1 → P2 → P1, nur die 1. Durchfahrt an P1"
    )
    anlauf = sg.AbschnittDef("a3", "Anlauf", sg.START, "1", sg.WIEDERKEHREND)
    assert sg.abschnitt_text(anlauf, teile, namen) == "Anlauf: Start → P1, einmal"
    weg = sg.AbschnittDef("a4", "Weg", "2", sg.START)
    assert sg.abschnitt_text(weg, teile, namen) == "Weg: kommt in der Abfolge nicht vor"
    assert sg.ART_TEXT == {
        sg.WIEDERKEHREND: "jede Durchfahrt",
        sg.EINMALIG: "eine bestimmte Durchfahrt",
    }


# --- Rundenblock automatisch als Auswertungsabschnitt ---------------------------


def test_rundenblock_ist_automatischer_abschnitt():
    t = beispiel()
    alle = sg.abschnitte(t)
    block = [a for a in alle if a.block]
    assert [(a.id, a.name, a.von, a.nach, a.art, a.vergleich, a.aus) for a in block] == [
        ("b1", "Runde", "1", "1", sg.WIEDERKEHREND, True, False)
    ]
    assert block[0].automatisch and not block[0].segment
    assert sg.paare(block[0], t) == [(1, 3), (3, 5)]  # je Wiederholung Anfang und Ende
    assert sg.abschnitt_text(block[0], t, MP) == "Runde: P1 → P2 → P1, 2-mal"
    tipps = [Tipp(f"u{i}", mp, 100 + i * 10) for i, mp in enumerate(["1", "2", "1", "2", "1"], 1)]
    f = Fahrt(100, 160, tipps)
    zeiten = {x.uuid: x.zeit_ms for x in tipps}
    assert sg.abschnittszeiten(block[0], t, f, sg.zuordnen(t, f), zeiten) == [20, 20]
    # ausblendbar, Vergleich abschaltbar; ein Block mit nur einem Segment ist schon ein Segment
    aus = sg.abschnitte(t, {"bloecke": {"1": {"aus": True, "vergleich": False}}})
    assert [(a.aus, a.vergleich) for a in aus if a.block] == [(True, False)]
    einzeln = (TeilDef((SegmentDef("Start→P1", START, "1"),)),
               TeilDef((SegmentDef("P1→P1", "1", "1"),), 3, "Schleife"),
               TeilDef((SegmentDef("P1→Ziel", "1", ZIEL),)))  # fmt: skip
    assert not [a for a in sg.abschnitte(einzeln) if a.block]


def test_rundenblock_kein_duplikat_eines_eigenen_abschnitts():
    t = beispiel()
    e = {"eigene": [{"id": "a1", "name": "Meine Runde", "von": "1", "nach": "1",
                     "art": "wiederkehrend", "vergleich": True}]}  # fmt: skip
    alle = sg.abschnitte(t, e)
    assert not [a for a in alle if a.block] and [a.name for a in alle if not a.automatisch] == [
        "Meine Runde"
    ]
    hinweis = ("Rundenblock „Runde“: Der eigene Abschnitt „Meine Runde“ wertet schon dasselbe "
               "aus – kein zusätzlicher automatischer Abschnitt.")  # fmt: skip
    assert sg.abschnitt_hinweise(t, e) == [hinweis]
    # nur die 1. Runde ist nicht gleichwertig: Block-Abschnitt bleibt
    e = {"eigene": [{"id": "a1", "name": "1. Runde", "von": "1", "nach": "1", "art": "einmalig",
                     "von_nr": 1, "nach_nr": 2}]}  # fmt: skip
    assert [a.id for a in sg.abschnitte(t, e) if a.block] == ["b1"]
    assert sg.abschnitt_hinweise(t, e) == []
