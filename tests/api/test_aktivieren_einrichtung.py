"""Aktiv setzen erst nach abgeschlossener Einrichtung – auf jedem Weg, auf dem eine
Veranstaltung aktiv wird; eine bestehende aktive mit offener Einrichtung bleibt unverändert."""

import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import demo, saetze
from app import korrekturen as k
from app.db import erzeuge_engine, erzeuge_sessionmaker, tabellen_anlegen
from app.einstellungen import Einstellungen
from app.main import create_app
from app.models import Veranstaltung, aktive_veranstaltung
from tests.api.test_admin_saetze import csrf, post

PROJEKT = Path(__file__).resolve().parents[2]
GESPERRT = "Aktivieren erst möglich, wenn die Einrichtung abgeschlossen ist"


@pytest.fixture
def admin(client):
    client.post("/admin/anmelden", data={"token": "tok-admin"})
    client.csrf = csrf(client.get("/admin").text)
    return client


def lesen(admin, vid) -> Veranstaltung:
    with admin.app.state.sessionmaker() as s:
        return s.get(Veranstaltung, vid)


def setzen(admin, vid, **felder):
    with admin.app.state.sessionmaker() as s:
        w = s.get(Veranstaltung, vid)
        for name, wert in felder.items():
            setattr(w, name, wert)
        s.commit()


def im_assistenten(admin, name="Kottenforst") -> int:
    """Über den Assistenten angelegt: inaktiv, Schritt 2 offen."""
    post(admin, "/admin/einrichtung/neu", name=name, datum="2026-10-10")
    with admin.app.state.sessionmaker() as s:
        return s.scalars(select(Veranstaltung.id).where(Veranstaltung.name == name)).one()


# --- Zentrale Prüfung --------------------------------------------------------


def test_zentrale_pruefung_gesperrt_und_danach_erlaubt(admin):
    vid = im_assistenten(admin)
    with admin.app.state.sessionmaker() as s:
        v = s.get(Veranstaltung, vid)
        with pytest.raises(k.KorrekturFehler, match=GESPERRT):
            k.aktiv_setzen(s, v)
        assert not s.get(Veranstaltung, vid).aktiv
        v.einrichtung_schritt = None
        s.commit()
        k.aktiv_setzen(s, v)
        assert aktive_veranstaltung(s).id == vid


# --- Admin-Knopf „Als aktive Veranstaltung setzen …“ ---------------------------


@pytest.mark.parametrize("schritt", [2, 3, 4, 5])
def test_admin_knopf_bei_offener_einrichtung(admin, schritt):
    vid = im_assistenten(admin)
    setzen(admin, vid, einrichtung_schritt=schritt)
    status = admin.get(f"/admin/v/{vid}/status").text
    block = re.search(
        r'<section class="card card-kopf" id="aktiv">.*?</section>', status, re.DOTALL
    ).group(0)
    assert 'id="aktivieren"' not in block  # statt des Knopfs der Hinweis
    assert f'<span class="hint" id="aktivieren-grund">{GESPERRT}' in block
    assert (
        f'href="/admin/einrichtung/{vid}/{schritt}" id="einrichtung-fortsetzen">Einrichtung fortsetzen</a>'
        in block
    )
    # Server direkt aufgerufen: verständliche Meldung, keine Änderung
    r = post(admin, f"/admin/v/{vid}/aktivieren")
    assert GESPERRT in r.text and f"Schritt {schritt} von 5 ist offen" in r.text
    w = lesen(admin, vid)
    assert not w.aktiv and w.einrichtung_schritt == schritt


def test_admin_knopf_nach_abgeschlossener_einrichtung(admin):
    vid = im_assistenten(admin)
    setzen(admin, vid, einrichtung_schritt=None)
    status = admin.get(f"/admin/v/{vid}/status").text
    assert f'action="/admin/v/{vid}/aktivieren"' in status and GESPERRT not in status
    assert "ist jetzt die aktive Veranstaltung" in post(admin, f"/admin/v/{vid}/aktivieren").text
    assert lesen(admin, vid).aktiv


def test_admin_knopf_beide_pruefungen(admin):
    """Die Sperre „laufende nicht verdrängen“ bleibt; bei offener Einrichtung zählt zuerst die."""
    laufend = k_laufende(admin)
    vid = im_assistenten(admin)
    r = post(admin, f"/admin/v/{vid}/aktivieren")
    assert GESPERRT in r.text
    setzen(admin, vid, einrichtung_schritt=None)
    r = post(admin, f"/admin/v/{vid}/aktivieren")
    assert "läuft (Startzeiten vorhanden)" in r.text
    assert lesen(admin, laufend).aktiv and not lesen(admin, vid).aktiv


def k_laufende(admin) -> int:
    """Eine aktive Veranstaltung mit Startzeit (ohne Assistent angelegt)."""
    with admin.app.state.sessionmaker() as s:
        saetze.standardsaetze_anlegen(s)
        v = k.veranstaltung_anlegen(s, "Läuft", demo.DATUM, aktivieren=True)
        k.teilnehmer_speichern(s, v, None, {"startnummer": "1", "vorname": "A", "nachname": "B",
                                            "geschlecht": "m", "geburtsjahr": "1980"})  # fmt: skip
        from app import api
        from app.auth import Zugang

        api.ereignisse_annehmen(s, Zugang("start", v), api.Batch(ereignisse=[
            {"uuid": "s1", "art": "START", "startnummer": 1, "zeit_ms": 1, "erstellt_ms": 1, "geraet": "g"}]))  # fmt: skip
        return v.id


# --- Assistent, letzter Schritt ------------------------------------------------


@pytest.mark.parametrize("schritt", [2, 3, 4])
def test_assistent_abschluss_erst_im_letzten_schritt(admin, schritt):
    vid = im_assistenten(admin)
    setzen(admin, vid, einrichtung_schritt=schritt)
    # Zusammenfassung per Adresse: zurück zum offenen Schritt
    r = admin.get(f"/admin/einrichtung/{vid}/5", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/admin/einrichtung/{vid}/{schritt}"
    # „Einrichtung abschließen“ direkt aufgerufen: Meldung, keine Änderung
    r = post(admin, f"/admin/einrichtung/{vid}/5", aktivieren="1")
    assert f"Erst Schritt {schritt} abschließen" in r.text
    w = lesen(admin, vid)
    assert not w.aktiv and w.einrichtung_schritt == schritt


def test_assistent_kein_schritt_uebersprungen(admin):
    """Auch „Weiter“ aus Schritt 3 oder 4 per Adresse schiebt die Einrichtung nicht voran."""
    vid = im_assistenten(admin)
    r = post(admin, f"/admin/einrichtung/{vid}/3")
    assert "Erst Schritt 2 abschließen" in r.text and lesen(admin, vid).einrichtung_schritt == 2
    r = post(admin, f"/admin/v/{vid}/zielmonitor", weiter="1", ziel=f"/admin/einrichtung/{vid}/4")
    assert "Erst Schritt 2 abschließen" in r.text and lesen(admin, vid).einrichtung_schritt == 2
    r = admin.get(f"/admin/einrichtung/{vid}/4", follow_redirects=False)
    assert r.headers["location"] == f"/admin/einrichtung/{vid}/2"
    assert admin.get(f"/admin/einrichtung/{vid}/1", follow_redirects=False).status_code == 200


def test_assistent_abschluss_im_letzten_schritt(admin):
    vid = im_assistenten(admin)
    setzen(admin, vid, einrichtung_schritt=5)
    assert 'id="zusammenfassung"' in admin.get(f"/admin/einrichtung/{vid}/5").text
    r = post(admin, f"/admin/einrichtung/{vid}/5", aktivieren="1")
    assert "Einrichtung abgeschlossen" in r.text and "ist jetzt die aktive Veranstaltung" in r.text
    w = lesen(admin, vid)
    assert w.aktiv and w.einrichtung_schritt is None
    # Danach bleibt der Assistent erreichbar (alle Schritte), ändert aber nichts mehr am Abschluss
    assert admin.get(f"/admin/einrichtung/{vid}/5", follow_redirects=False).status_code == 200


# --- Anlegen ohne Assistent: gilt als fertig eingerichtet ------------------------


def test_post_veranstaltungen_gilt_als_fertig(admin):
    r = post(admin, "/admin/veranstaltungen", name="Skript", datum="2026-10-10", aktivieren="1")
    assert r.status_code == 200
    with admin.app.state.sessionmaker() as s:
        v = s.scalars(select(Veranstaltung).where(Veranstaltung.name == "Skript")).one()
        assert v.aktiv and v.einrichtung_schritt is None


def test_cli_veranstaltung_gilt_als_fertig_und_nutzt_die_zentrale_pruefung(
    tmp_path, monkeypatch, capsys
):
    from app import verwaltung

    db = str(tmp_path / "cli.sqlite3")
    monkeypatch.setenv("TAPTRIAL_DB_PFAD", db)
    assert verwaltung.main(["veranstaltung", "Kottenforst", "2026-10-10"]) == 0
    engine = erzeuge_engine(db)
    with erzeuge_sessionmaker(engine)() as s:
        v = aktive_veranstaltung(s)
        assert v.name == "Kottenforst" and v.einrichtung_schritt is None
    engine.dispose()
    gerufen = []
    original = k.aktiv_setzen
    monkeypatch.setattr(k, "aktiv_setzen", lambda s, v: (gerufen.append(v.name), original(s, v)))
    assert verwaltung.main(["veranstaltung", "Zweite", "2026-10-11"]) == 0
    assert gerufen == ["Zweite"]


def test_simulation_gilt_als_fertig(tmp_path):
    db = tmp_path / "sim.sqlite3"
    r = subprocess.run([sys.executable, "scripts/simulieren.py", "--db", str(db)], cwd=PROJEKT,
                       capture_output=True, text=True, check=False)  # fmt: skip
    assert r.returncode == 0, r.stderr
    engine = erzeuge_engine(str(db))
    with erzeuge_sessionmaker(engine)() as s:
        v = aktive_veranstaltung(s)
        assert v is not None and v.einrichtung_schritt is None
    engine.dispose()


# --- Demo und Bestand ------------------------------------------------------------


def bestand(db, schritt=3) -> int:
    """Wie auf einer älteren Installation: aktiv, Einrichtung offen."""
    engine = erzeuge_engine(db)
    tabellen_anlegen(engine)
    with erzeuge_sessionmaker(engine)() as s:
        saetze.standardsaetze_anlegen(s)
        v = k.veranstaltung_anlegen(s, "Bestand", demo.DATUM.replace(day=18), aktivieren=False)
        v.einrichtung_schritt = schritt
        v.aktiv = True
        s.commit()
        vid = v.id
    engine.dispose()
    return vid


def test_demo_laesst_bestand_unveraendert(tmp_path):
    db = str(tmp_path / "demo.sqlite3")
    vid = bestand(db)
    assert demo.main(["--db", db]) == 0
    engine = erzeuge_engine(db)
    with erzeuge_sessionmaker(engine)() as s:
        v = s.get(Veranstaltung, vid)
        assert v.aktiv and v.einrichtung_schritt == 3
        d = s.scalars(select(Veranstaltung).where(Veranstaltung.name == demo.NAME)).one()
        assert not d.aktiv and d.einrichtung_schritt is None and d.zustand == "abgeschlossen"
    engine.dispose()


def test_bestand_beim_start_unveraendert_mit_hinweis(tmp_path):
    db = str(tmp_path / "bestand.sqlite3")
    vid = bestand(db, schritt=3)
    with TestClient(create_app(Einstellungen(db_pfad=db, token_admin="tok-admin"))) as c:
        c.post("/admin/anmelden", data={"token": "tok-admin"})
        html = c.get(f"/admin/v/{vid}").text
        block = re.search(r'data-aufgabe="einrichtung">.*?</a>', html, re.DOTALL).group(0)
        assert "Einrichtung abschließen" in block and f'href="/admin/einrichtung/{vid}/3"' in block
        with c.app.state.sessionmaker() as s:
            v = s.get(Veranstaltung, vid)
            assert v.aktiv and v.einrichtung_schritt == 3
