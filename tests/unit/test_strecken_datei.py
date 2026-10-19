"""Segmente 22: Streckendefinition als TOML speichern und laden."""

import pytest

from app import korrekturen as k
from app import segmente as sg
from app import strecken as st
from app import strecken_datei as sd
from app.saetze import SatzFehler
from tests.unit import test_saetze
from tests.unit.test_messpunkte import BEISPIEL, ab, mit_segmenten, mp, tl
from tests.unit.test_saetze import T0, ereignis
from tests.unit.test_strecken import formular, veranstaltung

session = test_saetze.session  # Fixture
MIT = {"Jungen 12-14", "Damen 31-40"}


def beispiel_veranstaltung(session):
    v = veranstaltung(session)  # 5 km und 20 km
    zeilen, zuordnung = formular(session, v)
    st.strecken_speichern(session, v, zeilen, zuordnung, MIT, messpunkte=[mp("Kreisel"), mp("Berg")],
                          teile=[{**x, "von": x["von"].replace("P1", "Kreisel").replace("P2", "Berg"),
                                  "nach": x["nach"].replace("P1", "Kreisel").replace("P2", "Berg")} for x in BEISPIEL],
                          abschnitte=[ab(segment="1.0", vergleich=True), ab(segment="2.0", aus=True),
                                      ab("Runde", "Kreisel", "Kreisel", vergleich=True)])  # fmt: skip
    lang = st.strecken_von(session, v)[1]
    lang.beschreibung, lang.komoot_url = (
        "Rundkurs Kottenforst",
        "https://www.komoot.com/de-de/tour/1",
    )
    session.commit()
    return v, lang


def test_speichern_und_in_andere_veranstaltung_laden(session):
    v, lang = beispiel_veranstaltung(session)
    text = sd.strecke_toml(session, v, lang)
    assert (
        "version = 1" in text
        and 'messpunkte = ["Kreisel", "Berg"]' in text
        and "[[teil.segment]]" in text
    )
    assert "klasse" not in text.lower().replace(
        "klassenzuordnung", ""
    )  # Klassenzuordnung gehört nicht dazu
    # Segmente 52: Darstellung (anzeigen, Vergleich) gehört nicht mehr in die Datei
    assert "vergleich" not in text and "aus =" not in text and "segment_abschnitt" not in text
    e = sd.lesen(text.encode())
    assert (
        e.fehler == [] and e.name == "20 km" and e.komoot.startswith("https://") and e.beschreibung
    )
    # andere Veranstaltung mit einem schon vorhandenen Messpunkt „kreisel“ (Groß-/Kleinschreibung egal)
    w = k.veranstaltung_anlegen(session, "Zweite", v.datum, aktivieren=False)
    zeilen, zuordnung = formular(session, w)
    st.strecken_speichern(session, w, zeilen, zuordnung, set(), messpunkte=[mp("kreisel")],
                          teile=[tl("Start", "kreisel", strecke="0"), tl("kreisel", "Ziel", strecke="0")])  # fmt: skip
    vs = sd.vorschau(session, w, e)
    assert (
        vs.wiederverwendet == ["Start", "Kreisel", "Ziel"]
        and vs.neu == ["Berg"]
        and vs.anzahl == [("Kreisel", 3), ("Berg", 2)]
    )
    assert (
        vs.abfolge == "Start · Kreisel · Berg · Kreisel · Berg · Kreisel · Ziel"
        and not vs.namenskonflikt
    )
    neu = sd.uebernehmen(session, w, e, "Rundkurs", set())
    namen = {str(m.id): m.name for m in st.messpunkte_von(session, w)}
    assert len(namen) == 2  # „kreisel“ wiederverwendet, „Berg“ neu
    assert (
        sg.abfolge_text(st.teile_von(neu), namen)
        == "Start · kreisel · Berg · kreisel · Berg · kreisel · Ziel"
    )
    # neue Abschnitte: angezeigt, ohne Vergleich (Segmente 52)
    assert [(a.name, a.aus, a.vergleich) for a in st.abschnitte_von(neu)] == [
        (a.name, False, False) for a in st.abschnitte_von(lang)
    ]
    assert (neu.laenge_km, neu.komoot_url, neu.klassen) == (lang.laenge_km, lang.komoot_url, [])
    # nochmal derselbe Name → Konflikt
    assert (
        sd.vorschau(session, w, e).namenskonflikt is False
        and sd.vorschau(
            session, w, sd.lesen(text.replace('name = "20 km"', 'name = "Rundkurs"', 1).encode())
        ).namenskonflikt
    )
    with pytest.raises(SatzFehler, match="schon eine Strecke"):
        sd.uebernehmen(session, w, e, "rundkurs", set())


@pytest.mark.parametrize("ersetzen, meldung", [
    (("version = 1", "version = 7"), "Unbekannte Version"),
    (('nach = "Berg"', 'nach = "Wald"'), "steht nicht in der Liste"),
    (("https://www.komoot.com", "http://www.komoot.com"), "https-Adresse"),
    (('name = "20 km"', 'name = ""'), "Name der Strecke fehlt"),
])  # fmt: skip
def test_pruefhinweise(session, ersetzen, meldung):
    v, lang = beispiel_veranstaltung(session)
    text = sd.strecke_toml(session, v, lang).replace(*ersetzen, 1)
    e = sd.lesen(text.encode())
    assert any(meldung in f for f in e.fehler), e.fehler
    with pytest.raises(SatzFehler, match="Fehler"):
        sd.uebernehmen(session, v, e, "Neu", MIT)
    assert sd.lesen(b"kein = [toml").fehler[0].startswith("Datei nicht lesbar")


def test_nach_dem_einfrieren_gesperrt(session):
    v, lang = beispiel_veranstaltung(session)
    e = sd.lesen(sd.strecke_toml(session, v, lang).encode())
    ereignis(session, v, "START", startnummer=2, zeit_ms=T0)
    k.saetze.einfrieren(session, v)
    session.commit()
    assert sd.vorschau(session, v, e).sperre
    with pytest.raises(SatzFehler, match="eingefroren"):
        sd.uebernehmen(session, v, e, "Neu", MIT)


def test_komoot_pruefen():
    assert (
        sd.komoot_pruefen("") is None
        and sd.komoot_pruefen(" https://komoot.com/tour/1 ") == "https://komoot.com/tour/1"
    )
    for falsch in ("http://komoot.com/tour/1", "komoot.com", "https://x y"):
        with pytest.raises(SatzFehler):
            sd.komoot_pruefen(falsch)


def test_einfache_strecke_ohne_messpunkte(session):
    v = veranstaltung(session)
    kurz = st.strecken_von(session, v)[0]
    e = sd.lesen(sd.strecke_toml(session, v, kurz).encode())
    assert e.fehler == [] and e.teile == () and sd.vorschau(session, v, e).abfolge == "Start · Ziel"
    assert mit_segmenten  # Hilfsfunktion importiert (gleiches Formular wie die Admin-Seite)


def test_bestehende_strecke_ersetzen(session):
    """Segmente 39: Verlauf, Abschnitte, Länge, Beschreibung, Komoot ersetzt; Name und Klassen bleiben."""
    v, lang = beispiel_veranstaltung(session)
    text = sd.strecke_toml(session, v, lang).replace("laenge_km = 20", "laenge_km = 21.5")
    e = sd.lesen(text.encode())
    kurz = st.strecken_von(session, v)[0]
    klassen = sorted(sk.klasse for sk in kurz.klassen)
    vs = sd.vorschau(session, v, e)
    assert vs.namenskonflikt and vs.ersetzbar.id == lang.id
    assert sd.vorschau(session, v, e, ersetzen=kurz.id).ersetzbar.id == kurz.id
    neu = sd.uebernehmen(session, v, e, "", MIT, ersetzen=kurz)
    assert (
        neu.id == kurz.id
        and neu.name == "5 km"
        and sorted(sk.klasse for sk in neu.klassen) == klassen
    )
    assert neu.laenge_km == 21.5 and neu.beschreibung == "Rundkurs Kottenforst" and neu.komoot_url
    assert neu.teile_json == lang.teile_json
    assert [(a.name, a.aus, a.vergleich) for a in st.abschnitte_von(neu)] == [
        (a.name, False, False) for a in st.abschnitte_von(lang)
    ]  # „5 km“ hatte diese Abschnitte nicht: neu, angezeigt, ohne Vergleich
    assert len(st.strecken_von(session, v)) == 2
    # dieselbe Strecke aus ihrer eigenen Datei ersetzt: passende Abschnitte behalten ihre Darstellung
    vorher = [(a.name, a.aus, a.vergleich) for a in st.abschnitte_von(lang)]
    assert any(aus for _, aus, _ in vorher) and any(vg for _, _, vg in vorher)
    sd.uebernehmen(session, v, e, "", MIT, ersetzen=lang)
    assert [(a.name, a.aus, a.vergleich) for a in st.abschnitte_von(lang)] == vorher
    ereignis(session, v, "START", startnummer=1, zeit_ms=T0)
    from app import saetze as sz

    sz.einfrieren(session, v)
    with pytest.raises(SatzFehler, match="eingefroren"):
        sd.uebernehmen(session, v, e, "", MIT, ersetzen=kurz)


def test_ersetzen_nennt_was_verloren_geht():
    """Segmente 50: Bestätigungstext beim Ersetzen."""
    import json

    from app.models import Strecke
    from app.segmente import nach_json
    from app.simulation import beispiel_teile

    s = Strecke(name="20 km", laenge_km=20.3, teile_json=nach_json(beispiel_teile({"P1": "1", "P2": "2"})),
                abschnitte_json=json.dumps({"eigene": [{"id": "a"}, {"id": "b"}]}))  # fmt: skip
    assert (
        sd.ersetzen_verlust(s)
        == "Verlauf, Start/Ziel, 2 eigene Abschnitte und Länge von ‚20 km‘ werden ersetzt."
    )
    einfach = Strecke(
        name="Kurz", abschnitte_json=json.dumps({"eigene": [{"id": "a"}]}), beschreibung="x"
    )
    assert (
        sd.ersetzen_verlust(einfach)
        == "Start/Ziel, 1 eigener Abschnitt und Beschreibung von ‚Kurz‘ werden ersetzt."
    )


def test_alte_datei_mit_darstellung_laedt_mit_hinweis():
    """Segmente 52: „anzeigen“/„Vergleich“ aus älteren Dateien werden ignoriert – mit Hinweis."""
    alt = (
        'version = 1\nname = "Alt"\nmesspunkte = ["P1"]\n'
        '[[teil]]\nname = ""\nwdh = 1\n[[teil.segment]]\nvon = "Start"\nnach = "P1"\n'
        '[[teil.segment]]\nvon = "P1"\nnach = "Ziel"\n'
        "[[segment_abschnitt]]\nteil = 1\nsegment = 1\nvergleich = false\naus = true\n"
        '[[abschnitt]]\nname = "Weg"\nvon = "Start"\nnach = "Ziel"\nart = "einmalig"\nvergleich = false\naus = true\n'
    )
    e = sd.lesen(alt.encode())
    assert e.fehler == [] and e.hinweise == [sd.DARSTELLUNG_IGNORIERT]
    assert "Darstellungseinstellungen werden ignoriert" in sd.DARSTELLUNG_IGNORIERT
    assert all("aus" not in a and "vergleich" not in a for a in e.abschnitte)
    assert sd.lesen(alt.split("[[segment_abschnitt]]")[0].encode()).hinweise == []
