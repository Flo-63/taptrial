"""Wer hat es ausgelöst? Admin-Ereignisse und Admin-Aktionen mit Person (Protokoll, Fahrer-Seite,
CSV); im Modus token „Admin (Passphrase)“."""

import re

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.einstellungen import Einstellungen
from app.main import create_app
from app.models import AdminAktion, Ereignis, Teilnehmer
from tests.api import test_admin
from tests.api.test_admin import MIN, T0, geraet, korr, post
from tests.proxy_hilfe import Anmeldedienst, einstellungen_proxy

admin, v, keine_sperre = test_admin.admin, test_admin.v, test_admin.keine_sperre  # Fixtures


def _aktionen(client):
    with client.app.state.sessionmaker() as s:
        return [(a.benutzer, a.aktion, a.veranstaltung_id) for a in s.scalars(select(AdminAktion))]


def test_modus_token_admin_passphrase(admin, v):
    geraet(admin, v, "START", startnummer=1, zeit_ms=T0 + MIN)
    korr(admin, v, "status", startnummer="1", wert="DNF", notiz="Sturz")
    with admin.app.state.sessionmaker() as s:
        e = s.scalars(select(Ereignis).where(Ereignis.art == "STATUS")).one()
        geraete = s.scalars(select(Ereignis).where(Ereignis.art == "START")).one()
        tid = s.scalars(select(Teilnehmer).where(Teilnehmer.startnummer == 1)).one().id
    assert e.benutzer == "Admin (Passphrase)" and geraete.benutzer is None
    assert "von Admin (Passphrase)" in admin.get(f"/admin/v/{v.id}/protokoll").text
    assert "von Admin (Passphrase)" in admin.get(f"/admin/v/{v.id}/teilnehmer/{tid}").text
    csv = admin.get(f"/admin/v/{v.id}/export/protokoll.csv").content.decode("utf-8-sig")
    assert csv.splitlines()[0].endswith("Ausgelöst von") and "Admin (Passphrase)" in csv
    assert ("Admin (Passphrase)", "Korrektur", v.id) in _aktionen(admin)
    html = admin.get(f"/admin/v/{v.id}/protokoll").text
    assert 'id="admin-aktionen"' in html and "Korrektur" in html


def test_abgelehnte_aktion_steht_nicht_im_protokoll(admin, v):
    vorher = len(_aktionen(admin))
    r = korr(admin, v, "status", startnummer="99", wert="DNF", notiz="gibt es nicht")
    assert "fehler" in str(r.url) or "nicht" in r.text
    assert len(_aktionen(admin)) == vorher


def test_api_mit_passphrase(admin, v):
    r = admin.post("/api/ereignisse", headers={"Authorization": "Bearer tok-admin"}, json={
        "ereignisse": [{"uuid": "u-api-1", "art": "STATUS", "startnummer": 2, "wert": "DNS",
                        "geraet": "skript", "erstellt_ms": T0, "veranstaltung_id": v.id,
                        "notiz": "per API"}]})  # fmt: skip
    assert r.status_code == 200, r.text
    with admin.app.state.sessionmaker() as s:
        assert s.get(Ereignis, "u-api-1").benutzer == "Admin (Passphrase)"


def test_modus_proxy_mit_person(tmp_path):
    dienst = Anmeldedienst.starten()
    e = Einstellungen(db_pfad=str(tmp_path / "p.sqlite3"), token_admin="tok-admin",
                      **einstellungen_proxy(dienst, superadmins="erika.muster"))  # fmt: skip
    try:
        with TestClient(create_app(e)) as c:
            c.headers.update(dienst.kopf("u-erika", "erika.muster", name="Erika Muster"))
            c.csrf = re.search(r'name="csrf" value="([0-9a-f]+)"', c.get("/admin").text).group(1)
            assert "Erika Muster (erika.muster)" in c.get("/admin").text  # Kopf
            post(
                c, "/admin/veranstaltungen", name="Kottenforst", datum="2026-10-10", aktivieren="1"
            )
            with c.app.state.sessionmaker() as s:
                from app.models import Veranstaltung

                vid = s.scalars(select(Veranstaltung)).one().id
            c.post(f"/admin/v/{vid}/teilnehmer", data={"csrf": c.csrf, "startnummer": "7",
                   "vorname": "A", "nachname": "B", "geschlecht": "w", "geburtsjahr": "1990"})  # fmt: skip
            korr(
                c, type("V", (), {"id": vid}), "status", startnummer="7", wert="DNS", notiz="krank"
            )
            with c.app.state.sessionmaker() as s:
                ev = s.scalars(select(Ereignis)).one()
            assert ev.benutzer == "Erika Muster (erika.muster)"
            aktionen = _aktionen(c)
            assert ("Erika Muster (erika.muster)", "Veranstaltung anlegen", None) in aktionen
            assert ("Erika Muster (erika.muster)", "Korrektur", vid) in aktionen
    finally:
        dienst.beenden()
