"""Gerätedatei im Admin einlesen (AUSBAU C2): Vorschau, Übernehmen, Protokoll."""

import json
import uuid

from sqlalchemy import select

from app.models import Ereignis
from tests.api import test_admin
from tests.api.test_admin import MIN, T0, geraet, post

admin, v, keine_sperre = test_admin.admin, test_admin.v, test_admin.keine_sperre  # Fixtures


def _eintrag(art="ZIEL", **kw):
    t = kw.pop("zeit_ms", T0 + 30 * MIN)
    return {"uuid": str(uuid.uuid4()), "art": art, "zeit_ms": t, "erstellt_ms": t,
            "geraet": "ziel-ab12", "gesendet": 0, "zeit": "10:30:00,0", **kw}  # fmt: skip


def _datei(v, eintraege, vid=None):
    return {"geraet": "ziel-ab12", "exportiert": "2026-10-10T09:00:00Z",
            "veranstaltung": {"id": vid or v.id, "name": "Kottenforst", "datum": "2026-10-10"},
            "eintraege": eintraege}  # fmt: skip


def _hochladen(admin, v, inhalt):
    daten = inhalt if isinstance(inhalt, bytes) else json.dumps(inhalt).encode()
    return admin.post(f"/admin/v/{v.id}/geraetedatei", data={"csrf": admin.csrf},
                      files={"datei": ("rcb-ziel-ab12.json", daten, "application/json")})  # fmt: skip


def _inhalt(html):
    import html as h
    import re

    return h.unescape(re.search(r'name="inhalt" value="([^"]*)"', html).group(1))


def _ereignisse(admin):
    with admin.app.state.sessionmaker() as s:
        return {e.uuid: e for e in s.scalars(select(Ereignis))}


def test_upload_seite(admin, v):
    html = admin.get(f"/admin/v/{v.id}/geraetedatei").text
    assert 'id="geraetedatei"' in html and 'type="file"' in html


def test_vorschau_und_uebernehmen_idempotent(admin, v):
    schon = geraet(admin, v, "ZIEL", zeit_ms=T0 + 20 * MIN)
    alt = {**_eintrag(), "uuid": schon["uuid"], "zeit_ms": schon["zeit_ms"]}
    neu = [
        _eintrag(zeit_ms=T0 + 31 * MIN),
        _eintrag("ZUORDNUNG", bezug_uuid=schon["uuid"], startnummer=1),
    ]
    kaputt = {"uuid": "x", "art": "ZIEL", "geraet": "ziel-ab12", "erstellt_ms": T0}  # zeit_ms fehlt
    r = _hochladen(admin, v, _datei(v, [alt, *neu, kaputt]))
    assert r.status_code == 200
    for id_, zahl in (("gd-anzahl", 4), ("gd-vorhanden", 1), ("gd-neu", 2), ("gd-ungueltig", 1)):
        assert f'id="{id_}">{zahl}<' in r.text
    assert "andere Veranstaltung" not in r.text
    inhalt = _inhalt(r.text)
    r = post(admin, f"/admin/v/{v.id}/geraetedatei/uebernehmen", inhalt=inhalt)
    assert "2 Einträge aus der Datei übernommen" in r.text
    alle = _ereignisse(admin)
    for e in neu:
        assert alle[e["uuid"]].geraet == "ziel-ab12" and alle[e["uuid"]].quelle == "datei"
    assert alle[schon["uuid"]].quelle is None and alle[schon["uuid"]].geraet == "test"
    # noch einmal: nichts Neues (idempotent wie beim Sync)
    r = post(admin, f"/admin/v/{v.id}/geraetedatei/uebernehmen", inhalt=inhalt)
    assert "0 Einträge aus der Datei übernommen" in r.text
    assert len(_ereignisse(admin)) == len(alle)
    prot = admin.get(f"/admin/v/{v.id}/protokoll").text
    assert prot.count(">aus Datei übernommen<") == 2
    csv = admin.get(f"/admin/v/{v.id}/export/protokoll.csv").content.decode("utf-8-sig")
    assert csv.count("aus Datei übernommen") == 2


def test_andere_veranstaltung_warnt_und_braucht_bestaetigung(admin, v):
    e = _eintrag(veranstaltung_id=999)
    r = _hochladen(admin, v, _datei(v, [e], vid=999))
    assert "gehört zu einer anderen Veranstaltung" in r.text
    inhalt = _inhalt(r.text)
    r = post(admin, f"/admin/v/{v.id}/geraetedatei/uebernehmen", inhalt=inhalt)
    assert "anderen Veranstaltung" in r.text and e["uuid"] not in _ereignisse(admin)
    r = post(admin, f"/admin/v/{v.id}/geraetedatei/uebernehmen", inhalt=inhalt, trotzdem="1")
    assert "1 Eintrag aus der Datei übernommen" in r.text
    assert _ereignisse(admin)[e["uuid"]].veranstaltung_id == v.id


def test_unlesbare_datei_und_csrf(admin, v):
    r = _hochladen(admin, v, b"kein json")
    assert "Datei nicht lesbar" in r.text
    r = _hochladen(admin, v, {"eintraege": "falsch"})
    assert "Datei nicht lesbar" in r.text
    r = admin.post(f"/admin/v/{v.id}/geraetedatei/uebernehmen", data={"inhalt": "{}"})
    assert r.status_code == 403


def test_abgeschlossen_schreibgeschuetzt(admin, v):
    post(admin, f"/admin/v/{v.id}/abschliessen", bestaetigung="1", name="Kottenforst")
    r = _hochladen(admin, v, _datei(v, [_eintrag()]))
    assert "schreibgeschützt" in r.text
