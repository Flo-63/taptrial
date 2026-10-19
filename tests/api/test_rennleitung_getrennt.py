"""Ziel und Rennleitung trennbar (Test 08.10., Jetzt 14)."""

import uuid

from sqlalchemy import select

from app.models import Ereignis, GeloeschterToken, Veranstaltung, token_fingerabdruck
from tests.api import test_admin
from tests.api.test_admin import MIN, T0, post

admin, v, keine_sperre = test_admin.admin, test_admin.v, test_admin.keine_sperre  # Fixtures


def _v(admin, vid):
    with admin.app.state.sessionmaker() as s:
        v = s.get(Veranstaltung, vid)
        s.expunge(v)
        return v


def _e(art, geraet="ziel-ab12", **kw):
    t = kw.pop("zeit_ms", None)
    erstellt = kw.pop("erstellt_ms", t or T0)
    d = {"uuid": str(uuid.uuid4()), "art": art, "geraet": geraet, "erstellt_ms": erstellt, **kw}
    if t is not None:
        d["zeit_ms"] = t
    return d


def _senden(admin, token, *ereignisse):
    return admin.post("/api/ereignisse", json={"ereignisse": list(ereignisse)},
                      headers={"Authorization": f"Bearer {token}"})  # fmt: skip


def _trennen(admin, v, **mehr):
    return post(admin, f"/admin/v/{v.id}/rennleitung", zusammen="", **mehr)


def test_standard_zusammen_rennleitungstoken_ungueltig(admin, v):
    v = _v(admin, v.id)
    assert v.rennleitung_getrennt is False and v.token_rennleitung
    r = admin.get("/api/stand", headers={"Authorization": f"Bearer {v.token_rennleitung}"})
    assert r.status_code == 401
    stand = admin.get("/api/stand", headers={"Authorization": f"Bearer {v.token_ziel}"}).json()
    assert stand["rennleitung_getrennt"] is False and stand["rolle"] == "ziel"
    html = admin.get(f"/admin/v/{v.id}/geraete").text
    assert html.count('class="card geraet"') == 4 and "sicht=rennleitung" in html
    assert 'id="erneuern-rennleitung"' not in html


def test_getrennt_links_tokens_und_rechte(admin, v):
    assert "getrennt" in _trennen(admin, v).text
    v = _v(admin, v.id)
    assert v.rennleitung_getrennt is True
    html = admin.get(f"/admin/v/{v.id}/geraete").text
    assert f"/rennleitung?token={v.token_rennleitung}" in html and "sicht=rennleitung" not in html
    assert 'id="erneuern-rennleitung"' in html
    rl = {"Authorization": f"Bearer {v.token_rennleitung}"}
    stand = admin.get("/api/stand", headers=rl).json()
    assert stand["rolle"] == "rennleitung" and stand["rennleitung_getrennt"] is True
    assert admin.get("/rennleitung", params={"token": v.token_rennleitung}).status_code == 200

    z1 = _e("ZIEL", startnummer=1, zeit_ms=T0 + 30 * MIN)
    ohne = _e("ZIEL", zeit_ms=T0 + 31 * MIN, erstellt_ms=T0 + 31 * MIN)
    assert _senden(admin, v.token_ziel, z1, ohne).status_code == 200
    # Ziel: Zuordnen und Status nur mit Rennleitungs-Token
    zuo = _e("ZUORDNUNG", bezug_uuid=ohne["uuid"], startnummer=2)
    assert _senden(admin, v.token_ziel, zuo).status_code == 422
    dnf = _e("STATUS", startnummer=3, wert="DNF")
    assert _senden(admin, v.token_ziel, dnf).status_code == 422
    # ±1 s / Rückgängig nur für den eigenen letzten Einlauf
    alt = _e("ZEITKORREKTUR", bezug_uuid=z1["uuid"], delta_ms=1000, erstellt_ms=T0 + 32 * MIN)
    assert _senden(admin, v.token_ziel, alt).status_code == 422  # nicht mehr der letzte
    fremd = _e("STORNO", geraet="ziel-anders", bezug_uuid=ohne["uuid"], erstellt_ms=T0 + 32 * MIN)
    assert _senden(admin, v.token_ziel, fremd).status_code == 422  # anderes Gerät
    eigen = _e("ZEITKORREKTUR", bezug_uuid=ohne["uuid"], delta_ms=-1000, erstellt_ms=T0 + 32 * MIN)
    assert _senden(admin, v.token_ziel, eigen).status_code == 200
    # offline erfasst: Korrektur am damals letzten Einlauf, danach ein weiterer Einlauf im selben Batch
    z3 = _e("ZIEL", startnummer=3, zeit_ms=T0 + 40 * MIN, erstellt_ms=T0 + 40 * MIN)
    k3 = _e("ZEITKORREKTUR", bezug_uuid=z3["uuid"], delta_ms=1000, erstellt_ms=T0 + 40 * MIN + 5)
    z4 = _e("ZIEL", startnummer=1, zeit_ms=T0 + 41 * MIN, erstellt_ms=T0 + 41 * MIN)
    assert _senden(admin, v.token_ziel, z3, k3, z4).status_code == 200
    st = _e("STORNO", bezug_uuid=z4["uuid"], erstellt_ms=T0 + 42 * MIN)
    assert _senden(admin, v.token_ziel, st).status_code == 200
    # Rennleitung darf alles der Rennleitung
    for e in (zuo, dnf, _e("ZEITKORREKTUR", "rennleitung-x", bezug_uuid=z1["uuid"], delta_ms=1000)):
        e["geraet"] = "rennleitung-x"
        assert _senden(admin, v.token_rennleitung, e).status_code == 200
    with admin.app.state.sessionmaker() as s:
        assert {e.uuid for e in s.scalars(select(Ereignis))} >= {
            zuo["uuid"],
            dnf["uuid"],
            k3["uuid"],
        }


def test_rennleitungstoken_erneuern_und_loeschen(admin, v):
    _trennen(admin, v)
    alt = _v(admin, v.id).token_rennleitung
    r = post(admin, f"/admin/v/{v.id}/token", rolle="rennleitung")
    assert "Rennleitung-Token erneuert" in r.text
    neu = _v(admin, v.id).token_rennleitung
    assert neu != alt
    assert admin.get("/api/stand", headers={"Authorization": f"Bearer {alt}"}).status_code == 401
    post(admin, f"/admin/v/{v.id}/loeschen", bestaetigung="1", name="Kottenforst")
    with admin.app.state.sessionmaker() as s:
        abdruecke = set(s.scalars(select(GeloeschterToken.fingerabdruck)))
    assert token_fingerabdruck(neu) in abdruecke
    assert admin.get("/api/stand", headers={"Authorization": f"Bearer {neu}"}).status_code == 410


def test_umschalten_nach_erster_zeit_nur_mit_bestaetigung(admin, v):
    from tests.api.test_admin import geraet

    geraet(admin, v, "START", startnummer=1, zeit_ms=T0)
    r = _trennen(admin, v)
    assert "neue Links" in r.text and _v(admin, v.id).rennleitung_getrennt is False
    r = _trennen(admin, v, bestaetigung="1")
    assert _v(admin, v.id).rennleitung_getrennt is True
    r = post(admin, f"/admin/v/{v.id}/rennleitung", zusammen="1", bestaetigung="1")
    assert _v(admin, v.id).rennleitung_getrennt is False
    # zurück auf „zusammen“: Ziel-Token darf wieder alles der Rennleitung
    ohne = geraet(admin, v, "ZIEL", zeit_ms=T0 + 30 * MIN)
    geraet(admin, v, "ZUORDNUNG", bezug_uuid=ohne["uuid"], startnummer=1)
