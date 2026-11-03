"""S 2: Rechte der Geräte (app/rechte.py): eigene Einträge, endgültiger DSQ, offizielles Ergebnis."""

import uuid

import pytest
from sqlalchemy import select

from app.models import Ereignis, Teilnehmer
from tests.api.test_posten import ADMIN, START, T0, ZIEL, durch, ev, posten, senden, v  # noqa: F401


def ziel_ev(nr=1, geraet="ziel-a", **kw):
    return ev("ZIEL", startnummer=nr, zeit_ms=T0 + 5000, geraet=geraet, **kw)


def start_ev(nr=1, geraet="start-a"):
    return ev("START", startnummer=nr, zeit_ms=T0, geraet=geraet)


def storno(bezug, geraet, **kw):
    return ev("STORNO", bezug_uuid=bezug["uuid"], geraet=geraet, **kw)


def ereignis(client, u):
    with client.app.state.sessionmaker() as s:
        return s.get(Ereignis, u)


def admin_status(client, nr, wert):
    r = senden(client, [ev("STATUS", startnummer=nr, wert=wert, notiz="Admin", geraet="admin",
                           erstellt_ms=T0 + 60_000)], ADMIN)  # fmt: skip
    assert r.status_code == 200, r.text


def erklaeren(client, wert):
    r = senden(client, [ev("ERGEBNIS", wert=wert, notiz="Admin", geraet="admin",
                           erstellt_ms=T0 + 120_000 + (wert == "VORLAEUFIG"))], ADMIN)  # fmt: skip
    assert r.status_code == 200, r.text


# --- Nur eigene Einträge ------------------------------------------------------------------------


def test_startgeraet_storniert_nur_den_eigenen_start(client, v):  # noqa: F811
    eigen, fremd, ziel = start_ev(1), start_ev(2, "start-b"), ziel_ev(3)
    assert senden(client, [eigen], START).status_code == 200
    assert senden(client, [fremd], START).status_code == 200
    assert senden(client, [ziel], ZIEL).status_code == 200
    r = senden(client, [storno(fremd, "start-a")], START)
    assert r.status_code == 422 and "anderen Gerät" in r.json()["detail"]
    assert senden(client, [storno(ziel, "start-a")], START).status_code == 422
    # gleiche Gerätekennung, aber fremder Zugang: die Kennung des Ziels nützt dem Startlink nichts
    assert senden(client, [storno(ziel, "ziel-a")], START).status_code == 422
    assert senden(client, [storno(eigen, "start-a")], START).status_code == 200


def test_posten_aendert_nur_eigene_durchfahrt_auch_nicht_vom_zweiten_handy(client, v):  # noqa: F811
    d = {**durch(v, 1, T0 + 1000), "geraet": "posten-a"}
    assert senden(client, [d], posten("kreisel")).status_code == 200
    r = senden(client, [storno(d, "posten-b")], posten("kreisel"))
    assert r.status_code == 422
    korrektur = ev("ZEITKORREKTUR", bezug_uuid=d["uuid"], delta_ms=1000, geraet="posten-b")
    assert senden(client, [korrektur], posten("kreisel")).status_code == 422
    assert senden(client, [storno(d, "posten-a")], posten("kreisel")).status_code == 200


def test_stapel_mit_gefaelschtem_bezug_storniert_nichts(client, v):  # noqa: F811
    """Der Trick aus dem Review: gefälschte Durchfahrt mit der UUID eines fremden Zielstempels."""
    z = ziel_ev(1)
    assert senden(client, [z], ZIEL).status_code == 200
    falsch = {**durch(v, 1, T0), "uuid": z["uuid"]}
    r = senden(client, [falsch, storno(falsch, "g")], posten("kreisel"))
    assert r.status_code == 422
    with client.app.state.sessionmaker() as s:
        assert s.scalars(select(Ereignis).where(Ereignis.art == "STORNO")).first() is None


def test_kennung_nicht_doppelt_und_kein_bezug_auf_unbekannt_oder_sich_selbst(client, v):  # noqa: F811
    z = ziel_ev(1)
    assert senden(client, [z], ZIEL).status_code == 200
    assert senden(client, [{**start_ev(1), "uuid": z["uuid"]}], START).status_code == 422
    assert (
        senden(client, [ev("STORNO", bezug_uuid=str(uuid.uuid4()), geraet="g")], ZIEL).status_code
        == 422
    )
    selbst = str(uuid.uuid4())
    r = senden(client, [{**ev("STORNO", bezug_uuid=selbst, geraet="g"), "uuid": selbst}], ZIEL)
    assert r.status_code == 422


def test_wiederholung_bleibt_idempotent(client, v):  # noqa: F811
    z = ziel_ev(1)
    assert senden(client, [z, z], ZIEL).json()["bestaetigt"] == [z["uuid"]]
    assert senden(client, [z], ZIEL).json()["bestaetigt"] == [z["uuid"]]


def test_rennleitungsfunktion_aendert_fremde_einraege(client, v):  # noqa: F811
    s, d = start_ev(1), {**durch(v, 1, T0 + 1000), "geraet": "posten-a"}
    z = ziel_ev(1, geraet="ziel-a")
    assert senden(client, [s], START).status_code == 200
    assert senden(client, [d], posten("kreisel")).status_code == 200
    assert senden(client, [z], ZIEL).status_code == 200
    korrektur = ev("ZEITKORREKTUR", bezug_uuid=z["uuid"], delta_ms=1000, geraet="rl", startnummer=1)
    assert senden(client, [korrektur], ZIEL).status_code == 200
    for bezug in (s, d, z):
        assert senden(client, [storno(bezug, "rl")], ZIEL).status_code == 200


def test_ohne_rennleitungsfunktion_nur_der_eigene_letzte_einlauf(client, v):  # noqa: F811
    with client.app.state.sessionmaker() as s:
        s.merge(v)
        vv = s.get(type(v), v.id)
        vv.rennleitung_getrennt = True
        vv.token_rennleitung = "tok-rl"
        s.commit()
    fremd, eigen = ziel_ev(1, geraet="ziel-b"), ziel_ev(2, geraet="ziel-a")
    assert senden(client, [fremd], ZIEL).status_code == 200
    assert senden(client, [eigen], ZIEL).status_code == 200
    assert senden(client, [storno(fremd, "ziel-a")], ZIEL).status_code == 422
    assert senden(client, [storno(eigen, "ziel-a", erstellt_ms=T0 + 6000)], ZIEL).status_code == 200
    rl = {"Authorization": "Bearer tok-rl"}
    assert senden(client, [storno(fremd, "rl")], rl).status_code == 200


def test_admin_eintraege_aendert_nur_der_admin(client, v):  # noqa: F811
    admin_status(client, 1, "DNF")
    with client.app.state.sessionmaker() as s:
        e = s.scalars(select(Ereignis).where(Ereignis.art == "STATUS")).one()
        bezug = {"uuid": e.uuid}
    assert senden(client, [storno(bezug, "rl")], ZIEL).status_code == 422
    r = senden(client, [ev("STORNO", bezug_uuid=bezug["uuid"], notiz="x", geraet="admin")], ADMIN)
    assert r.status_code == 200


def test_admin_eintrag_mit_eigener_kennung_bleibt_geschuetzt(client, v):  # noqa: F811
    """Auch wenn der Admin über die API eine andere Gerätekennung sendet."""
    dsq = ev(
        "STATUS", startnummer=1, wert="DSQ", notiz="x", geraet="leitung-pc", erstellt_ms=T0 + 1
    )
    frei = ev(
        "STATUS", startnummer=2, wert="DNF", notiz="x", geraet="leitung-pc", erstellt_ms=T0 + 2
    )
    assert senden(client, [dsq, frei], ADMIN).status_code == 200
    assert senden(client, [storno(dsq, "rl")], ZIEL).status_code == 422
    assert senden(client, [storno(frei, "rl")], ZIEL).status_code == 422


def test_erklaerung_und_merkmal_storniert_kein_geraet_auch_mit_fremder_kennung(client, v):  # noqa: F811
    erklaerung = ev("ERGEBNIS", wert="VORLAEUFIG", notiz="x", geraet="leitung-pc")
    assert senden(client, [erklaerung], ADMIN).status_code == 200
    assert senden(client, [storno(erklaerung, "rl")], ZIEL).status_code == 422


def test_aus_datei_uebernommene_aendert_nur_die_rennleitung(client, v):  # noqa: F811
    with client.app.state.sessionmaker() as s:
        s.add(Ereignis(uuid="d", veranstaltung_id=v.id, art="START", startnummer=1, zeit_ms=T0,
                       geraet="start-a", erstellt_ms=T0, empfangen_ms=T0, zugang="datei"))  # fmt: skip
        s.commit()
    assert senden(client, [storno({"uuid": "d"}, "start-a")], START).status_code == 422
    assert senden(client, [storno({"uuid": "d"}, "rl")], ZIEL).status_code == 200


def test_status_aus_der_zukunft_ueberstimmt_die_rennleitung_nicht(client, v):  # noqa: F811
    import time

    jetzt = int(time.time() * 1000)
    weit = ev("STATUS", startnummer=2, wert="OK", erstellt_ms=jetzt + 3_600_000)
    r = senden(client, [weit], START)
    assert r.status_code == 422 and "Uhr des Geräts" in r.json()["detail"]
    assert (
        senden(
            client, [ev("STATUS", startnummer=2, wert="DNF", erstellt_ms=jetzt)], ZIEL
        ).status_code
        == 200
    )
    stand = client.get("/api/stand", headers=ZIEL).json()["teilnehmer"]
    assert next(t for t in stand if t["startnummer"] == 2)["status"] == "DNF"
    # Zeitstempel einer etwas falschen Uhr gehen weiter durch: kein Tipp geht so verloren
    stempel = ev("START", startnummer=3, zeit_ms=jetzt + 3_600_000, geraet="start-a")
    assert senden(client, [stempel], START).status_code == 200
    kurz = ev("STATUS", startnummer=1, wert="DNS", erstellt_ms=jetzt + 60_000)
    assert senden(client, [kurz], START).status_code == 200


def test_geraetekennung_admin_ist_vorbehalten(client, v):  # noqa: F811
    assert senden(client, [ziel_ev(1, geraet="admin")], ZIEL).status_code == 422


def test_zugang_setzt_der_server(client, v):  # noqa: F811
    s, d = start_ev(1), durch(v, 1, T0 + 1000)
    senden(client, [{**s, "zugang": "admin"}], START)
    senden(client, [d], posten("kreisel"))
    assert ereignis(client, s["uuid"]).zugang == "start"
    assert ereignis(client, d["uuid"]).zugang == f"posten-{v.mp['Kreisel']}"


def test_aeltere_eintraege_ohne_zugang_zaehlen_ueber_die_geraetekennung(client, v):  # noqa: F811
    with client.app.state.sessionmaker() as s:
        s.add(Ereignis(uuid="alt", veranstaltung_id=v.id, art="START", startnummer=1, zeit_ms=T0,
                       geraet="start-a", erstellt_ms=T0, empfangen_ms=T0))  # fmt: skip
        s.commit()
    assert senden(client, [storno({"uuid": "alt"}, "start-b")], START).status_code == 422
    assert senden(client, [storno({"uuid": "alt"}, "start-a")], START).status_code == 200


# --- Status ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kopf", "art", "felder"),
    [
        (ZIEL, "STATUS", {"wert": "OK"}),
        (ZIEL, "STATUS", {"wert": "DNF"}),
        (ZIEL, "START_ANNULLIERT", {"notiz": "Grund"}),
        (START, "STATUS", {"wert": "DNS"}),
        (START, "STATUS", {"wert": "OK"}),
    ],
)
def test_endgueltigen_dsq_hebt_kein_geraet_auf(client, v, kopf, art, felder):  # noqa: F811
    admin_status(client, 1, "DSQ")
    r = senden(client, [ev(art, startnummer=1, erstellt_ms=T0 + 90_000, **felder)], kopf)
    assert r.status_code == 422 and "endgültig disqualifiziert" in r.json()["detail"]
    admin_status(client, 1, "OK")  # der Admin darf
    assert (
        senden(
            client, [ev(art, startnummer=1, erstellt_ms=T0 + 90_000, **felder)], kopf
        ).status_code
        == 200
    )


def test_startgeraet_aendert_keinen_status_der_rennleitung(client, v):  # noqa: F811
    assert senden(client, [ev("STATUS", startnummer=1, wert="DNF")], ZIEL).status_code == 200
    r = senden(client, [ev("STATUS", startnummer=1, wert="OK", erstellt_ms=T0 + 1)], START)
    assert r.status_code == 422
    assert senden(client, [ev("STATUS", startnummer=2, wert="DNS")], START).status_code == 200
    # DNS aufheben bleibt, auch offline im selben Stapel
    dns, ok = ev("STATUS", startnummer=3, wert="DNS"), ev("STATUS", startnummer=3, wert="OK")
    assert senden(client, [dns, ok], START).status_code == 200
    assert (
        senden(
            client, [ev("STATUS", startnummer=2, wert="OK", erstellt_ms=T0 + 2)], START
        ).status_code
        == 200
    )


# --- Offizielles Ergebnis ----------------------------------------------------------------------


def test_offiziell_keine_neuen_ereignisse_bis_zum_zuruecksetzen(client, v):  # noqa: F811
    gesendet = ziel_ev(1)
    assert senden(client, [gesendet], ZIEL).status_code == 200
    assert senden(client, [storno(gesendet, "ziel-a")], ZIEL).status_code == 200  # ohne Zielstempel
    erklaeren(client, "OFFIZIELL")
    assert client.get("/api/stand", headers=ZIEL).json()["offiziell"] is True

    r = senden(client, [start_ev(2)], START)
    assert r.status_code == 423 and r.json()["detail"]["grund"] == "offiziell"
    assert "vorläufig" in r.json()["detail"]["text"]
    assert senden(client, [durch(v, 1, T0 + 2000)], posten("kreisel")).status_code == 423
    assert senden(client, [ev("STATUS", startnummer=2, wert="DNF")], ZIEL).status_code == 423
    # Schon Gespeichertes bestätigt der Server weiter, ein Gerät wartet nicht ewig darauf
    assert senden(client, [gesendet], ZIEL).json()["bestaetigt"] == [gesendet["uuid"]]

    erklaeren(client, "VORLAEUFIG")
    assert client.get("/api/stand", headers=ZIEL).json()["offiziell"] is False
    assert senden(client, [start_ev(2)], START).status_code == 200


def test_admin_korrigiert_auch_unter_offiziell(client, v):  # noqa: F811
    erklaeren(client, "OFFIZIELL")
    r = senden(client, [ev("STATUS", startnummer=1, wert="DNS", notiz="x", geraet="admin",
                           erstellt_ms=T0 + 200_000)], ADMIN)  # fmt: skip
    assert r.status_code == 200


def test_geraet_sieht_seinen_stand_auch_unter_offiziell(client, v):  # noqa: F811
    erklaeren(client, "OFFIZIELL")
    with client.app.state.sessionmaker() as s:
        assert s.scalars(select(Teilnehmer)).first() is not None
    assert client.get("/api/stand", headers=START).status_code == 200
