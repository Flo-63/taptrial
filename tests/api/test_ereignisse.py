import uuid
from datetime import date

import pytest

from app.models import Teilnehmer, Veranstaltung

T0 = 1_760_000_000_000
START = {"Authorization": "Bearer tok-start"}
ZIEL = {"Authorization": "Bearer tok-ziel"}
ADMIN = {"Authorization": "Bearer tok-admin"}


@pytest.fixture
def veranstaltung(client):
    with client.app.state.sessionmaker() as s:
        v = Veranstaltung(
            name="Test",
            datum=date(2026, 10, 10),
            aktiv=True,
            token_start="tok-start",
            token_ziel="tok-ziel",
        )
        s.add(v)
        s.flush()
        for nr, g, j in ((1, "w", 1990), (2, "m", 1985), (3, "m", 1970)):
            s.add(
                Teilnehmer(
                    veranstaltung_id=v.id,
                    startnummer=nr,
                    vorname=f"V{nr}",
                    nachname=f"N{nr}",
                    geschlecht=g,
                    geburtsjahr=j,
                )
            )
        s.commit()
        return v


def ev(art, **kw):
    kw.setdefault("uuid", str(uuid.uuid4()))
    kw.setdefault("geraet", "test-1")
    kw.setdefault("erstellt_ms", kw.get("zeit_ms") or T0)
    return {"art": art, **kw}


def senden(client, ereignisse, headers=START):
    return client.post("/api/ereignisse", json={"ereignisse": ereignisse}, headers=headers)


def test_batch_wird_gespeichert_und_bestaetigt(client, veranstaltung):
    batch = [ev("START", startnummer=1, zeit_ms=T0), ev("START", startnummer=2, zeit_ms=T0 + 1)]
    r = senden(client, batch)
    assert r.status_code == 200
    assert r.json()["bestaetigt"] == [e["uuid"] for e in batch]
    stand = client.get("/api/stand", headers=START).json()
    assert stand["gestartet"] == [1, 2]


def test_doppeltes_senden_ist_idempotent(client, veranstaltung):
    batch = [ev("START", startnummer=1, zeit_ms=T0)]
    assert senden(client, batch).status_code == 200
    r = senden(client, batch)
    assert r.status_code == 200
    assert r.json()["bestaetigt"] == [batch[0]["uuid"]]
    protokoll = client.get("/api/ereignisse", headers=ADMIN).json()
    assert len(protokoll["ereignisse"]) == 1


def test_doppelte_uuid_im_selben_batch(client, veranstaltung):
    e = ev("START", startnummer=1, zeit_ms=T0)
    r = senden(client, [e, e])
    assert r.status_code == 200 and r.json()["bestaetigt"] == [e["uuid"]]


def test_empfangen_ms_wird_vom_server_gesetzt(client, veranstaltung):
    senden(client, [ev("START", startnummer=1, zeit_ms=T0, empfangen_ms=1)])
    e = client.get("/api/ereignisse", headers=ADMIN).json()["ereignisse"][0]
    assert e["empfangen_ms"] > T0 // 2 and e["veranstaltung_id"] == veranstaltung.id


def test_zielgeraet_ablauf_ueber_api(client, veranstaltung):
    senden(client, [ev("START", startnummer=1, zeit_ms=T0)])
    z = ev("ZIEL", zeit_ms=T0 + 600_000)
    senden(client, [z], ZIEL)
    stand = client.get("/api/stand", headers=ZIEL).json()
    assert [o["uuid"] for o in stand["offene_ziele"]] == [z["uuid"]]
    zu = ev("ZUORDNUNG", bezug_uuid=z["uuid"], startnummer=1, erstellt_ms=T0 + 700_000)
    senden(client, [zu], ZIEL)
    stand = client.get("/api/stand", headers=ZIEL).json()
    assert stand["offene_ziele"] == [] and stand["im_ziel"] == [1]
    t1 = next(t for t in stand["teilnehmer"] if t["startnummer"] == 1)
    assert t1["fahrzeit_ms"] == 600_000 and t1["status"] == "OK"
    assert t1["klasse"] == "Damen 31-40"


def test_stand_enthaelt_teilnehmer_und_veranstaltung(client, veranstaltung):
    stand = client.get("/api/stand", headers=START).json()
    assert stand["veranstaltung"]["name"] == "Test"
    assert [t["startnummer"] for t in stand["teilnehmer"]] == [1, 2, 3]
    assert {"vorname", "nachname", "verein", "klasse", "status"} <= stand["teilnehmer"][0].keys()
    assert isinstance(stand["server_ms"], int)


# --- Token ----------------------------------------------------------------


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer falsch"}])
def test_ohne_oder_mit_falschem_token_401(client, veranstaltung, headers):
    assert senden(client, [ev("START", startnummer=1, zeit_ms=T0)], headers).status_code == 401
    assert client.get("/api/stand", headers=headers).status_code == 401


def test_token_auch_als_query_parameter(client, veranstaltung):
    assert client.get("/api/stand?token=tok-ziel").status_code == 200
    assert client.get("/api/stand?token=nix").status_code == 401


@pytest.mark.parametrize(
    ("headers", "art", "felder"),
    [
        (START, "ZIEL", {"zeit_ms": T0}),
        (START, "MANUELLE_ZEIT", {"startnummer": 1, "zeit_ms": T0, "notiz": "x"}),
        (ZIEL, "START", {"startnummer": 1, "zeit_ms": T0}),
        (ZIEL, "MANUELLE_ZEIT", {"startnummer": 1, "zeit_ms": T0, "notiz": "x"}),
    ],
)
def test_falsche_rolle_403(client, veranstaltung, headers, art, felder):
    assert senden(client, [ev(art, **felder)], headers).status_code == 403


def test_protokoll_nur_fuer_admin(client, veranstaltung):
    assert client.get("/api/ereignisse", headers=START).status_code == 403
    assert client.get("/api/ereignisse", headers=ADMIN).status_code == 200


def test_admin_korrektur_braucht_notiz(client, veranstaltung):
    e = ev("MANUELLE_ZEIT", startnummer=1, zeit_ms=T0)
    assert senden(client, [e], ADMIN).status_code == 422
    e["notiz"] = "Handstoppuhr"
    assert senden(client, [e], ADMIN).status_code == 200


# --- Validierung ----------------------------------------------------------


@pytest.mark.parametrize(
    "e",
    [
        ev("START", zeit_ms=T0),  # ohne Startnummer
        ev("START", startnummer=1),  # ohne Zeit
        ev("ZUORDNUNG", startnummer=1),  # ohne Bezug
        ev("STORNO"),
        ev("STATUS", startnummer=1, wert="FOO"),
        ev("QUATSCH"),
    ],
)
def test_ungueltige_ereignisse_422(client, veranstaltung, e):
    assert senden(client, [e]).status_code == 422


def test_ohne_veranstaltung_geraetetoken_unbekannt_und_admin_409(client):
    assert senden(client, [ev("START", startnummer=1, zeit_ms=T0)]).status_code == 401
    assert client.get("/api/stand", headers=ADMIN).status_code == 409


def test_ereignisse_sind_append_only(client, veranstaltung):
    import sqlalchemy

    senden(client, [ev("START", startnummer=1, zeit_ms=T0)])
    with client.app.state.engine.begin() as conn, pytest.raises(sqlalchemy.exc.DatabaseError):
        conn.execute(sqlalchemy.text("UPDATE ereignis SET zeit_ms = 0"))
    with client.app.state.engine.begin() as conn, pytest.raises(sqlalchemy.exc.DatabaseError):
        conn.execute(sqlalchemy.text("DELETE FROM ereignis"))


def test_veranstaltung_id_vom_geraet(client, veranstaltung):
    e = ev("START", startnummer=1, zeit_ms=T0, veranstaltung_id=veranstaltung.id)
    assert senden(client, [e]).status_code == 200
    assert (
        senden(client, [ev("START", startnummer=1, zeit_ms=T0, veranstaltung_id=999)]).status_code
        == 422
    )


def test_duplikat_ueberschreibt_nicht(client, veranstaltung):
    e = ev("START", startnummer=1, zeit_ms=T0)
    senden(client, [e])
    senden(client, [{**e, "zeit_ms": T0 + 5}])
    gespeichert = client.get("/api/ereignisse", headers=ADMIN).json()["ereignisse"]
    assert [x["zeit_ms"] for x in gespeichert] == [T0]


@pytest.mark.parametrize("wert", ["DNF", "OK"])
def test_ziel_darf_status_senden(client, veranstaltung, wert):
    assert senden(client, [ev("STATUS", startnummer=1, wert=wert)], ZIEL).status_code == 200


def test_rennleitung_status_wirkt_auf_stand(client, veranstaltung):
    senden(client, [ev("START", startnummer=1, zeit_ms=T0)])
    senden(client, [ev("STATUS", startnummer=1, wert="DNF", erstellt_ms=T0 + 1)], ZIEL)
    t1 = client.get("/api/stand", headers=ZIEL).json()["teilnehmer"][0]
    assert t1["status"] == "DNF"
    senden(client, [ev("STATUS", startnummer=1, wert="OK", erstellt_ms=T0 + 2)], ZIEL)
    t1 = client.get("/api/stand", headers=ZIEL).json()["teilnehmer"][0]
    assert t1["status"] == "UNVOLLSTAENDIG"


def test_stand_enthaelt_letzte_ziele_aller_geraete(client, veranstaltung):
    a = ev("ZIEL", startnummer=1, zeit_ms=T0 + 100, geraet="ziel-a")
    b = ev("ZIEL", zeit_ms=T0 + 200, geraet="ziel-b")
    c = ev("ZIEL", startnummer=3, zeit_ms=T0 + 300, geraet="ziel-a")
    senden(client, [a, b, c], ZIEL)
    zu = ev("ZUORDNUNG", bezug_uuid=b["uuid"], startnummer=2, erstellt_ms=T0 + 400)
    st = ev("STORNO", bezug_uuid=c["uuid"], erstellt_ms=T0 + 500)
    senden(client, [zu, st], ZIEL)
    letzte = client.get("/api/stand", headers=ZIEL).json()["letzte_ziele"]
    assert [(z["uuid"], z["startnummer"], z["geraet"]) for z in letzte] == [
        (b["uuid"], 2, "ziel-b"),
        (a["uuid"], 1, "ziel-a"),
    ]
    assert letzte[0]["zeit_ms"] == T0 + 200


def test_letzte_ziele_begrenzt(client, veranstaltung):
    senden(client, [ev("ZIEL", zeit_ms=T0 + i) for i in range(15)], ZIEL)
    letzte = client.get("/api/stand", headers=ZIEL).json()["letzte_ziele"]
    assert len(letzte) == 10 and letzte[0]["zeit_ms"] == T0 + 14


def test_ziel_darf_dsq_nur_vorlaeufig_mit_grund(client, veranstaltung):
    assert senden(client, [ev("STATUS", startnummer=1, wert="DSQ")], ZIEL).status_code == 403
    ohne = ev("STATUS", startnummer=1, wert="DSQ_VORLAEUFIG")
    assert senden(client, [ohne], ZIEL).status_code == 422
    leer = ev("STATUS", startnummer=1, wert="DSQ_VORLAEUFIG", notiz="  ")
    assert senden(client, [leer], ZIEL).status_code == 422
    mit = ev("STATUS", startnummer=1, wert="DSQ_VORLAEUFIG", notiz="Windschatten")
    assert senden(client, [mit], ZIEL).status_code == 200
    t1 = client.get("/api/stand", headers=ZIEL).json()["teilnehmer"][0]
    assert t1["dsq_vorlaeufig"] == "Windschatten"
    assert t1["status"] == "OFFEN"


def test_admin_bestaetigt_dsq(client, veranstaltung):
    e = ev("STATUS", startnummer=1, wert="DSQ", notiz="bestätigt")
    assert senden(client, [e], ADMIN).status_code == 200


@pytest.mark.parametrize(
    ("headers", "wert", "erlaubt"),
    [
        (START, "DNS", True),
        (START, "OK", True),
        (START, "DNF", False),
        (START, "DSQ_VORLAEUFIG", False),
        (ZIEL, "DNS", False),
    ],
)
def test_status_werte_je_rolle(client, veranstaltung, headers, wert, erlaubt):
    r = senden(client, [ev("STATUS", startnummer=1, wert=wert, notiz="n")], headers)
    assert r.status_code == (200 if erlaubt else 403)
