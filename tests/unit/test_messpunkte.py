"""Messpunkte der Veranstaltung und Segmente je Strecke speichern, prüfen, einfrieren (Segmente 7/12)."""

import pytest

from app import korrekturen as k
from app import segmente as sg
from app import strecken as st
from tests.unit import test_saetze
from tests.unit.test_saetze import T0, ereignis
from tests.unit.test_strecken import formular, speichern, veranstaltung

session = test_saetze.session  # Fixture


def mp(name, id_=""):
    return {"id": id_, "name": name}


def mps(session, v):
    return st.messpunkte_von(session, v)


def tl(von, nach, block="", wdh="", name="", km="", vergleich=False, strecke="1"):
    return {"strecke": strecke, "block": block, "wdh": wdh, "name": name, "von": von, "nach": nach,
            "km": km, "vergleich": vergleich}  # fmt: skip


BEISPIEL = [tl("Start", "P1", km="1,5"), tl("P1", "P2", "Runde", "2", km="3,5", vergleich=True),
            tl("P2", "P1", "Runde", "2", km="3,5"), tl("P1", "Ziel", km="0,5")]  # fmt: skip


def mit_segmenten(session, v, messpunkte, teile):
    zeilen, zuordnung = formular(session, v)
    return st.strecken_speichern(session, v, zeilen, zuordnung, {"Jungen 12-14", "Damen 31-40"},
                                 messpunkte=list(messpunkte), teile=list(teile))  # fmt: skip


def test_beispielstrecke_speichern(session):
    v = veranstaltung(session)  # 5 km und 20 km
    mit_segmenten(session, v, [mp("P1"), mp("P2")], BEISPIEL)
    lang = st.strecken_von(session, v)[1]
    namen = {str(m.id): m.name for m in mps(session, v)}
    teile = st.teile_von(lang)
    assert sg.abfolge_text(teile, namen) == "Start · P1 · P2 · P1 · P2 · P1 · Ziel"
    assert [(t.name, t.wdh, len(t.segmente)) for t in teile] == [
        ("", 1, 1),
        ("Runde", 2, 2),
        ("", 1, 1),
    ]
    assert teile[1].segmente[0].km == 3.5  # „Vergleich“ gehört jetzt zum Abschnitt (Segmente 13)
    assert teile[0].segmente[0].name == "Start→P1"  # ohne Namen: „von→nach“
    assert len({m.token for m in mps(session, v)}) == 2
    defs = st.auswertungs_strecken(session, v)
    assert defs[1].mit_durchfahrten and not defs[0].mit_durchfahrten
    # ohne Segment-Angaben (älteres Formular) bleibt alles, wie es ist
    zeilen, zuordnung = formular(session, v)
    speichern(session, v, zeilen, zuordnung)
    assert st.teile_von(st.strecken_von(session, v)[1]) == teile
    # Messpunkt umbenennen: Token bleibt, Teile zeigen den neuen Namen
    ids = {m.name: m for m in mps(session, v)}
    token = ids["P1"].token
    neu = [
        {**x, "von": x["von"].replace("P1", "Kreisel"), "nach": x["nach"].replace("P1", "Kreisel")}
        for x in BEISPIEL
    ]
    mit_segmenten(
        session, v, [mp("Kreisel", id_=str(ids["P1"].id)), mp("P2", id_=str(ids["P2"].id))], neu
    )
    lang = st.strecken_von(session, v)[1]
    assert [(m.name, m.token == token) for m in mps(session, v)] == [
        ("Kreisel", True),
        ("P2", False),
    ]
    assert sg.abfolge_text(
        st.teile_von(lang), {str(m.id): m.name for m in mps(session, v)}
    ).startswith("Start · Kreisel")
    # alles entfernen: keine Segmente mehr
    mit_segmenten(session, v, [{**mp("Kreisel", id_=str(ids["P1"].id)), "entfernen": True},
                               {**mp("P2", id_=str(ids["P2"].id)), "entfernen": True}], [])  # fmt: skip
    assert st.strecken_von(session, v)[1].teile_json is None


@pytest.mark.parametrize("messpunkte, teile, meldung", [
    ([mp("P1")], [tl("Start", "P1"), tl("P2", "Ziel")], "keine Zwischenzeit"),
    ([mp("P1"), mp("P2")], [tl("Start", "P1"), tl("P2", "Ziel")], "Lücke"),
    ([mp("P1")], [tl("Start", "P1"), tl("P1", "P1", wdh="2"), tl("P1", "Ziel")], "nur in einem Rundenblock"),
    ([mp("P1")], [tl("Start", "P1"), tl("P1", "P1", "R", "2"), tl("P1", "P1", "R", "3"), tl("P1", "Ziel")], "unterschiedliche"),
    ([mp("P1")], [tl("Start", "P1", km="-1"), tl("P1", "Ziel")], "Länge"),
    ([mp("P1")], [tl("Start", "P1", wdh="99", block="R"), tl("P1", "Ziel")], "Wiederholungen"),
    ([mp("P1")], [tl("Start", ""), tl("P1", "Ziel")], "Pflicht"),
    ([mp("Ziel")], [tl("Start", "Ziel")], "eigene Geräte"),
    ([mp("A"), mp("a")], [], "doppelt"),
])  # fmt: skip
def test_pruefungen(session, messpunkte, teile, meldung):
    v = veranstaltung(session)
    with pytest.raises(st.StreckenFehler, match=meldung):
        mit_segmenten(session, v, messpunkte, teile)


def test_einfrieren(session):
    v = veranstaltung(session)
    mit_segmenten(session, v, [mp("P1"), mp("P2")], BEISPIEL)
    lang = st.strecken_von(session, v)[1]
    ereignis(session, v, "START", startnummer=2, zeit_ms=T0)
    k.saetze.einfrieren(session, v)
    session.commit()
    ids = {m.name: str(m.id) for m in mps(session, v)}
    zeilen = [mp("P1", id_=ids["P1"]), mp("P2", id_=ids["P2"])]
    anders_wdh = [{**x, "wdh": "3"} if x["block"] else x for x in BEISPIEL]
    for m, t in ((zeilen, anders_wdh), (zeilen + [mp("Neu")], BEISPIEL), (zeilen[:1], BEISPIEL[:1] + BEISPIEL[3:]),
                 (zeilen, [*BEISPIEL[:3], tl("P1", "P2"), tl("P2", "Ziel")])):  # fmt: skip
        with pytest.raises(st.StreckenFehler, match="eingefroren"):
            mit_segmenten(session, v, m, t)
    # Namen, Längen und „Vergleich“ bleiben änderbar
    erlaubt = [
        {**x, "name": f"S{i}", "km": "2,0", "vergleich": False} for i, x in enumerate(BEISPIEL)
    ]
    mit_segmenten(session, v, [mp("Kreisel", id_=ids["P1"]), zeilen[1]],
                  [{**x, "von": x["von"].replace("P1", "Kreisel"), "nach": x["nach"].replace("P1", "Kreisel")}
                   for x in erlaubt])  # fmt: skip
    lang = st.strecken_von(session, v)[1]
    assert mps(session, v)[0].name == "Kreisel"
    assert [s.name for t in st.teile_von(lang) for s in t.segmente] == ["S0", "S1", "S2", "S3"]


def test_gemeinsamer_messpunkt_fuer_zwei_strecken(session):
    """Segmente 12: P1 nutzen beide Strecken, P2 nur die lange."""
    v = veranstaltung(session)  # 5 km (Zeile 0) und 20 km (Zeile 1)
    kurz = [tl("Start", "P1", strecke="0"), tl("P1", "Ziel", strecke="0")]
    mit_segmenten(session, v, [mp("P1"), mp("P2")], kurz + BEISPIEL)
    liste = st.strecken_von(session, v)
    p1, p2 = mps(session, v)
    assert st.nutzende_strecken(liste, p1.id) == [0, 1] and st.nutzende_strecken(liste, p2.id) == [
        1
    ]
    defs = st.auswertungs_strecken(session, v)
    assert [m.name for m in defs[0].messpunkte] == ["P1"] and [
        m.name for m in defs[1].messpunkte
    ] == ["P1", "P2"]
    assert len(mps(session, v)) == 2  # ein Posten-Gerät/Token je Messpunkt


def ab(
    name="",
    von="",
    nach="",
    art="wiederkehrend",
    vergleich=False,
    aus=False,
    segment="",
    strecke="1",
    **kw,
):
    return {"strecke": strecke, "segment": segment, "id": kw.get("id", ""), "name": name, "von": von, "nach": nach,
            "art": art, "von_nr": kw.get("von_nr", ""), "nach_nr": kw.get("nach_nr", ""), "vergleich": vergleich,
            "aus": aus}  # fmt: skip


def test_auswertungsabschnitte_speichern(session):
    """Segmente 13: Segment-Abschnitte ausblendbar mit Vergleich, eigener Abschnitt „Runde“."""
    v = veranstaltung(session)
    zeilen, zuordnung = formular(session, v)
    abschnitte = [ab(segment="1.0", vergleich=True), ab(segment="2.0", aus=True),
                  ab("Runde", "P1", "P1", vergleich=True)]  # fmt: skip
    st.strecken_speichern(session, v, zeilen, zuordnung, {"Jungen 12-14", "Damen 31-40"},
                          messpunkte=[mp("P1"), mp("P2")], teile=BEISPIEL, abschnitte=abschnitte)  # fmt: skip
    lang = st.strecken_von(session, v)[1]
    alle = st.abschnitte_von(lang)
    assert [(a.name, a.aus, a.vergleich) for a in alle] == [
        ("Start→P1", False, False), ("P1→P2", False, True), ("P2→P1", False, False), ("P1→Ziel", True, False),
        ("Runde", False, True)]  # fmt: skip
    assert alle[-1].id == "a1" and sg.paare(alle[-1], st.teile_von(lang)) == [(1, 3), (3, 5)]
    # Fehler: Y nicht nach X, Punkt nicht auf der Strecke, Vergleich bei einmaligem
    for falsch, meldung in ((ab("X", "Ziel", "P1", art="einmalig"), "liegt nicht nach"),
                            (ab("X", "Start", "Ziel", art="einmalig", vergleich=True), "nur bei „jede Durchfahrt“"),
                            (ab(segment="0.0", vergleich=True), "nur bei wiederholten")):  # fmt: skip
        zeilen, zuordnung = formular(session, v)
        with pytest.raises(st.StreckenFehler, match=meldung):
            st.strecken_speichern(session, v, zeilen, zuordnung, {"Jungen 12-14", "Damen 31-40"},
                                  messpunkte=[mp("P1", id_=str(mps(session, v)[0].id)),
                                              mp("P2", id_=str(mps(session, v)[1].id))],
                                  teile=BEISPIEL, abschnitte=[falsch])  # fmt: skip


def test_ungenutzter_messpunkt_nur_hinweis_genutzter_nicht_entfernbar(session):
    """Segmente 18: Messpunkte entstehen im Reiter „Strecken“ vor ihrer Nutzung – ungenutzt ist ein Hinweis."""
    v = veranstaltung(session)
    hinweise = mit_segmenten(
        session, v, [mp("P1"), mp("P2")], [tl("Start", "P1"), tl("P1", "Ziel")]
    )
    assert "Zwischenzeit „P2“ kommt noch in keiner Strecke vor." in hinweise
    # Messpunkt entfernen, den eine andere (nicht mitgespeicherte) Strecke nutzt → Fehler
    zeilen, zuordnung = formular(session, v)
    p1, p2 = mps(session, v)
    with pytest.raises(st.StreckenFehler, match="wird noch von"):
        st.strecken_speichern(session, v, zeilen, zuordnung, {"Jungen 12-14", "Damen 31-40"},
                              messpunkte=[{**mp("P1", id_=str(p1.id)), "entfernen": True}, mp("P2", id_=str(p2.id))])  # fmt: skip
    # nur die Teile einer Strecke speichern (Seite der Strecke): die andere bleibt
    kurz = [tl("Start", "P2", strecke="0"), tl("P2", "Ziel", strecke="0")]
    st.strecken_speichern(
        session, v, zeilen, zuordnung, {"Jungen 12-14", "Damen 31-40"}, teile=kurz, nur={0}
    )
    liste = st.strecken_von(session, v)
    assert st.nutzende_strecken(liste, p2.id) == [0] and st.nutzende_strecken(liste, p1.id) == [1]
