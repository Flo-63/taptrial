"""K 4: Rennleitung annulliert einen versehentlichen Start (Ereignis START_ANNULLIERT)."""

from tests.api import test_posten
from tests.api.test_posten import ADMIN, MIN, START, T0, ZIEL, durch, ev, posten, senden

v = test_posten.v  # Fixture


def annullieren(nr, notiz="nicht da", t=T0 + 10 * MIN):
    return ev("START_ANNULLIERT", startnummer=nr, notiz=notiz, erstellt_ms=t)


def stand(client, headers=ZIEL):
    return client.get("/api/stand", headers=headers).json()


def tn(daten, nr):
    return next(t for t in daten["teilnehmer"] if t["startnummer"] == nr)


def test_rennleitung_annulliert_start_fahrer_dns(client, v):
    senden(client, [ev("START", startnummer=1, zeit_ms=T0)], START)
    senden(client, [ev("START", startnummer=2, zeit_ms=T0 + MIN)], START)
    assert senden(client, [annullieren(1)], ZIEL).status_code == 200
    daten = stand(client)
    assert tn(daten, 1)["status"] == "DNS" and tn(daten, 1)["start_ms"] is None
    assert daten["gestartet"] == [2]
    # nichts gelöscht: der Startstempel bleibt in den Ereignissen
    arten = [e["art"] for e in client.get("/api/ereignisse", headers=ADMIN).json()["ereignisse"]]
    assert arten.count("START") == 2 and arten.count("START_ANNULLIERT") == 1


def test_notiz_pflicht_und_nur_rennleitung(client, v):
    senden(client, [ev("START", startnummer=1, zeit_ms=T0)], START)
    assert senden(client, [annullieren(1, notiz="  ")], ZIEL).status_code == 422
    assert senden(client, [ev("START_ANNULLIERT", notiz="x")], ZIEL).status_code == 422
    assert senden(client, [annullieren(1)], START).status_code == 403
    assert senden(client, [annullieren(1)], posten("kreisel")).status_code == 403
    assert senden(client, [annullieren(1)], ADMIN).status_code == 200


def test_stand_listet_tipps_fuer_die_warnung(client, v):
    senden(client, [ev("START", startnummer=1, zeit_ms=T0)], START)
    senden(client, [durch(v, 1, T0 + 3 * MIN)], posten("kreisel"))
    senden(client, [durch(v, 1, T0 + 8 * MIN, "Brücke")], posten("bruecke"))
    senden(client, [ev("ZIEL", startnummer=1, zeit_ms=T0 + 25 * MIN)], ZIEL)
    tipps = tn(stand(client), 1)["tipps"]
    assert [t[0] for t in tipps] == ["Kreisel", "Brücke", "Ziel"]
    assert [t[1] for t in tipps] == [T0 + 3 * MIN, T0 + 8 * MIN, T0 + 25 * MIN]
    assert tn(stand(client), 2)["tipps"] == []
