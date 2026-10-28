"""Ziel einer Strecke an einem Zwischen-Messpunkt: wählbar, geprüft, in der Streckendatei.

Beispiel wie beim RCB Zeitfahrtraining: 20 km „Start · P1 · [P2 · P1] ×2 · Ziel“ und 5 km
„Start · P1 · P2“ – das Ziel der 5 km liegt an P2, dort steht nur ein Posten-Gerät.
"""

from datetime import date

import pytest

from app import korrekturen as k
from app import segmente as sg
from app import strecken as st
from app import strecken_datei as sd
from app.saetze import SatzFehler
from tests.unit import test_saetze
from tests.unit.test_orte import ort, orte_anlegen, zeile
from tests.unit.test_strecken import formular, speichern, veranstaltung

session = test_saetze.session  # Fixture


def zwei_strecken(session, ziel_5km="P2"):
    """20 km mit Rundenblock (Ziel „Ziel“) und 5 km mit Ziel an einer Zwischenzeit."""
    v = veranstaltung(session)
    orte_anlegen(session, v, ("P1", ""), ("P2", ""))
    p1, p2 = str(ort(session, v, "P1").id), str(ort(session, v, "P2").id)
    lang, kurz = st.strecken_von(session, v)[:2]
    lang.teile_json = sg.nach_json(
        sg.teile_aus_verlauf([p1, sg.VerlaufBlock("Runde", 2, (p2, p1))])
    )
    kurz.teile_json = sg.nach_json(sg.teile_aus_verlauf([p1]))
    if ziel_5km:
        st.strecke_orte_setzen(session, v, kurz, None, str(ort(session, v, ziel_5km).id))
    session.commit()
    return v, lang, kurz


def test_ziel_an_zwischenzeit_waehlbar(session):
    v, lang, kurz = zwei_strecken(session)
    orte = st.orte_von(session, v)
    assert st.zielort_von(kurz, orte).name == "P2" and st.zielpunkt_von(kurz, orte).name == "P2"
    assert st.zielort_von(lang, orte).name == "Ziel" and st.zielpunkt_von(lang, orte) is None
    assert kurz.zielort_id == ort(session, v, "P2").id and lang.zielort_id is None
    # gilt als genutzt: Gerätelink, „Genutzt von“, Hinweise
    liste = st.strecken_von(session, v)
    assert st.nutzende_strecken(liste, ort(session, v, "P2").id) == [0, 1]
    assert st.nutzende_strecken(liste, ort(session, v, "P1").id) == [0, 1]
    assert st.genutzt(session, v, ort(session, v, "P2"))
    # zurück auf den Standard „Ziel“: keine Angabe
    st.strecke_orte_setzen(session, v, kurz, None, str(ort(session, v, "Ziel").id))
    assert kurz.zielort_id is None


def test_start_nicht_als_ziel(session):
    v, _, kurz = zwei_strecken(session)
    orte_anlegen(session, v, ("Nord", "start"))
    with pytest.raises(SatzFehler, match="Unbekannter Messpunkt für „Ziel“"):
        st.strecke_orte_setzen(session, v, kurz, None, str(ort(session, v, "Nord").id))


def test_ziel_darf_nicht_vorher_vorkommen(session):
    """P1 als Ziel der 20 km (P1 kommt dort schon dreimal vor) und P2 im eigenen Verlauf der
    5 km: verboten mit klarer Meldung, nichts wird gespeichert."""
    v, lang, kurz = zwei_strecken(session, ziel_5km=None)
    st.strecke_orte_setzen(session, v, lang, None, str(ort(session, v, "P1").id))
    zeilen, zuordnung = formular(session, v)
    with pytest.raises(SatzFehler, match="Das Ziel „P1“ kommt im Verlauf schon vorher vor"):
        speichern(session, v, zeilen, zuordnung)
    session.rollback()
    assert st.strecken_von(session, v)[0].zielort_id is None
    kurz = st.strecken_von(session, v)[1]
    p1, p2 = str(ort(session, v, "P1").id), str(ort(session, v, "P2").id)
    kurz.teile_json = sg.nach_json(sg.teile_aus_verlauf([p1, p2]))
    st.strecke_orte_setzen(session, v, kurz, None, p2)
    with pytest.raises(SatzFehler, match="Das Ziel „P2“ kommt im Verlauf schon vorher vor"):
        speichern(session, v, *formular(session, v))


def test_ziel_zwischenzeit_nicht_entfernbar(session):
    v, _, _ = zwei_strecken(session)
    lang = st.strecken_von(session, v)[0]
    lang.teile_json = sg.nach_json(sg.teile_aus_verlauf([str(ort(session, v, "P1").id)]))
    session.commit()  # P2 nur noch Ziel der 5 km
    zeilen = [zeile(m, entfernen=m.name == "P2") for m in st.orte_von(session, v)]
    with pytest.raises(SatzFehler, match="„P2“ wird noch von .*genutzt"):
        st.orte_speichern(session, v, zeilen)
    session.rollback()
    hinweise = st.orte_speichern(session, v, [zeile(m) for m in st.orte_von(session, v)])
    assert not any("P2" in h for h in hinweise)  # kein „kommt noch in keiner Strecke vor“


def test_streckendatei_mit_ziel_an_zwischenzeit(session):
    v, _, kurz = zwei_strecken(session)
    text = sd.strecke_toml(session, v, kurz)
    assert 'zielort = "P2"' in text and 'zielort_art = "zwischenzeit"' in text
    assert 'messpunkte = ["P1"]' in text
    e = sd.lesen(text.encode())
    assert e.fehler == [] and e.zielort == "P2" and e.ziel_zwischenzeit
    w = k.veranstaltung_anlegen(session, "Zweite", date(2026, 10, 11), aktivieren=False)
    vs = sd.vorschau(session, w, e)
    assert vs.abfolge == "Start · P1 · P2" and vs.neu == ["P1", "P2"] and vs.ortkonflikte == []
    neu = sd.uebernehmen(session, w, e, "5 km", set())
    orte = st.orte_von(session, w)
    assert st.zielpunkt_von(neu, orte).name == "P2"
    assert {(m.name, m.art) for m in orte} >= {("P1", "punkt"), ("P2", "punkt")}
    # gibt es „P2“ dort schon als Ziel: Hinweis statt stiller Umdeutung
    x = k.veranstaltung_anlegen(session, "Dritte", date(2026, 10, 12), aktivieren=False)
    orte_anlegen(session, x, ("P2", "ziel"))
    assert sd.vorschau(session, x, e).ortkonflikte == [
        "„P2“ ist hier ein Messpunkt der Art „Ziel“, in der Datei „Zwischenzeit“."
    ]
    # Ziel kommt vorher vor / unbekannte Art: Fehler beim Lesen
    doppelt = sd.lesen(text.replace('messpunkte = ["P1"]', 'messpunkte = ["P1", "P2"]').encode())
    assert any("kommt im Verlauf schon vorher vor" in f for f in doppelt.fehler)
    falsch = sd.lesen(text.replace('"zwischenzeit"', '"posten"').encode())
    assert any("Unbekannte Art des Zielorts" in f for f in falsch.fehler)


def test_streckendatei_ohne_zielort_art_wie_bisher(session):
    """Regression: Dateien ohne „zielort_art“ legen den Zielort als Ziel an."""
    v, _, kurz = zwei_strecken(session)
    text = sd.strecke_toml(session, v, kurz).replace('zielort_art = "zwischenzeit"\n', "")
    e = sd.lesen(text.encode())
    assert e.fehler == [] and not e.ziel_zwischenzeit
    w = k.veranstaltung_anlegen(session, "Zweite", date(2026, 10, 11), aktivieren=False)
    neu = sd.uebernehmen(session, w, e, "5 km", set())
    orte = st.orte_von(session, w)
    assert st.zielpunkt_von(neu, orte) is None and st.zielort_von(neu, orte).art == "ziel"
    lang = st.strecken_von(session, v)[0]
    assert "zielort_art" not in sd.strecke_toml(session, v, lang)


def test_bestehende_strecken_unveraendert(session):
    """Regression: Strecken mit Ziel-Ort – Zielort, Nutzung und Prüfung wie bisher."""
    v, lang, _ = zwei_strecken(session, ziel_5km=None)
    orte_anlegen(session, v, ("Hof", "ziel"))
    orte = st.orte_von(session, v)
    for s in st.strecken_von(session, v):
        assert st.zielpunkt_von(s, orte) is None and st.zielort_von(s, orte).name == "Ziel"
        st.ziel_pruefen(s, orte)  # kein Fehler
    st.strecke_orte_setzen(session, v, lang, None, str(ort(session, v, "Hof").id))
    assert st.zielort_von(lang, st.orte_von(session, v)).name == "Hof"
    assert st.zielpunkt_von(lang, st.orte_von(session, v)) is None
    speichern(session, v, *formular(session, v))
    liste = st.strecken_von(session, v)
    assert st.nutzende_strecken(liste, ort(session, v, "P2").id) == [0]
    assert st.strecken_am_ort(session, v, ort(session, v, "Hof")) == [0]
