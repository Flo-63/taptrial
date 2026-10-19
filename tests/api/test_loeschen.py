"""Löschen einer Veranstaltung: nichts bleibt übrig, Geräte bekommen 410 „Veranstaltung gelöscht“."""

import hashlib

from sqlalchemy import inspect, text

from app.models import GeloeschterToken, Veranstaltung
from tests.api import test_admin
from tests.api.test_admin import T0, geraet, post

admin, v, keine_sperre = test_admin.admin, test_admin.v, test_admin.keine_sperre  # Fixtures


def kopf(token):
    return {"Authorization": f"Bearer {token}"}


def test_loeschen_ohne_reste_und_410_fuer_alte_geraete(admin, v):
    geraet(admin, v, "START", startnummer=1, zeit_ms=T0)
    geraet(admin, v, "ZIEL", startnummer=1, zeit_ms=T0 + 30 * 60_000)  # friert auch die Regeln ein
    admin.post(f"/admin/v/{v.id}/import", data={"csrf": admin.csrf},  # offener Import-Entwurf
               files={"datei": ("t.csv", b"startnummer;vorname;nachname;geschlecht;geburtsjahr\n9;A;B;m;1990\n")})  # fmt: skip
    alt_start = v.token_start
    post(admin, f"/admin/v/{v.id}/token", rolle="start")  # erneuerter Token: bleibt 401 (A51)
    with admin.app.state.sessionmaker() as s:
        x = s.get(Veranstaltung, v.id)
        token_start, token_ziel, token_rl = x.token_start, x.token_ziel, x.token_rennleitung
        assert x.aktiv and x.regeln_json
    html = admin.get(f"/admin/v/{v.id}/status").text
    assert "aktive Veranstaltung" in html and "Geräte" in html  # Hinweis in der Bestätigung
    r = post(admin, f"/admin/v/{v.id}/loeschen", bestaetigung="1", name="Kottenforst")
    assert "komplett gelöscht" in r.text

    # nichts mehr zur Veranstaltung in irgendeiner Tabelle außer den Token-Fingerabdrücken
    with admin.app.state.sessionmaker() as s:
        tabellen = inspect(s.get_bind()).get_table_names()
        for tabelle in tabellen:
            if tabelle == "geloeschter_token":
                continue
            spalten = {c["name"] for c in inspect(s.get_bind()).get_columns(tabelle)}
            if "veranstaltung_id" in spalten:
                n = s.execute(text(f"SELECT COUNT(*) FROM {tabelle} WHERE veranstaltung_id = :v"),
                              {"v": v.id}).scalar()  # fmt: skip
                assert n == 0, tabelle
        assert s.execute(text("SELECT COUNT(*) FROM veranstaltung")).scalar() == 0
        for t in ("strecke_klasse", "strecke", "teilnehmer", "ereignis", "import_entwurf"):
            assert s.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() == 0, t
        abdruecke = {g.fingerabdruck for g in s.query(GeloeschterToken)}
        assert abdruecke == {
            hashlib.sha256(t.encode()).hexdigest() for t in (token_start, token_ziel, token_rl)
        }
        assert all(len(f) == 64 for f in abdruecke)  # nur Hash, kein Token im Klartext
        inhalt = "".join(str(r) for r in s.execute(text("SELECT * FROM geloeschter_token")))
        assert token_start not in inhalt and "Kottenforst" not in inhalt

    # Geräte der gelöschten Veranstaltung: 410 „Veranstaltung gelöscht“
    for token in (token_start, token_ziel):
        r = admin.get("/api/stand", headers=kopf(token))
        assert r.status_code == 410 and "gelöscht" in r.json()["detail"]
        r = admin.post("/api/ereignisse", headers=kopf(token), json={"ereignisse": []})
        assert r.status_code == 410
    assert admin.get("/api/stand", headers=kopf(alt_start)).status_code == 401  # erneuert
    assert admin.get("/api/stand", headers=kopf("gibtsnicht")).status_code == 401
    assert (
        admin.get(f"/start?token={token_start}").status_code == 200
    )  # Seite lädt, Gerät räumt auf

    # nichts mehr aktiv: öffentliche Seiten
    assert "Derzeit keine Veranstaltung" in admin.get("/").text
    assert "Derzeit keine Ergebnisse" in admin.get("/ergebnisse").text
    assert admin.get("/api/oeffentlich").json() == {"veranstaltung": None}


def test_abgeschlossen_bleibt_beendet(admin, v):
    post(admin, f"/admin/v/{v.id}/abschliessen", bestaetigung="1", name="Kottenforst")
    r = admin.get("/api/stand", headers=kopf(v.token_ziel))
    assert r.status_code == 410 and r.json()["detail"] == "Veranstaltung beendet"
