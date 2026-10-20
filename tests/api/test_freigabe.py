"""Rennfunktionen erst nach Freigabe (Einrichtung abgeschlossen und aktiv)."""

import json
import re
import uuid

import pytest

from app.models import Veranstaltung
from tests.api import test_admin
from tests.api.test_admin import MIN, T0, geraet, korr, post

admin, v, keine_sperre = test_admin.admin, test_admin.v, test_admin.keine_sperre  # Fixtures


def setzen(admin, v, **felder):
    with admin.app.state.sessionmaker() as s:
        w = s.get(Veranstaltung, v.id)
        for k, wert in felder.items():
            setattr(w, k, wert)
        s.commit()


def ev(art, **kw):
    return {"uuid": str(uuid.uuid4()), "art": art, "geraet": "test", "erstellt_ms": T0, **kw}


def senden(admin, token, *ereignisse):
    return admin.post("/api/ereignisse", json={"ereignisse": list(ereignisse)},
                      headers={"Authorization": f"Bearer {token}"})  # fmt: skip


@pytest.mark.parametrize(("felder", "fehlt"), [
    ({"einrichtung_schritt": 3}, ["einrichtung"]),
    ({"aktiv": False}, ["aktiv"]),
    ({"einrichtung_schritt": 2, "aktiv": False}, ["einrichtung", "aktiv"]),
])  # fmt: skip
def test_jede_rennfunktion_gesperrt_bis_zur_freigabe(admin, v, felder, fehlt):
    start = geraet(admin, v, "START", startnummer=1, zeit_ms=T0)  # vorher freigegeben
    ziel = geraet(admin, v, "ZIEL", startnummer=1, zeit_ms=T0 + 20 * MIN)
    setzen(admin, v, **felder)
    stand = admin.get("/api/stand", headers={"Authorization": f"Bearer {v.token_start}"}).json()
    assert stand["freigabe"]["frei"] is False
    assert [x["code"] for x in stand["freigabe"]["fehlt"]] == fehlt
    geraete = [
        (v.token_start, ev("START", startnummer=2, zeit_ms=T0)),
        (v.token_start, ev("STATUS", startnummer=3, wert="DNS")),
        (v.token_ziel, ev("ZIEL", zeit_ms=T0 + 30 * MIN)),
        (v.token_ziel, ev("ZUORDNUNG", bezug_uuid=ziel["uuid"], startnummer=2)),
        (v.token_ziel, ev("ZEITKORREKTUR", bezug_uuid=ziel["uuid"], delta_ms=1000)),
        (v.token_ziel, ev("STORNO", bezug_uuid=start["uuid"])),
        (v.token_ziel, ev("STATUS", startnummer=2, wert="DNF")),
        ("tok-admin", ev("STATUS", startnummer=2, wert="DSQ", notiz="x", veranstaltung_id=v.id)),
    ]
    for token, e in geraete:
        r = senden(admin, token, e)
        assert r.status_code == 423, (e["art"], r.text)
        detail = r.json()["detail"]
        assert (
            detail["grund"] == "nicht_freigegeben" and [x["code"] for x in detail["fehlt"]] == fehlt
        )
        assert detail["text"].startswith("Zeitnahme noch nicht freigegeben:")
    # Admin: manuelle Zeiten und Korrekturen
    for aktion, daten in (
        ("manuell", {"startnummer": "2", "art": "START", "uhrzeit": "10:00:00,0"}),
        ("umbuchen", {"stempel": ziel["uuid"], "startnummer": "2"}),
        ("stornieren", {"stempel": start["uuid"]}),
        ("status", {"startnummer": "2", "wert": "DNF"}),
    ):
        r = korr(admin, v, aktion, notiz="Test", **daten)
        assert "Zeitnahme noch nicht freigegeben" in r.text, aktion
    # Gerätedatei
    datei = json.dumps({"geraet": "start-x", "eintraege": [ev("START", startnummer=2, zeit_ms=T0)]})
    r = post(admin, f"/admin/v/{v.id}/geraetedatei/uebernehmen", inhalt=datei)
    assert "Zeitnahme noch nicht freigegeben" in r.text
    protokoll = admin.get(
        f"/api/ereignisse?veranstaltung_id={v.id}", headers={"Authorization": "Bearer tok-admin"}
    )
    assert len(protokoll.json()["ereignisse"]) == 2  # nichts dazugekommen
    # nach der Freigabe geht alles
    setzen(admin, v, einrichtung_schritt=None, aktiv=True)
    for token, e in geraete:
        assert senden(admin, token, e).status_code == 200, e["art"]
    assert "eingetragen" in korr(admin, v, "manuell", notiz="Test", startnummer="3", art="START",
                                 uhrzeit="10:00:00,0").text  # fmt: skip


def test_admin_nennt_den_grund_mit_knoepfen(admin, v):
    setzen(admin, v, einrichtung_schritt=3, aktiv=False)
    html = admin.get(f"/admin/v/{v.id}").text
    block = re.search(r'data-aufgabe="freigabe">.*?</a>', html, re.DOTALL).group(0)
    assert "Zeitnahme noch nicht freigegeben" in block and "Einrichtung abschließen" in block
    assert f'href="/admin/einrichtung/{v.id}/3"' in block
    assert 'data-aufgabe="einrichtung"' in html and 'data-aufgabe="aktiv"' in html
    geraete = admin.get(f"/admin/v/{v.id}/geraete").text
    assert "Zeitnahme gesperrt" in geraete
    assert f'href="/admin/einrichtung/{v.id}/3" data-schritt="einrichtung"' in geraete
    assert f'href="/admin/v/{v.id}/status#aktiv" data-schritt="aktiv"' in geraete
    # „Aktiv setzen“ gesperrt, solange die Einrichtung offen ist
    status = admin.get(f"/admin/v/{v.id}/status").text
    assert re.search(r'id="aktivieren" disabled', status)
    assert "Gesperrt: Erst die Einrichtung abschließen (Schritt 3 von 5 ist offen)." in status
    r = post(admin, f"/admin/v/{v.id}/aktivieren")
    assert "Erst die Einrichtung abschließen" in r.text
    setzen(admin, v, einrichtung_schritt=None)
    assert "ist jetzt die aktive Veranstaltung" in post(admin, f"/admin/v/{v.id}/aktivieren").text
    assert 'data-aufgabe="freigabe"' not in admin.get(f"/admin/v/{v.id}").text
    assert "Zeitnahme gesperrt" not in admin.get(f"/admin/v/{v.id}/geraete").text


def test_nicht_mehr_aktiv_setzen_nur_ohne_startzeit(admin, v):
    status = admin.get(f"/admin/v/{v.id}/status").text
    assert f'action="/admin/v/{v.id}/deaktivieren"' in status
    r = post(admin, f"/admin/v/{v.id}/deaktivieren")
    assert "nicht mehr aktiv" in r.text
    stand = admin.get("/api/stand", headers={"Authorization": f"Bearer {v.token_start}"}).json()
    assert stand["freigabe"] == {
        "frei": False,
        "fehlt": [{"code": "aktiv", "text": "Veranstaltung aktiv setzen"}],
    }
    assert senden(admin, v.token_start, ev("START", startnummer=1, zeit_ms=T0)).status_code == 423
    post(admin, f"/admin/v/{v.id}/aktivieren")
    geraet(admin, v, "START", startnummer=1, zeit_ms=T0)
    status = admin.get(f"/admin/v/{v.id}/status").text
    assert (
        re.search(r'id="deaktivieren" disabled', status)
        and "Es gibt bereits Startzeiten." in status
    )
    r = post(admin, f"/admin/v/{v.id}/deaktivieren")
    assert "Es gibt bereits Startzeiten." in r.text
    with admin.app.state.sessionmaker() as s:
        assert s.get(Veranstaltung, v.id).aktiv


def test_oeffentlich_ohne_freigabe_und_ohne_zeiten(admin, v):
    setzen(admin, v, aktiv=False)
    oeffentlich = admin.app  # gleicher Server, aber ohne Admin-Anmeldung
    from fastapi.testclient import TestClient

    gast = TestClient(oeffentlich)
    seite = gast.get(f"/ergebnisse?veranstaltung={v.id}").text
    assert "Für diese Veranstaltung gibt es noch keine Ergebnisse." in seite and "Anna" not in seite
    daten = gast.get(f"/api/oeffentlich?veranstaltung={v.id}").json()
    assert daten["keine_ergebnisse"] == "Für diese Veranstaltung gibt es noch keine Ergebnisse."
    assert "Für diese Veranstaltung" in gast.get(f"/ergebnisse/druck?veranstaltung={v.id}").text
    assert gast.get(f"/ergebnisse.csv?veranstaltung={v.id}").status_code == 404
    # Admin-Vorschau (Monitor-Vorschau, „Öffentliche Ergebnisse“ aus der Übersicht) bleibt möglich
    assert (
        "keine_ergebnisse"
        not in gast.get(f"/api/oeffentlich?veranstaltung={v.id}&vorschau=1").json()
    )
    assert f"/ergebnisse?veranstaltung={v.id}&amp;vorschau=1" in admin.get(f"/admin/v/{v.id}").text
    # mit Zeiten (vorher freigegeben, dann nicht mehr aktiv) wird gezeigt, was da ist
    setzen(admin, v, aktiv=True)
    geraet(admin, v, "START", startnummer=1, zeit_ms=T0)
    setzen(admin, v, aktiv=False)
    assert "keine_ergebnisse" not in gast.get(f"/api/oeffentlich?veranstaltung={v.id}").json()
    # abgeschlossene Veranstaltungen unverändert
    setzen(admin, v, zustand="abgeschlossen")
    assert "keine_ergebnisse" not in gast.get(f"/api/oeffentlich?veranstaltung={v.id}").json()


def test_laufende_veranstaltung_nicht_verdraengen(admin, v):
    """Hat die aktive Veranstaltung Startzeiten und ist sie nicht abgeschlossen, lässt
    sich keine andere aktiv setzen – Admin, Assistent und Neuanlage; ohne Startzeiten wie bisher."""
    from datetime import date

    from app import korrekturen as k

    with admin.app.state.sessionmaker() as s:
        andere = k.veranstaltung_anlegen(s, "Herbst", date(2026, 11, 1), aktivieren=False)
        aid = andere.id
    assert "ist jetzt die aktive Veranstaltung" in post(admin, f"/admin/v/{aid}/aktivieren").text
    post(admin, f"/admin/v/{v.id}/aktivieren")  # ohne Startzeiten: Wechsel geht
    geraet(admin, v, "START", startnummer=1, zeit_ms=T0)
    status = admin.get(f"/admin/v/{aid}/status").text
    assert re.search(r'id="aktivieren" disabled', status)
    assert "Gesperrt: ‚Kottenforst‘ läuft (Startzeiten vorhanden). Erst abschließen." in status
    assert f'href="/admin/v/{v.id}/status" id="zur-laufenden"' in status
    r = post(admin, f"/admin/v/{aid}/aktivieren")
    assert "‚Kottenforst‘ läuft (Startzeiten vorhanden). Erst abschließen." in r.text
    r = post(admin, "/admin/veranstaltungen", name="Noch eine", datum="2026-12-01", aktivieren="1")
    assert "läuft (Startzeiten vorhanden)" in r.text
    with admin.app.state.sessionmaker() as s:
        assert s.get(Veranstaltung, v.id).aktiv and not s.get(Veranstaltung, aid).aktiv
        assert s.query(Veranstaltung).filter_by(name="Noch eine").count() == 0
    # nach dem Abschließen wieder möglich
    post(admin, f"/admin/v/{v.id}/abschliessen", bestaetigung="1", name="Kottenforst")
    assert "ist jetzt die aktive Veranstaltung" in post(admin, f"/admin/v/{aid}/aktivieren").text
