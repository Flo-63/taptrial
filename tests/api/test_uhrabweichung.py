"""Uhrabweichung messen und warnen: Serverzeit in der Sync-Antwort, Meldung je Gerät."""

from sqlalchemy import select

from app.models import GeraetUhr
from tests.api import test_admin
from tests.api.test_admin import geraet, post

admin, v, keine_sperre = test_admin.admin, test_admin.v, test_admin.keine_sperre  # Fixtures


def test_serverzeit_in_sync_antworten(admin, v):
    r = admin.post(
        "/api/ereignisse",
        json={"ereignisse": []},
        headers={"Authorization": f"Bearer {v.token_start}"},
    )
    assert r.status_code == 200 and isinstance(r.json()["server_ms"], int)
    stand = admin.get("/api/stand", headers={"Authorization": f"Bearer {v.token_ziel}"}).json()
    assert isinstance(stand["server_ms"], int)


def test_geraet_meldet_abweichung_admin_zeigt_sie(admin, v):
    kopf = {
        "Authorization": f"Bearer {v.token_ziel}",
        "X-Geraet": "ziel-ab12",
        "X-Uhr-Abweichung": "820",
    }
    assert admin.get("/api/stand", headers=kopf).status_code == 200
    admin.post("/api/ereignisse", json={"ereignisse": []},
               headers={"Authorization": f"Bearer {v.token_start}", "X-Geraet": "start-cd34",
                        "X-Uhr-Abweichung": "-260"})  # fmt: skip
    admin.get("/api/stand", headers={**kopf, "X-Uhr-Abweichung": "kaputt"})  # ungültig: ignoriert
    with admin.app.state.sessionmaker() as s:
        zeilen = {u.geraet: (u.rolle, u.abweichung_ms) for u in s.scalars(select(GeraetUhr))}
    assert zeilen == {"ziel-ab12": ("ziel", 820), "start-cd34": ("start", -260)}
    html = admin.get(f"/admin/v/{v.id}/geraete").text
    assert 'id="uhren"' in html and "ziel-ab12" in html and "+0,8 s" in html and "−0,3 s" in html
    assert 'class="badge b-warn uhr-warnung"' in html  # über 0,5 s deutlich markiert
    # Zeiten werden nicht verändert
    e = geraet(admin, v, "START", startnummer=1, zeit_ms=1_000_000)
    with admin.app.state.sessionmaker() as s:
        from app.models import Ereignis

        assert s.get(Ereignis, e["uuid"]).zeit_ms == 1_000_000
    # Löschen der Veranstaltung entfernt auch die Uhr-Meldungen
    post(admin, f"/admin/v/{v.id}/loeschen", bestaetigung="1", name="Kottenforst")
    with admin.app.state.sessionmaker() as s:
        assert s.scalars(select(GeraetUhr)).all() == []
