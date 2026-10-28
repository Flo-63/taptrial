"""Ziel einer Strecke an einem Zwischen-Messpunkt im Admin und an den Geräten."""

import re
import uuid
from datetime import date

import pytest

from app import korrekturen as k
from app import saetze
from app import segmente as sg
from app import strecken as st
from app.auswertung import Status
from app.models import Messpunkt, Strecke, StreckeKlasse, Teilnehmer, Veranstaltung
from tests.api import test_admin
from tests.api.test_orte import ids, orte_speichern, strecken

admin, v = test_admin.admin, test_admin.v  # Fixtures


def test_editor_bietet_zwischenzeiten_als_ziel(admin, v):
    orte_speichern(admin, v, ("P1", "punkt"), ("P2", "punkt"))
    o = ids(admin, v)
    sid = strecken(admin, v)[0].id
    seite = admin.get(f"/admin/v/{v.id}/strecken/{sid}").text
    auswahl = re.search(r'<select name="zielort".*?</select>', seite, re.DOTALL).group(0)
    assert '<optgroup label="Ziel">' in auswahl and '<optgroup label="Zwischenzeit">' in auswahl
    assert re.search(rf'<option value="{o["Ziel"]}" selected>Ziel</option>', auswahl)  # Standard
    assert f'<option value="{o["P2"]}" >P2</option>' in auswahl
    assert f'value="{o["Start"]}"' not in auswahl  # Start ist kein Ziel
    assert "nimmt deren Posten-Gerät die Zielzeit" in seite
    r = admin.post(f"/admin/v/{v.id}/strecken/{sid}", headers={"accept": "application/json"},
                   data={"csrf": admin.csrf, "zielort": str(o["P2"]),
                         "verlauf": '{"punkte": ["P1"]}'})  # fmt: skip
    assert r.json()["ok"], r.text
    assert strecken(admin, v)[0].zielort_id == o["P2"]
    seite = admin.get(f"/admin/v/{v.id}/strecken/{sid}").text
    assert '<li class="pf-fest">P2</li>' in seite
    # P2 auch im eigenen Verlauf: Fehler am Reiter „Verlauf“, nichts gespeichert
    r = admin.post(f"/admin/v/{v.id}/strecken/{sid}", headers={"accept": "application/json"},
                   data={"csrf": admin.csrf, "verlauf": '{"punkte": ["P1", "P2"]}'})  # fmt: skip
    assert not r.json()["ok"] and r.json()["reiter"] == "verlauf"
    assert "Das Ziel „P2“ kommt im Verlauf schon vorher vor" in r.json()["fehler"]


def test_editor_ohne_zwischenzeiten_wie_bisher(admin, v):
    """Regression: ohne Zwischenzeiten keine Gruppen, keine Erklärung."""
    sid = strecken(admin, v)[0].id
    seite = admin.get(f"/admin/v/{v.id}/strecken/{sid}").text
    auswahl = re.search(r'<select name="zielort".*?</select>', seite, re.DOTALL).group(0)
    assert "optgroup" not in auswahl and "nimmt deren Posten-Gerät" not in seite


# --- Geräte und Auswertung: 20 km „Start · P1 · [P2 · P1] ×2 · Ziel“, 5 km „Start · P1 · P2“ ----

T0 = 1_791_000_000_000
MIN = 60_000
ZIEL = {"Authorization": "Bearer tok-ziel"}
START = {"Authorization": "Bearer tok-start"}
P1 = {"Authorization": "Bearer tok-p1"}
P2 = {"Authorization": "Bearer tok-p2"}


@pytest.fixture
def zm(client):
    """Nr. 1 fährt 20 km, Nr. 2 fährt 5 km (Ziel an P2), Nr. 3 fährt 20 km."""
    with client.app.state.sessionmaker() as s:
        saetze.standardsaetze_anlegen(s)
        v = Veranstaltung(name="KoFo", datum=date(2026, 10, 31), aktiv=True, token_start="tok-start",
                          token_ziel="tok-ziel", klassensatz_id=saetze.standard_klassensatz(s).id)  # fmt: skip
        s.add(v)
        s.flush()
        for nr, j in ((1, 1990), (2, 1960), (3, 1991)):
            s.add(Teilnehmer(veranstaltung_id=v.id, startnummer=nr, vorname=f"V{nr}", nachname=f"N{nr}",
                             geschlecht="m", geburtsjahr=j))  # fmt: skip
        st.sicherstellen(s, v)
        lang = st.strecken_von(s, v)[0]
        lang.name = "20 km"
        mps = [Messpunkt(veranstaltung_id=v.id, position=0, name="P1", token="tok-p1"),
               Messpunkt(veranstaltung_id=v.id, position=1, name="P2", token="tok-p2")]  # fmt: skip
        s.add_all(mps)
        s.flush()
        p1, p2 = str(mps[0].id), str(mps[1].id)
        lang.teile_json = sg.nach_json(
            sg.teile_aus_verlauf([p1, sg.VerlaufBlock("Runde", 2, (p2, p1))])
        )
        kurz = Strecke(veranstaltung_id=v.id, position=1, name="5 km", zielort_id=mps[1].id,
                       teile_json=sg.nach_json(sg.teile_aus_verlauf([p1])))  # fmt: skip
        s.add(kurz)
        s.commit()
        klasse = k.ergebnis(s, v).zeile(2).klasse
        lang.klassen = [x for x in lang.klassen if x.klasse != klasse]
        kurz.klassen = [StreckeKlasse(klasse=klasse)]
        s.commit()
        v.mp = {"P1": p1, "P2": p2}
        v.klasse_5km = klasse
        return v


def ev(art, **kw):
    return {"uuid": str(uuid.uuid4()), "art": art, "geraet": kw.pop("geraet", "g"),
            "erstellt_ms": kw.get("zeit_ms") or T0, **kw}  # fmt: skip


def senden(client, ereignisse, headers):
    r = client.post("/api/ereignisse", json={"ereignisse": ereignisse}, headers=headers)
    assert r.status_code == 200, r.text
    return ereignisse


def durch(v, nr, t, name, **kw):
    return ev("DURCHFAHRT", startnummer=nr, zeit_ms=t, wert=v.mp[name], **kw)


def ergebnis(client, v):
    with client.app.state.sessionmaker() as s:
        return k.ergebnis(s, s.get(Veranstaltung, v.id))


def rennen(client, v):
    """Start beider, alle Tipps: Nr. 1 an P1, P2 (beide), Ziel; Nr. 2 an P1 und P2 (Ziel)."""
    senden(
        client,
        [ev("START", startnummer=1, zeit_ms=T0), ev("START", startnummer=2, zeit_ms=T0 + MIN)],
        START,
    )
    senden(client, [durch(v, 1, T0 + 3 * MIN, "P1"), durch(v, 2, T0 + 4 * MIN, "P1"),
                    durch(v, 1, T0 + 13 * MIN, "P1"), durch(v, 1, T0 + 23 * MIN, "P1")], P1)  # fmt: skip
    ziel_tipp = durch(v, 2, T0 + 8 * MIN, "P2")
    senden(client, [ziel_tipp, durch(v, 1, T0 + 8 * MIN + 30_000, "P2"),
                    durch(v, 1, T0 + 18 * MIN, "P2")], P2)  # fmt: skip
    senden(client, [ev("ZIEL", startnummer=1, zeit_ms=T0 + 25 * MIN)], ZIEL)
    return ziel_tipp


def test_zielzeit_am_zwischen_messpunkt(client, zm):
    ziel_tipp = rennen(client, zm)
    erg = ergebnis(client, zm)
    a, b = erg.zeile(1), erg.zeile(2)
    assert (b.strecke, b.status, b.ziel_ms, b.fahrzeit_ms) == (1, Status.OK, T0 + 8 * MIN, 7 * MIN)
    assert b.rang_gesamt == 1 and b.durchfahrten[0].zeit_ms == 3 * MIN
    assert [x.zeit_ms for x in b.segmentzeiten] == [3 * MIN, 4 * MIN]  # Start→P1, P1→Ziel (P2)
    assert [s.uuid for s in erg.zieldurchfahrten] == [ziel_tipp["uuid"]]
    # 20 km unverändert: alle Durchfahrten gezählt, Zielzeit vom Zielgerät
    assert (a.strecke, a.status, a.fahrzeit_ms) == (0, Status.OK, 25 * MIN)
    assert all(d.uuid for d in a.durchfahrten) and erg.nicht_gezaehlt == {}
    assert erg.konflikte == []
    assert k.auf_der_strecke(erg) == []


def test_posten_am_ziel_zeigt_beide_strecken(client, zm):
    senden(
        client,
        [ev("START", startnummer=1, zeit_ms=T0), ev("START", startnummer=2, zeit_ms=T0)],
        START,
    )
    stand = client.get("/api/stand", headers=P2).json()
    assert stand["messpunkt"]["strecken"] == [0, 1]
    assert stand["messpunkt"]["erwartet"] == {"0": 2, "1": 1}
    assert stand["messpunkt"]["ziel_strecken"] == [1]
    nr = {t["startnummer"]: t for t in stand["teilnehmer"]}
    assert nr[2]["naechste_runde"][zm.mp["P2"]] == 1
    senden(client, [durch(zm, 2, T0 + 8 * MIN, "P2")], P2)
    nr = {t["startnummer"]: t for t in client.get("/api/stand", headers=P2).json()["teilnehmer"]}
    assert nr[2]["naechste_runde"][zm.mp["P2"]] == 2 and nr[2]["ziel_ms"] == T0 + 8 * MIN  # fertig
    assert nr[1]["naechste_runde"][zm.mp["P2"]] == 1  # 20 km weiter erwartet
    # P1 kennt das Ziel nicht; Zielgerät zeigt nur die 20 km
    assert "ziel_strecken" not in client.get("/api/stand", headers=P1).json()["messpunkt"]
    assert client.get("/api/stand", headers=ZIEL).json()["ort"]["strecken"] == [0]


def test_rennleitung_letzte_einlaeufe_mit_posten_ziel(client, zm):
    ziel_tipp = rennen(client, zm)
    letzte = client.get("/api/stand", headers=ZIEL).json()["letzte_ziele"]
    posten = [x for x in letzte if x.get("durchfahrt")]
    assert [(x["uuid"], x["startnummer"], x["ort_name"]) for x in posten] == [
        (ziel_tipp["uuid"], 2, "P2")
    ]
    assert [x["startnummer"] for x in letzte] == [1, 2]  # nach Zeit, neueste zuerst
    assert set(letzte[0]) == {"uuid", "zeit_ms", "geraet", "startnummer", "korrektur_ms"}
    # ±1 s und Rückgängig über die Rennleitung wirken auf die Zieldurchfahrt
    senden(
        client,
        [ev("ZEITKORREKTUR", bezug_uuid=ziel_tipp["uuid"], delta_ms=1000, startnummer=2)],
        ZIEL,
    )
    assert ergebnis(client, zm).zeile(2).ziel_ms == T0 + 8 * MIN + 1000
    senden(client, [ev("STORNO", bezug_uuid=ziel_tipp["uuid"], startnummer=2)], ZIEL)
    b = ergebnis(client, zm).zeile(2)
    assert b.ziel_ms is None and b.status == Status.UNVOLLSTAENDIG
    assert [z.startnummer for z in k.auf_der_strecke(ergebnis(client, zm))] == [2]


def test_doppeltipp_und_zweiter_zieltipp(client, zm):
    ziel_tipp = rennen(client, zm)
    doppelt = senden(client, [durch(zm, 2, T0 + 8 * MIN + 4000, "P2")], P2)[0]
    erg = ergebnis(client, zm)
    assert erg.zeile(2).ziel_ms == T0 + 8 * MIN and erg.nicht_gezaehlt == {
        doppelt["uuid"]: "Doppeltipp"
    }
    spaeter = senden(client, [durch(zm, 2, T0 + 20 * MIN, "P2")], P2)[0]
    erg = ergebnis(client, zm)
    assert erg.zeile(2).status == Status.KONFLIKT
    (konflikt,) = erg.konflikte
    assert konflikt.art == "MEHRERE_ZIELE" and set(konflikt.uuids) == {
        ziel_tipp["uuid"],
        spaeter["uuid"],
    }
    senden(
        client, [ev("STORNO", bezug_uuid=spaeter["uuid"], startnummer=2)], P2
    )  # Posten nimmt zurück
    assert ergebnis(client, zm).zeile(2).status == Status.OK


def test_rennleitung_status_fuer_posten_ziel(client, zm):
    rennen(client, zm)
    senden(client, [ev("STATUS", startnummer=2, wert="DNF")], ZIEL)
    assert ergebnis(client, zm).zeile(2).status == Status.DNF
    senden(client, [ev("STATUS", startnummer=2, wert="OK")], ZIEL)
    assert ergebnis(client, zm).zeile(2).status == Status.OK
    # offen gebliebene Fahrer: „Ergebnis offiziell“ wartet auch auf die 5 km
    senden(client, [ev("START", startnummer=3, zeit_ms=T0 + 2 * MIN)], START)
    assert [z.startnummer for z in k.auf_der_strecke(ergebnis(client, zm))] == [3]
    senden(client, [durch(zm, 3, T0 + 30 * MIN, "P2")], P2)  # 20 km: nur Zwischenzeit
    assert [z.startnummer for z in k.auf_der_strecke(ergebnis(client, zm))] == [3]


def test_streckenwechsel_bewertet_tipps_neu(client, zm):
    ziel_tipp = rennen(client, zm)
    with client.app.state.sessionmaker() as s:
        v = s.get(Veranstaltung, zm.id)
        lang, kurz = st.strecken_von(s, v)
        kurz.klassen = []
        lang.klassen = [*lang.klassen, StreckeKlasse(klasse=zm.klasse_5km)]
        s.commit()
    b = ergebnis(client, zm).zeile(2)  # jetzt 20 km: P2-Tipp ist Zwischenzeit, kein Ziel
    assert b.strecke == 0 and b.ziel_ms is None and b.status == Status.UNVOLLSTAENDIG
    assert ziel_tipp["uuid"] in [d.uuid for d in b.durchfahrten]
    with client.app.state.sessionmaker() as s:
        v = s.get(Veranstaltung, zm.id)
        lang, kurz = st.strecken_von(s, v)
        lang.klassen = [x for x in lang.klassen if x.klasse != zm.klasse_5km]
        kurz.klassen = [StreckeKlasse(klasse=zm.klasse_5km)]
        s.commit()
    assert ergebnis(client, zm).zeile(2).ziel_ms == T0 + 8 * MIN  # nichts verloren


def test_zielmonitor_und_ergebnisse(client, zm):
    rennen(client, zm)
    daten = client.get("/api/oeffentlich").json()
    assert [x["startnummer"] for x in daten["letzte"]] == [1, 2]
    assert daten["zaehlung"]["im_ziel"] == 2 and daten["unterwegs"] == []
    csv = client.get("/ergebnisse.csv").text
    assert "7:00,0" in csv and "25:00,0" in csv


def test_ohne_ziel_an_zwischenzeit_unveraendert(client, zm):
    """Regression: Ziel der 5 km zurück auf „Ziel“ – P2-Tipps zählen dann nicht als Ziel."""
    with client.app.state.sessionmaker() as s:
        st.strecken_von(s, s.get(Veranstaltung, zm.id))[1].zielort_id = None
        s.commit()
    rennen(client, zm)
    erg = ergebnis(client, zm)
    assert erg.zieldurchfahrten == [] and erg.zeile(2).ziel_ms is None
    stand = client.get("/api/stand", headers=P2).json()
    assert "ziel_strecken" not in stand["messpunkt"] and stand["messpunkt"]["strecken"] == [0]
    assert not any(
        x.get("durchfahrt") for x in client.get("/api/stand", headers=ZIEL).json()["letzte_ziele"]
    )


def test_fahrerseite_nennt_zieldurchfahrt(client, zm):
    from app.admin import durchfahrten_von, messpunkt_namen

    ziel_tipp = rennen(client, zm)
    erg = ergebnis(client, zm)
    zeilen = durchfahrten_von(erg, erg.zeile(2))
    assert [(x.messpunkt, x.zuordnung) for x in zeilen] == [("P1", "P1"), ("P2", "Ziel")]
    assert zeilen[1].uuid == ziel_tipp["uuid"] and zeilen[1].grund is None
    assert messpunkt_namen(erg)[zm.mp["P2"]] == "P2"
