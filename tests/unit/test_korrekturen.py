"""Fachschicht des Admin (Phase 6): Korrekturen, DSQ, Ergebnisstatus, Lebenszyklus, Teilnehmer."""

import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app import korrekturen as k
from app.db import erzeuge_engine, erzeuge_sessionmaker, tabellen_anlegen
from app.einstellungen import STANDARD_KLASSEN_DATEI
from app.klassen import lade_klassen
from app.models import Ereignis, Teilnehmer, Veranstaltung

BERLIN = ZoneInfo("Europe/Berlin")
T0 = int(datetime(2026, 10, 10, 10, 0, tzinfo=BERLIN).timestamp() * 1000)
MIN = 60_000


@pytest.fixture(scope="module")
def klassen():
    return lade_klassen(STANDARD_KLASSEN_DATEI)


@pytest.fixture
def session(tmp_path):
    engine = erzeuge_engine(str(tmp_path / "k.sqlite3"))
    tabellen_anlegen(engine)
    with erzeuge_sessionmaker(engine)() as s:
        yield s
    engine.dispose()


@pytest.fixture
def v(session):
    v = k.veranstaltung_anlegen(session, "Kottenforst", date(2026, 10, 10), aktivieren=True)
    for nr, g, j in ((1, "w", 1990), (2, "m", 1985), (3, "m", 1970)):
        session.add(Teilnehmer(veranstaltung_id=v.id, startnummer=nr, vorname=f"V{nr}",
                               nachname=f"N{nr}", geschlecht=g, geburtsjahr=j, verein="RC"))  # fmt: skip
    session.commit()
    return v


def geraet(session, v, art, **kw):
    e = Ereignis(uuid=str(uuid.uuid4()), veranstaltung_id=v.id, art=art, geraet="test",
                 erstellt_ms=kw.get("zeit_ms") or T0, empfangen_ms=T0, **kw)  # fmt: skip
    session.add(e)
    session.commit()
    return e


def erg(session, v, klassen=None):
    return k.ergebnis(session, v)


def zeile(ergebnis, nr):
    return ergebnis.zeile(nr)


def protokoll_notizen(session, v):
    return [e.notiz for e in session.scalars(select(Ereignis).where(Ereignis.geraet == "admin"))]


# --- Korrekturen ------------------------------------------------------------


def test_umbuchen(session, v, klassen):
    geraet(session, v, "START", startnummer=1, zeit_ms=T0)
    geraet(session, v, "START", startnummer=2, zeit_ms=T0 + MIN)
    z = geraet(session, v, "ZIEL", startnummer=1, zeit_ms=T0 + 12 * MIN)
    k.umbuchen(session, v, z.uuid, 2, "falsch angetippt")
    e = erg(session, v)
    assert zeile(e, 2).fahrzeit_ms == 11 * MIN and zeile(e, 1).fahrzeit_ms is None
    assert "falsch angetippt" in protokoll_notizen(session, v)


def test_tauschen(session, v, klassen):
    for nr in (1, 2):
        geraet(session, v, "START", startnummer=nr, zeit_ms=T0)
    geraet(session, v, "ZIEL", startnummer=1, zeit_ms=T0 + 10 * MIN)
    geraet(session, v, "ZIEL", startnummer=2, zeit_ms=T0 + 11 * MIN)
    k.tauschen(session, v, 1, 2, "ZIEL", "Nummern vertauscht")
    e = erg(session, v)
    assert (zeile(e, 1).fahrzeit_ms, zeile(e, 2).fahrzeit_ms) == (11 * MIN, 10 * MIN)
    assert e.konflikte == []


def test_tauschen_braucht_je_einen_stempel(session, v, klassen):
    geraet(session, v, "ZIEL", startnummer=1, zeit_ms=T0 + 10 * MIN)
    with pytest.raises(k.KorrekturFehler, match="Nr. 2"):
        k.tauschen(session, v, 1, 2, "ZIEL", "x")


def test_stornieren(session, v, klassen):
    geraet(session, v, "START", startnummer=1, zeit_ms=T0)
    z1 = geraet(session, v, "ZIEL", startnummer=1, zeit_ms=T0 + 10 * MIN)
    geraet(session, v, "ZIEL", startnummer=1, zeit_ms=T0 + 11 * MIN)
    assert zeile(erg(session, v), 1).status == "KONFLIKT"
    k.stornieren(session, v, z1.uuid, "Doppelstempel")
    assert zeile(erg(session, v), 1).fahrzeit_ms == 11 * MIN


def test_manuelle_zeit_als_uhrzeit_und_als_fahrzeit(session, v, klassen):
    geraet(session, v, "START", startnummer=1, zeit_ms=T0)
    k.manuelle_zeit(session, v, 1, "ZIEL", "10:15:30,4", None, "Handstoppuhr")
    z = zeile(erg(session, v), 1)
    assert z.fahrzeit_ms == 15 * MIN + 30_400 and z.manuell
    geraet(session, v, "START", startnummer=2, zeit_ms=T0 + MIN)
    k.manuelle_zeit(session, v, 2, "ZIEL", None, "20:05,3", "Video")
    assert zeile(erg(session, v), 2).fahrzeit_ms == 20 * MIN + 5_300


def test_manuelle_fahrzeit_ohne_start_ist_fehler(session, v, klassen):
    with pytest.raises(k.KorrekturFehler, match="Startzeit"):
        k.manuelle_zeit(session, v, 1, "ZIEL", None, "20:00", "x")


@pytest.mark.parametrize("text", ["25:00:00", "10:61:00", "abc", ""])
def test_ungueltige_uhrzeit(session, v, klassen, text):
    with pytest.raises(k.KorrekturFehler):
        k.manuelle_zeit(session, v, 1, "ZIEL", text, None, "x")


@pytest.mark.parametrize("wert", ["DNS", "DNF", "DSQ"])
def test_status(session, v, klassen, wert):
    k.status_setzen(session, v, 1, wert, "Grund")
    assert zeile(erg(session, v), 1).status == wert


def test_notiz_ist_pflicht(session, v):
    with pytest.raises(k.KorrekturFehler, match="Notiz"):
        k.status_setzen(session, v, 1, "DNF", "  ")


def test_unbekannte_startnummer(session, v):
    with pytest.raises(k.KorrekturFehler, match="99"):
        k.status_setzen(session, v, 99, "DNF", "x")


# --- DSQ zweistufig und Ergebnisstatus --------------------------------------


def _vorlaeufiger_dsq(session, v, nr=1):
    geraet(session, v, "STATUS", startnummer=nr, wert="DSQ_VORLAEUFIG", notiz="Windschatten")


def test_dsq_bestaetigen(session, v, klassen):
    _vorlaeufiger_dsq(session, v)
    assert [z.startnummer for z in erg(session, v).vorlaeufige_dsq] == [1]
    k.dsq_bestaetigen(session, v, 1, "Video eindeutig")
    e = erg(session, v)
    assert zeile(e, 1).status == "DSQ" and e.vorlaeufige_dsq == []


def test_dsq_verwerfen(session, v, klassen):
    _vorlaeufiger_dsq(session, v)
    k.dsq_verwerfen(session, v, 1, "kein Verstoß")
    e = erg(session, v)
    assert zeile(e, 1).dsq_vorlaeufig is None and zeile(e, 1).status != "DSQ"
    assert "kein Verstoß" in protokoll_notizen(session, v)


def test_bestaetigen_ohne_vorlaeufigen_dsq_ist_fehler(session, v, klassen):
    with pytest.raises(k.KorrekturFehler, match="vorläufig"):
        k.dsq_bestaetigen(session, v, 1, "x")


def test_ergebnis_offiziell_gesperrt_und_dann_moeglich(session, v, klassen):
    geraet(session, v, "START", startnummer=1, zeit_ms=T0)
    geraet(session, v, "ZIEL", startnummer=1, zeit_ms=T0 + 10 * MIN)
    _vorlaeufiger_dsq(session, v)
    with pytest.raises(k.KorrekturFehler, match="vorläufige DSQ"):
        k.ergebnis_offiziell(session, v, "fertig")
    k.dsq_verwerfen(session, v, 1, "ok")
    z = geraet(session, v, "ZIEL", zeit_ms=T0 + 11 * MIN)  # offene Zeit = Konflikt
    with pytest.raises(k.KorrekturFehler, match="Konflikt"):
        k.ergebnis_offiziell(session, v, "fertig")
    k.stornieren(session, v, z.uuid, "Fehlstempel")
    k.ergebnis_offiziell(session, v, "fertig")
    e = erg(session, v)
    assert e.offiziell and abs(e.offiziell_seit_ms - k.jetzt_ms()) < 5000  # Serverzeit
    k.ergebnis_vorlaeufig(session, v, "Einspruch")
    assert not erg(session, v).offiziell
    arten = [x.art for x in session.scalars(select(Ereignis).where(Ereignis.art == "ERGEBNIS"))]
    assert arten == ["ERGEBNIS", "ERGEBNIS"]


# --- Veranstaltung, Tokens, Lebenszyklus ------------------------------------


def test_aktivieren_deaktiviert_andere(session, v):
    w = k.veranstaltung_anlegen(session, "Zweite", date(2026, 11, 1), aktivieren=True)
    session.refresh(v)
    assert w.aktiv and not v.aktiv


def test_token_erneuern(session, v):
    alt_start, alt_ziel = v.token_start, v.token_ziel
    k.token_erneuern(session, v, "start")
    assert v.token_start != alt_start and v.token_ziel == alt_ziel
    with pytest.raises(k.KorrekturFehler):
        k.token_erneuern(session, v, "admin")


def test_abschliessen_macht_schreibgeschuetzt(session, v, klassen):
    k.abschliessen(session, v)
    assert v.zustand == "abgeschlossen"
    with pytest.raises(k.KorrekturFehler, match="schreibgeschützt"):
        k.status_setzen(session, v, 1, "DNF", "x")
    with pytest.raises(k.KorrekturFehler, match="schreibgeschützt"):
        k.teilnehmer_speichern(session, v, None, {"startnummer": "9"})


def test_anonymisieren(session, v, klassen):
    geraet(session, v, "START", startnummer=1, zeit_ms=T0)
    geraet(session, v, "ZIEL", startnummer=1, zeit_ms=T0 + 10 * MIN)
    vorher = zeile(erg(session, v), 1)
    with pytest.raises(k.KorrekturFehler, match="abgeschlossen"):
        k.anonymisieren(session, v)
    k.abschliessen(session, v)
    k.anonymisieren(session, v)
    assert v.zustand == "anonymisiert"
    t = session.scalars(select(Teilnehmer).where(Teilnehmer.startnummer == 1)).one()
    assert (t.vorname, t.nachname, t.verein, t.geburtsjahr) == ("", "", None, 0)
    nachher = zeile(erg(session, v), 1)
    assert (nachher.klasse, nachher.fahrzeit_ms, nachher.status, nachher.rang_gesamt) == (
        vorher.klasse, vorher.fahrzeit_ms, vorher.status, vorher.rang_gesamt,
    )  # fmt: skip


def test_faellige_veranstaltungen(session, v):
    k.abschliessen(session, v)
    assert k.faellige(session, date(2027, 4, 9)) == []
    assert k.faellige(session, date(2027, 4, 10)) == [v]  # Standardfrist: Datum + 6 Monate
    assert v.zustand == "abgeschlossen"  # keine automatische Anonymisierung


def test_loeschen_trotz_append_only(session, v, klassen):
    andere = k.veranstaltung_anlegen(session, "Bleibt", date(2026, 11, 1), aktivieren=False)
    geraet(session, v, "START", startnummer=1, zeit_ms=T0)
    geraet(session, andere, "START", startnummer=1, zeit_ms=T0)
    k.loeschen(session, v)
    assert session.get(Veranstaltung, v.id) is None
    rest = session.scalars(select(Ereignis)).all()
    assert [e.veranstaltung_id for e in rest] == [andere.id]
    assert (
        session.scalars(select(Teilnehmer).where(Teilnehmer.veranstaltung_id == v.id)).all() == []
    )
    # Trigger ist danach wieder aktiv
    with pytest.raises(Exception, match="append-only"):
        session.execute(Ereignis.__table__.delete())
        session.commit()
    session.rollback()


# --- Teilnehmer --------------------------------------------------------------


def test_teilnehmer_anlegen_und_bearbeiten(session, v, klassen):
    t = k.teilnehmer_speichern(session, v, None, {"startnummer": "7", "vorname": "Eva",
        "nachname": "Neu", "geschlecht": "W", "geburtsjahr": "1999", "verein": ""})  # fmt: skip
    assert (t.startnummer, t.geschlecht, t.verein) == (7, "w", None)
    k.teilnehmer_speichern(session, v, t.id, {"startnummer": "8", "vorname": "Eva",
        "nachname": "Neu", "geschlecht": "w", "geburtsjahr": "1999", "verein": "RC"})  # fmt: skip
    assert (t.startnummer, t.verein) == (8, "RC")


@pytest.mark.parametrize(
    ("daten", "meldung"),
    [
        ({"startnummer": "1"}, "bereits vergeben"),
        ({"startnummer": "x"}, "Startnummer"),
        ({"geschlecht": "q"}, "Geschlecht"),
        ({"geburtsjahr": "2024"}, "Altersklasse"),
        ({"vorname": ""}, "Vorname"),
    ],
)
def test_teilnehmer_validierung(session, v, klassen, daten, meldung):
    basis = {"startnummer": "9", "vorname": "A", "nachname": "B", "geschlecht": "m",
             "geburtsjahr": "1980", "verein": ""}  # fmt: skip
    with pytest.raises(k.KorrekturFehler, match=meldung):
        k.teilnehmer_speichern(session, v, None, {**basis, **daten})


def test_startnummer_mit_ereignissen_nicht_aenderbar(session, v, klassen):
    geraet(session, v, "START", startnummer=1, zeit_ms=T0)
    t = session.scalars(select(Teilnehmer).where(Teilnehmer.startnummer == 1)).one()
    daten = {"startnummer": "11", "vorname": "V1", "nachname": "N1", "geschlecht": "w",
             "geburtsjahr": "1990", "verein": ""}  # fmt: skip
    with pytest.raises(k.KorrekturFehler, match="Ereignisse"):
        k.teilnehmer_speichern(session, v, t.id, daten)
    with pytest.raises(k.KorrekturFehler, match="Ereignisse"):
        k.teilnehmer_loeschen(session, v, t.id)
    t3 = session.scalars(select(Teilnehmer).where(Teilnehmer.startnummer == 3)).one()
    k.teilnehmer_loeschen(session, v, t3.id)
    assert session.get(Teilnehmer, t3.id) is None


def test_stempelliste(session, v, klassen):
    geraet(session, v, "START", startnummer=1, zeit_ms=T0)
    z = geraet(session, v, "ZIEL", zeit_ms=T0 + 10 * MIN)
    liste = k.stempel(session, v)
    assert [(s.art, s.startnummer) for s in liste] == [("START", 1), ("ZIEL", None)]
    assert liste[1].uuid == z.uuid and liste[1].uhrzeit == "10:10:00,0"


# --- Merkmale ------------------------------------------------------------------


def _merkmal_ereignisse(session):
    return [(e.startnummer, e.wert, e.notiz) for e in
            session.scalars(select(Ereignis).where(Ereignis.art == "MERKMAL"))]  # fmt: skip


def test_merkmal_umschalten_mit_protokoll(session, v):
    t = session.scalars(select(Teilnehmer).where(Teilnehmer.startnummer == 2)).one()
    k.merkmal_setzen(session, v, t.id, "ausser_konkurrenz", True)
    k.merkmal_setzen(session, v, t.id, "ausser_konkurrenz", True)  # unverändert: kein Eintrag
    k.merkmal_setzen(session, v, t.id, "abzeichen", True)
    assert t.ausser_konkurrenz and t.abzeichen
    assert _merkmal_ereignisse(session) == [
        (2, "AUSSER_KONKURRENZ_JA", "Außer Konkurrenz: ja (Nr. 2)"),
        (2, "ABZEICHEN_JA", "Abzeichen: ja (Nr. 2)"),
    ]
    with pytest.raises(k.KorrekturFehler):
        k.merkmal_setzen(session, v, t.id, "quatsch", True)


def test_merkmal_im_formular_geaendert_wird_protokolliert(session, v, klassen):
    t = session.scalars(select(Teilnehmer).where(Teilnehmer.startnummer == 1)).one()
    daten = {"startnummer": "1", "vorname": "V1", "nachname": "N1", "geschlecht": "w",
             "geburtsjahr": "1990", "verein": "RC", "ausser_konkurrenz": "1"}  # fmt: skip
    k.teilnehmer_speichern(session, v, t.id, daten)
    assert t.ausser_konkurrenz and not t.abzeichen  # Abzeichen nicht im Formular: unverändert
    assert _merkmal_ereignisse(session) == [
        (1, "AUSSER_KONKURRENZ_JA", "Außer Konkurrenz: ja (Nr. 1)")
    ]


def test_merkmal_schreibgeschuetzt_nach_abschluss(session, v):
    k.abschliessen(session, v)
    t = session.scalars(select(Teilnehmer)).first()
    with pytest.raises(k.KorrekturFehler, match="schreibgeschützt"):
        k.merkmal_setzen(session, v, t.id, "abzeichen", True)


def test_ausser_konkurrenz_im_ergebnis(session, v, klassen):
    for nr, minuten in ((1, 30), (2, 25), (3, 28)):
        geraet(session, v, "START", startnummer=nr, zeit_ms=T0)
        geraet(session, v, "ZIEL", startnummer=nr, zeit_ms=T0 + minuten * MIN)
    t2 = session.scalars(select(Teilnehmer).where(Teilnehmer.startnummer == 2)).one()
    k.merkmal_setzen(session, v, t2.id, "ausser_konkurrenz", True)
    e = erg(session, v)
    assert (zeile(e, 2).rang_gesamt, zeile(e, 3).rang_gesamt, zeile(e, 1).rang_gesamt) == (
        None,
        1,
        2,
    )


# --- genau eine aktive Veranstaltung ------------------------------------------------


def test_zweites_aktivieren_deaktiviert_das_erste(session, v):
    w = k.veranstaltung_anlegen(session, "Zweite", date(2026, 11, 1), aktivieren=False)
    k.aktiv_setzen(session, w)
    session.refresh(v)
    assert w.aktiv and not v.aktiv
    k.aktiv_setzen(session, v)
    session.refresh(w)
    assert v.aktiv and not w.aktiv
    assert session.scalars(select(Veranstaltung).where(Veranstaltung.aktiv.is_(True))).all() == [v]


def test_zwei_aktive_direkt_schlaegt_fehl(session, v):
    from sqlalchemy.exc import IntegrityError

    session.add(Veranstaltung(name="Zweite", datum=date(2026, 11, 1), aktiv=True))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    w = Veranstaltung(name="Dritte", datum=date(2026, 11, 2), aktiv=False)
    session.add(w)
    session.commit()
    w.aktiv = True
    with pytest.raises(IntegrityError):
        session.commit()
