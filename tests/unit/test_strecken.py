"""Strecken je Veranstaltung: Vorschlag, Speichern, Einfrieren (Phase 13)."""

from datetime import date

import pytest

from app import korrekturen as k
from app import saetze as sz
from app import strecken as st
from app.einstellungen import STANDARD_KLASSEN_DATEI
from app.klassen import lade_klassen
from app.models import Teilnehmer
from tests.unit import test_saetze
from tests.unit.test_saetze import T0, ereignis, ws

KLASSEN = lade_klassen(STANDARD_KLASSEN_DATEI)
JUGEND = [k.name for k in KLASSEN if k.alter_bis <= 17]
session = test_saetze.session  # Fixture (frische Datenbank mit Standardsätzen)


def test_vorschlag_aus_a1():
    from app.wertung import lade_wertungen

    w = lade_wertungen(STANDARD_KLASSEN_DATEI.rsplit("/", 1)[0] + "/wertungen", KLASSEN)
    liste = st.vorschlag(KLASSEN, w["radsportabzeichen-a1-flach.toml"])
    assert [(x.name, x.laenge_km) for x in liste] == [("5 km", 5.0), ("20 km", 20.0)]
    assert sorted(liste[0].klassen) == sorted(JUGEND)
    assert "Offen" in liste[1].klassen and "Herren 31-40" in liste[1].klassen
    assert len(liste[0].klassen) + len(liste[1].klassen) == len(KLASSEN) + 1
    # pro_km und keine Wertung: eine Strecke für alle Klassen
    for wertung in (w["radsportabzeichen-a2-berg-strasse.toml"], None):
        liste = st.vorschlag(KLASSEN, wertung)
        assert [(x.name, x.laenge_km) for x in liste] == [("Strecke 1", None)]
        assert len(liste[0].klassen) == len(KLASSEN) + 1


def veranstaltung(session, wertung=True):
    v = k.veranstaltung_anlegen(session, "Rennen", date(2026, 10, 10), aktivieren=True)
    session.add_all([
        Teilnehmer(veranstaltung_id=v.id, startnummer=1, vorname="A", nachname="B", geschlecht="m",
                   geburtsjahr=2013),  # Jungen 12-14
        Teilnehmer(veranstaltung_id=v.id, startnummer=2, vorname="C", nachname="D", geschlecht="w",
                   geburtsjahr=1990),  # Damen 31-40
    ])  # fmt: skip
    session.commit()
    if (
        wertung
    ):  # Strecken wie der Vorschlag aus A.1 (Segmente 49: legt der Satzwechsel nicht mehr an)
        st.anlegen(
            session, v, st.vorschlag(sz.regeln(session, v).klassen, sz.wertung_von(ws(session)))
        )
        sz.regeln_setzen(session, v, v.klassensatz_id, ws(session).id, None)
    return v


def formular(session, v, **aenderung):
    """Aktuelle Strecken als Formularzeilen + Zuordnung (wie die Admin-Seite sie schickt)."""
    liste = st.strecken_von(session, v)
    zeilen = [{"id": str(s.id), "name": s.name, "laenge": st.km_text(s.laenge_km)} for s in liste]
    zuordnung = {kl: str(i) for kl, i in st.zuordnung(liste).items()}
    return zeilen, zuordnung


def speichern(session, v, zeilen, zuordnung, offiziell=False):
    return st.strecken_speichern(session, v, zeilen, zuordnung, {"Jungen 12-14", "Damen 31-40"},
                                 offiziell=offiziell)  # fmt: skip


def test_neue_veranstaltung_hat_eine_strecke(session):
    v = veranstaltung(session, wertung=False)
    liste = st.strecken_von(session, v)
    assert [(s.name, s.laenge_km) for s in liste] == [("Strecke 1", None)]
    assert len(liste[0].klassen) == len(KLASSEN) + 1


def test_speichern_umordnen_hinzufuegen_entfernen(session):
    v = veranstaltung(session, wertung=False)
    zeilen, zuordnung = formular(session, v)
    zeilen[0].update(name="Erwachsene", laenge="20,3")
    zeilen.append({"id": "", "name": "Jugend", "laenge": "5,1"})
    for kl in JUGEND:
        zuordnung[kl] = "1"
    assert speichern(session, v, zeilen, zuordnung) == []
    liste = st.strecken_von(session, v)
    assert [(s.name, s.laenge_km) for s in liste] == [("Erwachsene", 20.3), ("Jugend", 5.1)]
    assert sorted(sk.klasse for sk in liste[1].klassen) == sorted(JUGEND)
    erg = k.ergebnis(session, v)
    assert {z.startnummer: z.strecke for z in erg.zeilen} == {1: 1, 2: 0}
    # dritte Strecke ohne Klasse → Hinweis
    zeilen, zuordnung = formular(session, v)
    zeilen.append({"id": "", "name": "Leer", "laenge": ""})
    assert speichern(session, v, zeilen, zuordnung) == ["Strecke „Leer“ hat keine Klasse."]
    # Strecke mit Klasse, die Teilnehmer hat, entfernen → Fehler
    zeilen, zuordnung = formular(session, v)
    zeilen[1]["entfernen"] = True
    with pytest.raises(
        st.StreckenFehler, match="„Jungen 12-14“ hat Teilnehmer, aber keine Strecke"
    ):
        speichern(session, v, zeilen, zuordnung)
    # Klassen ohne Teilnehmer wandern beim Entfernen auf die erste Strecke
    zuordnung["Jungen 12-14"] = "0"
    speichern(session, v, zeilen, zuordnung)
    liste = st.strecken_von(session, v)
    assert [s.name for s in liste] == ["Erwachsene", "Leer"]
    assert len(liste[0].klassen) == len(KLASSEN) + 1


@pytest.mark.parametrize("aenderung, meldung", [
    ({"name": ""}, "Name fehlt"),
    ({"name": "5 km"}, "doppelt"),
    ({"laenge": ""}, "Streckenlänge Pflicht"),
    ({"laenge": "abc"}, "ungültig"),
])  # fmt: skip
def test_speichern_fehler(session, aenderung, meldung):
    v = veranstaltung(session)  # A.1 → 5 km und 20 km
    zeilen, zuordnung = formular(session, v)
    zeilen[1].update(aenderung)
    with pytest.raises(st.StreckenFehler, match=meldung):
        speichern(session, v, zeilen, zuordnung)


def test_eingefroren_nur_name_und_laenge(session):
    v = veranstaltung(session)
    ereignis(session, v, "START", startnummer=1, zeit_ms=T0)
    sz.einfrieren(session, v)
    session.commit()
    zeilen, zuordnung = formular(session, v)
    zuordnung["Jungen 12-14"] = "1"
    with pytest.raises(st.StreckenFehler, match="eingefroren"):
        speichern(session, v, zeilen, zuordnung)
    zeilen, zuordnung = formular(session, v)
    with pytest.raises(st.StreckenFehler, match="eingefroren"):
        speichern(session, v, zeilen + [{"id": "", "name": "Neu", "laenge": "3"}], zuordnung)
    zeilen[1]["entfernen"] = True
    with pytest.raises(st.StreckenFehler, match="eingefroren"):
        speichern(session, v, zeilen, zuordnung)
    # Name und Länge bleiben änderbar; ohne übermittelte Zuordnung (gesperrte Auswahl) bleibt sie
    zeilen, _ = formular(session, v)
    zeilen[1].update(name="Erwachsene", laenge="20,4")
    speichern(session, v, zeilen, {})
    assert [(s.name, s.laenge_km) for s in st.strecken_von(session, v)] == [
        ("5 km", 5.0),
        ("Erwachsene", 20.4),
    ]
    assert sorted(st.zuordnung(st.strecken_von(session, v))) == sorted(
        st.zuordnungsklassen(KLASSEN)
    )
    # nach „offiziell“ ist die Länge gesperrt, der Name nicht
    zeilen, _ = formular(session, v)
    zeilen[1]["laenge"] = "20,5"
    with pytest.raises(st.StreckenFehler, match="offiziell"):
        speichern(session, v, zeilen, {}, offiziell=True)
    zeilen[1].update(laenge="20,4", name="Lang")
    speichern(session, v, zeilen, {}, offiziell=True)
    assert st.strecken_von(session, v)[1].name == "Lang"


def _laengen(session, v):
    return [(s.name, s.laenge_km) for s in st.strecken_von(session, v)]


def test_satzwechsel_legt_keine_strecken_an(session):
    """Segmente 49: Ein Wechsel von Klassen- oder Wertungssatz schlägt keine Strecken mehr vor –
    die Strecken bleiben, neue Klassen gehen an die Strecke passender Länge bzw. die letzte."""
    v = veranstaltung(session, wertung=False)  # „Strecke 1“ mit allen Klassen
    hinweise = sz.regeln_setzen(session, v, v.klassensatz_id, ws(session).id, 20.0)
    assert _laengen(session, v) == [("Strecke 1", 20.0)] and not hinweise
    zeilen, zuordnung = formular(session, v)
    zeilen.append({"id": "", "name": "Jugend", "laenge": "5,1"})
    speichern(session, v, zeilen, zuordnung)
    sz.regeln_setzen(session, v, v.klassensatz_id, None, None)  # Wertung aus: nichts verschwindet
    assert _laengen(session, v) == [("Strecke 1", 20.0), ("Jugend", 5.1)]
    # anderer Klassensatz: Klassen, die es nicht mehr gibt, fallen weg; neue → Länge bzw. letzte
    mini, _ = sz.klassensatz_speichern(session, None, "Klein", [test_saetze.zeile("Herren", "m", 18, 99),
                                                                 test_saetze.zeile("U15", "m", 0, 14)])  # fmt: skip
    hinweise = sz.regeln_setzen(session, v, mini.id, None, None)
    assert _laengen(session, v) == [("Strecke 1", 20.0), ("Jugend", 5.1)]
    zu = st.zuordnung(st.strecken_von(session, v))
    assert zu == {"Herren": 1, "U15": 1, "Offen": 0}  # „Offen“ gab es schon
    assert "neu zugeordnet: Herren, U15" in hinweise[0]


def test_plaetze_im_assistenten(session):
    """Segmente 49: je Längen-Platz entscheiden – Platzhalter anlegen; die vorläufige Strecke fällt
    weg, sobald sie keine Klassen mehr hat."""
    v = veranstaltung(session, wertung=False)
    v.einrichtung_schritt, v.einrichtung_json = 2, '{"plaetze": {}}'
    sz.regeln_setzen(session, v, v.klassensatz_id, ws(session).id, None)
    plaetze = st.plaetze(session, v)
    assert [(p.name, p.laenge_km, p.strecke) for p in plaetze] == [
        ("5 km", 5.0, None),
        ("20 km", 20.0, None),
    ]
    assert _laengen(session, v) == [("Strecke 1", None)]  # nichts angelegt
    st.platzhalter_anlegen(session, v, "20 km", "20 km", "20,3")
    assert [p.name for p in st.plaetze_offen(session, v)] == ["5 km"]
    with pytest.raises(st.StreckenFehler, match="gibt es schon"):
        st.platzhalter_anlegen(session, v, "5 km", "20 km", "5")
    st.platzhalter_anlegen(session, v, "5 km", "Jugend", "5,0")
    assert _laengen(session, v) == [("20 km", 20.3), ("Jugend", 5.0)]  # „Strecke 1“ weg
    zu = st.zuordnung(st.strecken_von(session, v))
    assert zu["Jungen 12-14"] == 1 and zu["Herren 31-40"] == 0
    assert st.plaetze_offen(session, v) == []
    # ein Satzwechsel lässt Entscheidungen für Plätze, die es noch gibt, stehen
    sz.regeln_setzen(session, v, v.klassensatz_id, sz.kopieren(session, ws(session)).id, None)
    assert st.plaetze_offen(session, v) == []
