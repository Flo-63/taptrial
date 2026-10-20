"""Schnellkorrektur am Ziel über die API, im Admin-Protokoll und in der Admin-CSV."""

import re

from tests.api import test_ereignisse
from tests.api.test_ereignisse import START, T0, ZIEL, ev, senden

MIN = 60_000
veranstaltung = test_ereignisse.veranstaltung  # Fixture


def korr(bezug, delta=200, **kw):
    return ev("ZEITKORREKTUR", bezug_uuid=bezug, delta_ms=delta, startnummer=kw.pop("nr", 1), **kw)


def rennen(client):
    z = ev("ZIEL", startnummer=1, zeit_ms=T0 + 30 * MIN)
    assert senden(client, [ev("START", startnummer=1, zeit_ms=T0)]).status_code == 200
    assert senden(client, [z], ZIEL).status_code == 200
    return z


def teilnehmer(client, nr):
    return next(
        t
        for t in client.get("/api/stand", headers=ZIEL).json()["teilnehmer"]
        if t["startnummer"] == nr
    )


def test_ziel_darf_senden_start_nicht(client, veranstaltung):
    z = rennen(client)
    assert senden(client, [korr(z["uuid"])], START).status_code == 403
    k = korr(z["uuid"])
    assert senden(client, [k, korr(z["uuid"])], ZIEL).status_code == 200
    assert senden(client, [k], ZIEL).status_code == 200  # gleiche UUID noch einmal: zählt einmal
    t = teilnehmer(client, 1)
    assert t["fahrzeit_ms"] == 30 * MIN + 400
    stand = client.get("/api/stand", headers=ZIEL).json()
    letzter = stand["letzte_ziele"][0]
    assert letzter["zeit_ms"] == T0 + 30 * MIN + 400 and letzter["korrektur_ms"] == 400


def test_pflichtfelder(client, veranstaltung):
    z = rennen(client)
    for falsch in ({"delta_ms": 300}, {"delta_ms": None}, {"bezug_uuid": None}, {"delta_ms": 0}):
        e = {**korr(z["uuid"]), **falsch}
        assert senden(client, [e], ZIEL).status_code == 422, falsch
    assert senden(client, [korr(z["uuid"], -200)], ZIEL).status_code == 200
    assert senden(client, [korr(z["uuid"], 1000), korr(z["uuid"], -1000)], ZIEL).status_code == 200
    assert senden(client, [korr(z["uuid"], 500)], ZIEL).status_code == 422


def test_offene_zeit_und_zuordnung(client, veranstaltung):
    senden(client, [ev("START", startnummer=2, zeit_ms=T0)])
    ohne = ev("ZIEL", zeit_ms=T0 + 20 * MIN)
    senden(client, [ohne, korr(ohne["uuid"], -200, nr=None)], ZIEL)
    stand = client.get("/api/stand", headers=ZIEL).json()
    assert stand["offene_ziele"][0]["zeit_ms"] == T0 + 20 * MIN - 200
    assert stand["offene_ziele"][0]["korrektur_ms"] == -200
    senden(client, [ev("ZUORDNUNG", bezug_uuid=ohne["uuid"], startnummer=2)], ZIEL)
    assert teilnehmer(client, 2)["fahrzeit_ms"] == 20 * MIN - 200


def test_admin_protokoll_und_csv(client, veranstaltung):
    z = rennen(client)
    senden(client, [korr(z["uuid"]), korr(z["uuid"])], ZIEL)
    client.post("/admin/anmelden", data={"token": "tok-admin"})
    vid = veranstaltung.id
    html = client.get(f"/admin/v/{vid}/protokoll").text
    assert html.count('data-art="ZEITKORREKTUR"') == 2 and "Zeitkorrektur" in html
    assert "+0,2 s" in html and re.search(r"korrigiert \(\+0,4 s\)", html)
    csv = client.get(f"/admin/v/{vid}/export/ergebnis.csv").content.decode("utf-8-sig")
    zeile = next(z for z in csv.splitlines() if ";V1;N1;" in z)
    assert "30:00,4" in zeile and "Zielzeit korrigiert (+0,4 s)" in zeile
    protokoll = client.get(f"/admin/v/{vid}/export/protokoll.csv").content.decode("utf-8-sig")
    assert protokoll.count("ZEITKORREKTUR") == 2 and "+0,2 s" in protokoll
    erg = client.get(f"/admin/v/{vid}/ergebnis").text
    assert "korrigiert (+0,4 s)" in erg
    fahrer = re.search(rf'href="(/admin/v/{vid}/teilnehmer/\d+)"', client.get(
        f"/admin/v/{vid}/teilnehmer", params={"q": "V1"}).text).group(1)  # fmt: skip
    seite = client.get(fahrer).text  # Stempelauswahl zum Umbuchen auf der Fahrer-Seite
    assert "(korrigiert +0,4 s) · Ziel · Nr. 1" in seite
