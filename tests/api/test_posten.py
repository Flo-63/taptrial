"""Rolle „posten“ (Messpunkte), Durchfahrten, Admin und Auswertung mit Segmenten."""

import json
import re
import uuid
from datetime import date

import pytest

from app import saetze, segmente
from app import strecken as st
from app.models import GeraetUhr, Messpunkt, Teilnehmer, Veranstaltung
from app.simulation import beispiel_teile

T0 = 1_791_000_000_000
MIN = 60_000
ZIEL = {"Authorization": "Bearer tok-ziel"}
START = {"Authorization": "Bearer tok-start"}
ADMIN = {"Authorization": "Bearer tok-admin"}


def kern(live):
    """Live-Felder ohne die Angaben fürs Ergebnis (Name, mehrere Durchfahrten)."""
    return [{k: x[k] for k in ("id", "strecke", "abschnitt", "vergleich")} for x in live]


@pytest.fixture
def v(client):
    with client.app.state.sessionmaker() as s:
        saetze.standardsaetze_anlegen(s)
        v = Veranstaltung(name="Rund", datum=date(2026, 10, 10), aktiv=True, token_start="tok-start",
                          token_ziel="tok-ziel", klassensatz_id=saetze.standard_klassensatz(s).id)  # fmt: skip
        s.add(v)
        s.flush()
        for nr, j in ((1, 1990), (2, 1985), (3, 1980)):
            s.add(Teilnehmer(veranstaltung_id=v.id, startnummer=nr, vorname=f"V{nr}", nachname=f"N{nr}",
                             geschlecht="m", geburtsjahr=j))  # fmt: skip
        st.sicherstellen(s, v)
        strecke = st.strecken_von(s, v)[0]
        mps = [Messpunkt(veranstaltung_id=v.id, position=0, name="Kreisel", token="tok-kreisel"),
               Messpunkt(veranstaltung_id=v.id, position=1, name="Brücke", token="tok-bruecke")]  # fmt: skip
        s.add_all(mps)
        s.flush()
        # Beispielstrecke: Start→Kreisel, Rundenblock Kreisel→Brücke→Kreisel ×2, Kreisel→Ziel
        ids = {"P1": str(mps[0].id), "P2": str(mps[1].id)}
        strecke.teile_json = segmente.nach_json(beispiel_teile(ids, {"P2": "Brücke"}))
        s.commit()
        v.mp = {m.name: m.id for m in mps}
        return v


def posten(name):
    return {"Authorization": f"Bearer tok-{name}"}


def ev(art, **kw):
    return {
        "uuid": str(uuid.uuid4()),
        "art": art,
        "geraet": "g",
        "erstellt_ms": kw.get("zeit_ms") or T0,
        **kw,
    }


def senden(client, ereignisse, headers):
    return client.post("/api/ereignisse", json={"ereignisse": ereignisse}, headers=headers)


def durch(v, nr, t, name="Kreisel"):
    return ev("DURCHFAHRT", startnummer=nr, zeit_ms=t, wert=str(v.mp[name]))


def test_posten_darf_nur_seinen_messpunkt(client, v):
    d = durch(v, 1, T0 + 3 * MIN)
    assert senden(client, [d], posten("kreisel")).status_code == 200
    assert senden(client, [durch(v, 1, T0, "Brücke")], posten("kreisel")).status_code == 403
    for art in (ev("START", startnummer=1, zeit_ms=T0), ev("ZIEL", startnummer=1, zeit_ms=T0),
                ev("STATUS", startnummer=1, wert="DNF"), ev("RUNDE", bezug_uuid=d["uuid"], wert="GILT")):  # fmt: skip
        assert senden(client, [art], posten("kreisel")).status_code == 403, art["art"]
    # Korrektur und Rückgängig nur für eigene Durchfahrten
    assert (
        senden(
            client, [ev("ZEITKORREKTUR", bezug_uuid=d["uuid"], delta_ms=1000)], posten("kreisel")
        ).status_code
        == 200
    )
    fremd = durch(v, 2, T0, "Brücke")
    assert senden(client, [fremd], posten("bruecke")).status_code == 200
    assert (
        senden(client, [ev("STORNO", bezug_uuid=fremd["uuid"])], posten("kreisel")).status_code
        == 403
    )
    neu = durch(v, 3, T0 + MIN)  # Durchfahrt und Rückgängig im selben Stapel (offline)
    assert (
        senden(client, [neu, ev("STORNO", bezug_uuid=neu["uuid"])], posten("kreisel")).status_code
        == 200
    )
    # Start/Ziel/Rennleitung dürfen keine Durchfahrten senden
    assert senden(client, [durch(v, 1, T0)], START).status_code == 403
    assert senden(client, [durch(v, 1, T0)], ZIEL).status_code == 403
    assert client.get("/api/stand", headers=posten("gibtsnicht")).status_code == 401


def test_pflichtfelder(client, v):
    for falsch in ({"wert": None}, {"startnummer": None}, {"zeit_ms": None}):
        assert senden(client, [{**durch(v, 1, T0), **falsch}], posten("kreisel")).status_code == 422
    assert senden(client, [ev("RUNDE", bezug_uuid="x", wert="Runde 1")], ZIEL).status_code == 422


def fahrt(client, v, nr=1, ohne=()):
    """Nr. fährt die Beispielstrecke: Kreisel nach 3 min, dann alle 5 min ein Messpunkt, Ziel nach 25 min."""
    start = T0 + nr * MIN
    senden(client, [ev("START", startnummer=nr, zeit_ms=start)], START)
    folge = [("Kreisel", 3), ("Brücke", 8), ("Kreisel", 13), ("Brücke", 18), ("Kreisel", 23)]
    tipps = [
        durch(v, nr, start + m * MIN, name) for i, (name, m) in enumerate(folge) if i not in ohne
    ]
    senden(client, [t for t in tipps if t["wert"] == str(v.mp["Kreisel"])], posten("kreisel"))
    senden(client, [t for t in tipps if t["wert"] == str(v.mp["Brücke"])], posten("bruecke"))
    senden(client, [ev("ZIEL", startnummer=nr, zeit_ms=start + 25 * MIN)], ZIEL)
    return tipps


def test_stand_fuer_posten_und_rennleitung(client, v):
    fahrt(client, v, 1)
    senden(client, [ev("START", startnummer=2, zeit_ms=T0 + 2 * MIN)], START)
    senden(client, [durch(v, 2, T0 + 5 * MIN)], posten("kreisel"))
    stand = client.get("/api/stand", headers=posten("kreisel")).json()
    assert stand["messpunkt"] == {
        "id": v.mp["Kreisel"],
        "name": "Kreisel",
        "strecken": [0],
        "erwartet": {"0": 3},
    }
    t1 = next(t for t in stand["teilnehmer"] if t["startnummer"] == 1)
    t2 = next(t for t in stand["teilnehmer"] if t["startnummer"] == 2)
    assert (
        t1["naechste_runde"][str(v.mp["Kreisel"])] == 4
        and t2["naechste_runde"][str(v.mp["Kreisel"])] == 2
    )
    assert len(stand["durchfahrten"]) == 4 and "korrektur_ms" in stand["durchfahrten"][0]
    bruecke = client.get("/api/stand", headers=posten("bruecke")).json()["messpunkt"]
    assert bruecke["erwartet"] == {"0": 2}
    # Rennleitung: Segmentzeiten
    leitung = client.get("/api/stand", headers=ZIEL).json()
    t1 = next(t for t in leitung["teilnehmer"] if t["startnummer"] == 1)
    assert t1["zwischenzeiten"] == [["Start→Kreisel", 3 * MIN], ["Kreisel→Brücke (1)", 5 * MIN],
                                    ["Brücke→Kreisel (1)", 5 * MIN], ["Kreisel→Brücke (2)", 5 * MIN],
                                    ["Brücke→Kreisel (2)", 5 * MIN], ["Kreisel→Ziel", 2 * MIN]]  # fmt: skip
    assert "durchfahrten_pruefen" not in leitung  # Segmente erzeugen keine Fälle


def test_posten_nach_abschluss_410(client, v):
    with client.app.state.sessionmaker() as s:
        s.get(Veranstaltung, v.id).zustand = "abgeschlossen"
        s.commit()
    assert client.get("/api/stand", headers=posten("kreisel")).status_code == 410


def test_posten_token_beim_loeschen_als_fingerabdruck(client, v):
    """Gelöschte Veranstaltung: auch Posten-Tokens antworten mit 410 (wie Start/Ziel)."""
    import re as _re

    from tests.api.test_admin import csrf

    client.post("/admin/anmelden", data={"token": "tok-admin"})
    token = posten("kreisel")["Authorization"]
    assert client.get("/api/stand", headers={"Authorization": token}).status_code == 200
    zeichen = csrf(client.get("/admin").text)
    client.post(
        f"/admin/v/{v.id}/loeschen", data={"csrf": zeichen, "bestaetigung": "1", "name": "Rund"}
    )
    r = client.get("/api/stand", headers={"Authorization": token})
    assert r.status_code == 410 and _re.search("gelöscht", r.text)


def test_posten_token_je_messpunkt_erneuerbar(client, v):
    from tests.api.test_admin import csrf

    client.post("/admin/anmelden", data={"token": "tok-admin"})
    zeichen = csrf(client.get("/admin").text)
    r = client.post(f"/admin/v/{v.id}/token", data={"csrf": zeichen, "rolle": f"posten-{v.mp['Kreisel']}"},
                    follow_redirects=True)  # fmt: skip
    assert "Posten-Token „Kreisel“ erneuert" in r.text
    assert client.get("/api/stand", headers=posten("kreisel")).status_code == 401
    assert client.get("/api/stand", headers=posten("bruecke")).status_code == 200


# --- Admin (Posten-Links, Einstellungen, Fahrer-Seite, Aufgaben) ---


def anmelden(client):
    client.post("/admin/anmelden", data={"token": "tok-admin"})


def csrf_aus(html):
    return re.search(r'name="csrf" value="([0-9a-f]+)"', html).group(1)


def test_admin_geraete_mit_posten_links(client, v):
    anmelden(client)
    html = client.get(f"/admin/v/{v.id}/geraete").text
    assert 'data-titel="Posten Kreisel"' in html and "/posten?token=tok-kreisel" in html
    assert "Strecke: Strecke 1" in html  # Karte nennt die nutzenden Strecken
    assert html.count("<svg") >= 5  # Start, Ziel, Rennleitung + 2 Posten
    r = client.post(f"/admin/v/{v.id}/token",
                    data={"csrf": csrf_aus(html), "rolle": f"posten-{v.mp['Kreisel']}"})  # fmt: skip
    assert "Posten-Token „Kreisel“ erneuert" in r.text and "tok-kreisel" not in r.text
    assert client.get("/api/stand", headers=posten("kreisel")).status_code == 401
    assert client.get("/posten").status_code == 200


def test_uhrentabelle_und_als_naechstes_mit_posten(client, v):
    anmelden(client)
    uebersicht = client.get(f"/admin/v/{v.id}").text
    assert "Posten Kreisel noch nicht verbunden" in uebersicht
    with client.app.state.sessionmaker() as s:
        s.add(GeraetUhr(veranstaltung_id=v.id, geraet="handy-k", rolle=f"posten-{v.mp['Kreisel']}",
                        abweichung_ms=0, gemessen_ms=T0))  # fmt: skip
        s.commit()
    assert "Posten Kreisel noch nicht verbunden" not in client.get(f"/admin/v/{v.id}").text
    assert "Posten Brücke noch nicht verbunden" in client.get(f"/admin/v/{v.id}").text
    geraete = client.get(f"/admin/v/{v.id}/geraete").text
    assert "handy-k" in geraete and "Posten Kreisel" in geraete


def test_messpunkte_seite_verbunden_und_entfernen_gesperrt(client, v):
    """Liste mit nutzenden Strecken, Posten-Gerät verbunden ja/nein; genutzte Messpunkte
    lassen sich nicht entfernen (Grund steht dabei)."""
    anmelden(client)
    with client.app.state.sessionmaker() as s:
        s.add(GeraetUhr(veranstaltung_id=v.id, geraet="handy-k", rolle=f"posten-{v.mp['Kreisel']}",
                        abweichung_ms=0, gemessen_ms=T0))  # fmt: skip
        s.commit()
    html = client.get(f"/admin/v/{v.id}/messpunkte").text
    kreisel = re.search(r'<tr [^>]*data-messpunkt="Kreisel">.*?</tr>', html, re.DOTALL).group(0)
    bruecke = re.search(r'<tr [^>]*data-messpunkt="Brücke">.*?</tr>', html, re.DOTALL).group(0)
    assert "verbunden" in kreisel and "nicht verbunden" not in kreisel
    assert "nicht verbunden" in bruecke
    assert (
        re.search(r'name="mp_entfernen_\d+"[^>]*disabled', kreisel)
        and "Gesperrt: genutzt von" in kreisel
    )
    # ungenutzter Messpunkt: entfernbar
    with client.app.state.sessionmaker() as s:
        s.add(Messpunkt(veranstaltung_id=v.id, name="Abzweig", position=9))
        s.commit()
    html = client.get(f"/admin/v/{v.id}/messpunkte").text
    abzweig = re.search(r'<tr [^>]*data-messpunkt="Abzweig">.*?</tr>', html, re.DOTALL).group(0)
    assert "disabled" not in abzweig and "Gesperrt" not in abzweig


def test_messpunkte_im_reiter_verlauf_und_abschnitte_auf_der_strecken_seite(client, v):
    """Messpunkte im Reiter „Strecken“, Streckenverlauf und Abschnitte auf der Seite der Strecke."""
    anmelden(client)
    html = client.get(f"/admin/v/{v.id}/messpunkte").text
    assert 'id="messpunkte"' in html and 'name="mp_name_1" value="Kreisel"' in html  # nach „Start“
    assert (
        "Start · Kreisel · Brücke · Kreisel · Brücke · Kreisel · Ziel"
        in client.get(f"/admin/v/{v.id}/strecken").text
    )  # Kurzinfo der Strecke
    with client.app.state.sessionmaker() as s:
        sid = st.strecken_von(s, s.get(Veranstaltung, v.id))[0].id
        mps = {m.name: m.id for m in st.messpunkte_von(s, s.get(Veranstaltung, v.id))}
    seite = client.get(f"/admin/v/{v.id}/strecken/{sid}").text
    start = json.loads(re.search(r'id="verlauf-start">(.*?)</script>', seite, re.DOTALL).group(1))
    assert start["punkte"] == [
        "Kreisel",
        {"block": "Runde", "wdh": 2, "punkte": ["Brücke", "Kreisel"]},
    ]
    assert [x["km"] for x in start["segmente"]] == ["1,5", "3,5", "3,5", "0,5"]
    assert "Kreisel 3×, Brücke 2×" in seite and 'id="abschnitte"' in seite
    assert "mindestrundenzeit" not in seite and "strecke_runden_0" not in seite
    zeichen = csrf_aus(html)
    # neuer Messpunkt „Abzweig“ (noch ungenutzt: Hinweis)
    r = client.post(f"/admin/v/{v.id}/messpunkte", data={"csrf": zeichen, "mp_anzahl": "3",
                    "mp_id_0": str(mps["Kreisel"]), "mp_name_0": "Kreisel", "mp_id_1": str(mps["Brücke"]),
                    "mp_name_1": "Brücke", "mp_id_2": "", "mp_name_2": "Abzweig"})  # fmt: skip
    assert (
        "Messpunkte gespeichert" in r.text
        and "„Abzweig“ kommt noch in keiner Strecke vor" in r.text
    )
    # Punktfolge ohne Brücke, mit Abzweig und Rundenblock aus einem Messpunkt
    folge = ["Abzweig", "Kreisel", {"block": "Runde", "wdh": 3, "punkte": ["Kreisel"]}]
    daten = {"csrf": zeichen, "verlauf": json.dumps({"punkte": folge}), "sg_km_0": "1,0", "sg_km_2": "7,0",
             **{f"sg_anzeigen_{k}": "1" for k in range(4)}}  # fmt: skip
    r = client.post(f"/admin/v/{v.id}/strecken/{sid}", data=daten)
    assert "Strecke gespeichert" in r.text and "„Brücke“ kommt noch in keiner Strecke vor" in r.text
    with client.app.state.sessionmaker() as s:
        x = s.get(Veranstaltung, v.id)
        namen = {str(m.id): m.name for m in st.messpunkte_von(s, x)}
        teile = st.teile_von(st.strecken_von(s, x)[0])
        assert segmente.abfolge_text(teile, namen) == (
            "Start · Abzweig · Kreisel · Kreisel · Kreisel · Kreisel · Ziel"
        )
        assert [(t.name, t.wdh, [sg.km for sg in t.segmente]) for t in teile] == [
            ("", 1, [1.0]), ("", 1, [None]), ("Runde", 3, [7.0]), ("", 1, [None])]  # fmt: skip
    # Rundenblock endet nicht, wo er beginnt → Fehler, nichts gespeichert
    daten["verlauf"] = json.dumps(
        {"punkte": ["Kreisel", {"block": "Runde", "wdh": 2, "punkte": ["Abzweig"]}]}
    )
    assert "muss dort auch enden" in client.post(f"/admin/v/{v.id}/strecken/{sid}", data=daten).text
    # Abschnitt „Runde“ = Kreisel bis nächster Kreisel; „Kreisel→Ziel“ nicht angezeigt – das stellt
    # inzwischen unter „Öffentlichkeit & Zielmonitor“ ein („Spalten in den Ergebnissen“)
    daten["verlauf"] = json.dumps({"punkte": folge})
    r = client.post(f"/admin/v/{v.id}/strecken/{sid}", data=daten | {"ab_anzahl": "1",
                    "ab_name_0": "Runde", "ab_von_0": "Kreisel", "ab_nach_0": "Kreisel", "ab_art_0": "wiederkehrend"})  # fmt: skip
    assert "Strecke gespeichert" in r.text
    with client.app.state.sessionmaker() as s:
        alle = st.abschnitte_von(st.strecken_von(s, s.get(Veranstaltung, v.id))[0])
    sichtbar = [f"{sid}:{a.id}" for a in alle if a.name != "Kreisel→Ziel"]
    r = client.post(f"/admin/v/{v.id}/spalten", data={"csrf": daten["csrf"], "spalten_strecke": str(sid),
                                                      "anzeigen": sichtbar})  # fmt: skip
    assert "Ergebnistabelle gespeichert" in r.text
    r = client.post(f"/admin/v/{v.id}/strecken/{sid}", data=daten | {"ab_anzahl": "1", "ab_id_0": "a1",
                    "ab_name_0": "Runde", "ab_von_0": "Kreisel", "ab_nach_0": "Kreisel", "ab_art_0": "wiederkehrend"})  # fmt: skip
    assert "Strecke gespeichert" in r.text  # erneutes Speichern im Editor lässt die Anzeige stehen
    with client.app.state.sessionmaker() as s:
        alle = st.abschnitte_von(st.strecken_von(s, s.get(Veranstaltung, v.id))[0])
        assert [(a.name, a.aus, a.vergleich) for a in alle if a.aus or not a.segment] == [
            ("Kreisel→Ziel", True, False), ("Runde", False, False)]  # fmt: skip


def test_auswertungsabschnitte_verstaendlich(client, v):
    """Einleitungstext, „jede Durchfahrt“/„eine bestimmte Durchfahrt“, Vorschau in Worten."""
    anmelden(client)
    with client.app.state.sessionmaker() as s:
        x = st.strecken_von(s, s.get(Veranstaltung, v.id))[0]
        sid = x.id
        x.abschnitte_json = json.dumps({"segmente": {}, "eigene": [
            {"id": "a1", "name": "Runde", "von": str(v.mp["Kreisel"]), "nach": str(v.mp["Kreisel"]),
             "art": "wiederkehrend", "vergleich": True},
            {"id": "a2", "name": "1. Runde", "von": str(v.mp["Kreisel"]), "nach": str(v.mp["Kreisel"]),
             "art": "einmalig", "von_nr": 1, "nach_nr": 2}]})  # fmt: skip
        s.commit()
    seite = client.get(f"/admin/v/{v.id}/strecken/{sid}").text
    assert (
        "Ein Auswertungsabschnitt ist ein Teil der Strecke, für den eine eigene Zeit ermittelt wird"
        in seite
    )
    assert "Anzeigen (Spalten) und Vergleich (Zielmonitor) stellst du unter Einstellungen" in seite
    assert '<option value="wiederkehrend" selected>jede Durchfahrt</option>' in seite
    assert '<option value="einmalig" selected>eine bestimmte Durchfahrt</option>' in seite
    assert ">wiederkehrend<" not in seite and ">einmalig<" not in seite
    assert "Runde: Kreisel → Brücke → Kreisel, 2-mal" in seite and "mit Vergleich" not in seite
    assert "1. Runde: Kreisel → Brücke → Kreisel, nur die 1. Durchfahrt an Kreisel" in seite


def test_punktfolge_verlustfrei_uebernommen(client, v):
    """Die bestehende Definition kommt als Punktfolge in den Editor und unverändert zurück."""
    anmelden(client)
    with client.app.state.sessionmaker() as s:
        x = st.strecken_von(s, s.get(Veranstaltung, v.id))[0]
        sid, teile_alt = x.id, x.teile_json
        x.abschnitte_json = json.dumps({"segmente": {"1.0": {"aus": True, "vergleich": False},
                                                     "1.1": {"aus": False, "vergleich": True}}, "eigene": []})  # fmt: skip
        s.commit()
        abschnitte_alt = st.abschnitte_von(x)
    seite = client.get(f"/admin/v/{v.id}/strecken/{sid}").text
    start = json.loads(re.search(r'id="verlauf-start">(.*?)</script>', seite, re.DOTALL).group(1))
    daten = {
        "csrf": csrf_aus(seite),
        "verlauf": json.dumps({"punkte": start["punkte"]}),
        "ab_anzahl": "0",
    }
    for k, sgm in enumerate(start["segmente"]):  # so sendet der Editor die abgeleiteten Segmente
        daten |= {f"sg_name_{k}": sgm["name"], f"sg_km_{k}": sgm["km"]}
        daten |= {f"sg_anzeigen_{k}": "1"} if sgm["anzeigen"] else {}
        daten |= {f"sg_vergleich_{k}": "1"} if sgm["vergleich"] else {}
    assert "Strecke gespeichert" in client.post(f"/admin/v/{v.id}/strecken/{sid}", data=daten).text
    with client.app.state.sessionmaker() as s:
        x = st.strecken_von(s, s.get(Veranstaltung, v.id))[0]
        ohne_vergleich = lambda teile: [
            (t.name, t.wdh, [(g.name, g.von, g.nach, g.km) for g in t.segmente]) for t in teile
        ]
        assert ohne_vergleich(segmente.aus_json(x.teile_json)) == ohne_vergleich(
            segmente.aus_json(teile_alt)
        )
        assert (
            st.abschnitte_von(x) == abschnitte_alt
        )  # anzeigen und Vergleich je Segment unverändert


def test_fahrer_seite_zeigt_zwischenzeiten_mit_herkunft(client, v):
    a = fahrt(client, v, 1)[0]
    senden(client, [ev("ZEITKORREKTUR", bezug_uuid=a["uuid"], delta_ms=1000)], posten("kreisel"))
    anmelden(client)
    tid = re.search(
        rf'href="/admin/v/{v.id}/teilnehmer/(\d+)"', client.get(f"/admin/v/{v.id}/teilnehmer").text
    )
    html = client.get(f"/admin/v/{v.id}/teilnehmer/{tid.group(1)}").text
    assert 'id="zwischenzeiten"' in html and "3. · Kreisel (2)" in html and "13:00,0" in html
    assert (
        "korrigiert +1,0 s" in html
        and 'id="segmentzeiten"' in html
        and "Kreisel→Brücke (2)" in html
    )
    assert "<td>Kreisel</td>" in html
    # Durchfahrt stornieren (Admin)
    r = client.post(f"/admin/v/{v.id}/korrektur", data={"csrf": csrf_aus(html), "aktion": "stornieren",
                    "stempel": a["uuid"], "notiz": "falsch getippt"})  # fmt: skip
    assert r.status_code == 200 and "storniert" in r.text.lower()


# --- Auswertung sichtbar (Ergebnis Admin/öffentlich, Druck, CSV) ---


def test_segmentzeiten_je_spalte_in_ergebnis_druck_und_csv(client, v):
    fahrt(client, v, 1, ohne={2})  # Kreisel (2) vergessen: danach keine Segmentzeiten
    # je Abschnitt (Segmente und Rundenblöcke sind Abschnitte) und Durchfahrt
    labels = ["Start→Kreisel", "Kreisel→Brücke (1)", "Kreisel→Brücke (2)", "Brücke→Kreisel (1)",
              "Brücke→Kreisel (2)", "Kreisel→Ziel", "Runde (1)", "Runde (2)"]  # fmt: skip
    d = client.get("/api/oeffentlich").json()
    assert d["zwischenzeiten"] and d["strecken"][0]["segmente"] == labels
    assert (
        d["gesamt"][0]["segmente"] == ["3:00,0", "5:00,0", "", "", "", "", "", ""]
        and d["gesamt"][0]["zeit"] == "25:00,0"
    )
    seite = client.get("/ergebnisse").text
    assert (
        all(f'<th class="segment-kopf">{x}</th>' in seite for x in labels) and ">5:00,0<" in seite
    )
    druck = client.get("/ergebnisse/druck").text
    assert '<th class="segment-kopf">Kreisel→Brücke (1)</th>' in druck and ">3:00,0<" in druck
    zeilen = client.get("/ergebnisse.csv").content.decode("utf-8-sig").splitlines()
    kopf = zeilen[2].split(";")
    assert kopf[-9:-1] == labels and zeilen[3].split(";")[-9:-1] == ["3:00,0", "5:00,0"] + [""] * 6
    anmelden(client)
    erg = client.get(f"/admin/v/{v.id}/ergebnis").text
    assert '<th class="segment-kopf">Kreisel→Ziel</th>' in erg and ">5:00,0<" in erg
    # „Zwischenzeiten öffentlich“ aus → öffentlich nichts, im Admin weiter
    with client.app.state.sessionmaker() as s:
        s.get(Veranstaltung, v.id).zwischenzeiten_oeffentlich = False
        s.commit()
    d = client.get("/api/oeffentlich").json()
    assert (
        not d["zwischenzeiten"]
        and d["gesamt"][0]["segmente"] == []
        and d["strecken"][0]["segmente"] == []
    )
    assert "segment-kopf" not in client.get("/ergebnisse").text
    assert "Start→Kreisel" not in client.get("/ergebnisse/druck").text
    assert "Start→Kreisel" not in client.get("/ergebnisse.csv").content.decode("utf-8-sig")
    admin_csv = client.get(f"/admin/v/{v.id}/export/ergebnis.csv").content.decode("utf-8-sig")
    assert "Kreisel→Brücke (1)" in admin_csv and "5:00,0" in admin_csv
    assert (
        '<th class="segment-kopf">Start→Kreisel</th>'
        in client.get(f"/admin/v/{v.id}/ergebnis").text
    )


def test_ohne_segmente_keine_zwischenzeiten_spalte(client, v):
    with client.app.state.sessionmaker() as s:
        strecke = st.strecken_von(s, s.get(Veranstaltung, v.id))[0]
        strecke.teile_json = None
        for m in st.messpunkte_von(s, s.get(Veranstaltung, v.id)):
            s.delete(m)
        s.commit()
    senden(client, [ev("START", startnummer=1, zeit_ms=T0)], START)
    senden(client, [ev("ZIEL", startnummer=1, zeit_ms=T0 + 30 * MIN)], ZIEL)
    assert not client.get("/api/oeffentlich").json()["zwischenzeiten"]
    assert "Zwischenzeiten" not in client.get("/ergebnisse").text
    anmelden(client)
    assert "segment-kopf" not in client.get(f"/admin/v/{v.id}/ergebnis").text
    assert "Start→" not in client.get(f"/admin/v/{v.id}/export/ergebnis.csv").content.decode(
        "utf-8-sig"
    )


# --- Doppeltipp automatisch verworfen, im Protokoll sichtbar und zurücknehmbar ---


def test_doppeltipp_im_protokoll_und_doch_werten(client, v):
    tipps = fahrt(client, v, 1)
    doppelt = durch(v, 1, tipps[0]["zeit_ms"] + 3_000)
    senden(client, [doppelt], posten("kreisel"))
    # keine Fälle: weder Rennleitung noch Aufgaben
    assert "durchfahrten_pruefen" not in client.get("/api/stand", headers=ZIEL).json()
    anmelden(client)
    assert not re.search(r"\d+ offene Aufgabe", client.get(f"/admin/v/{v.id}").text)
    html = client.get(f"/admin/v/{v.id}/protokoll?art=DURCHFAHRT").text
    assert "nicht gezählt: Doppeltipp" in html and f'value="{doppelt["uuid"]}"' in html
    assert ">Kreisel<" in html  # Messpunkt-Name statt ID
    r = client.post(f"/admin/v/{v.id}/korrektur", data={"csrf": csrf_aus(html), "aktion": "doppeltipp_werten",
                    "stempel": doppelt["uuid"], "notiz": "zwei Fahrer",
                    "zurueck": f"/admin/v/{v.id}/protokoll"})  # fmt: skip
    assert "Doppeltipp zählt wieder" in r.text and "nicht gezählt: Doppeltipp" not in r.text
    assert "Durchfahrt gewertet" in r.text
    # nochmals: kein verworfener Doppeltipp mehr
    r = client.post(f"/admin/v/{v.id}/korrektur", data={"csrf": csrf_aus(html), "aktion": "doppeltipp_werten",
                    "stempel": doppelt["uuid"], "notiz": "x", "zurueck": f"/admin/v/{v.id}/protokoll"})  # fmt: skip
    assert "kein verworfener Doppeltipp" in r.text


def test_fahrer_seite_alle_durchfahrten_mit_zuordnung(client, v):
    tipps = fahrt(client, v, 1, ohne={1})  # Brücke (1) vergessen
    senden(client, [durch(v, 1, tipps[0]["zeit_ms"] + 2_000)], posten("kreisel"))  # Doppeltipp
    anmelden(client)
    tid = re.search(
        rf'href="/admin/v/{v.id}/teilnehmer/(\d+)"', client.get(f"/admin/v/{v.id}/teilnehmer").text
    )
    html = client.get(f"/admin/v/{v.id}/teilnehmer/{tid.group(1)}").text
    tabelle = html.split('id="durchfahrten"')[1].split("</table>")[0]
    zeilen = re.findall(r"<tr data-uuid=.*?</tr>", tabelle, re.DOTALL)
    assert len(zeilen) == 6  # 4 gezählt, 1 Doppeltipp, 1 fehlend
    assert "1. · Kreisel (1)" in zeilen[0] and "nicht gezählt: Doppeltipp" in zeilen[1]
    assert "2. · Brücke (1)" in zeilen[2] and "nicht erfasst" in zeilen[2]
    assert "Doch werten" in zeilen[1] and "3. · Kreisel (2)" in zeilen[3]


def test_rennleitung_kann_keine_runde_mehr_senden(client, v):
    d = durch(v, 1, T0)
    senden(client, [d], posten("kreisel"))
    assert (
        senden(client, [ev("RUNDE", bezug_uuid=d["uuid"], wert="VERWORFEN")], ZIEL).status_code
        == 403
    )


# --- Zielmonitor konfigurierbar -------------------------------------------------


def test_zielmonitor_seite_standard_speichern_reihenfolge(client, v):
    anmelden(client)
    r = client.get(f"/admin/v/{v.id}/zielmonitor", follow_redirects=False)  # Jetzt Reiter
    assert r.status_code == 307 and r.headers["location"].endswith("/einstellungen/zielmonitor")
    html = client.get(f"/admin/v/{v.id}/zielmonitor").text
    assert 'data-seite="zielmonitor"' not in html and 'id="monitor-vorschau"' in html
    assert f'href="/monitor?veranstaltung={v.id}"' in html
    assert html.count('<tr data-typ="ergebnis"') == 1  # Standard = heutiges Verhalten
    d = client.get("/api/oeffentlich").json()
    assert d["monitor"]["seiten"] == [
        {"typ": "ergebnis", "dauer": 10, "live": True, "abzeichen": False, "strecken": []}
    ]
    assert d["monitor"]["live"] == []
    zeichen = csrf_aus(html)
    url = f"/admin/v/{v.id}/zielmonitor"
    basis = {"csrf": zeichen, "seite_an_0": "1", "seite_dauer_0": "12", "seite_live_0": "1"}
    r = client.post(url, data={**basis, "aktion": "neu", "neuer_typ": "ergebnis"})
    assert "Zielmonitor gespeichert" in r.text and r.text.count('<tr data-typ="ergebnis"') == 2
    with client.app.state.sessionmaker() as s:
        sid = st.strecken_von(s, s.get(Veranstaltung, v.id))[0].id
    zwei = {**basis, "seite_dauer_1": "5", "seite_an_1": "1", "live_abschnitt": f"{sid}:s1.0"}
    r = client.post(url, data={**zwei, "aktion": "auf:1"})
    assert (
        'name="seite_dauer_0" value="5"' in r.text and 'name="seite_dauer_1" value="12"' in r.text
    )
    d = client.get("/api/oeffentlich").json()
    assert [x["dauer"] for x in d["monitor"]["seiten"]] == [5, 12]
    assert d["monitor"]["seiten"][0]["live"] is False  # Live-Haken fehlte bei der neuen Seite
    assert kern(d["monitor"]["live"]) == [
        {"id": "0:s1.0", "strecke": 0, "abschnitt": "s1.0", "vergleich": False}
    ]
    # ausgeschaltete Seiten fehlen; ungültige Dauer → Fehler
    r = client.post(
        url,
        data={"csrf": zeichen, "seite_dauer_0": "5", "seite_dauer_1": "12", "aktion": "speichern"},
    )
    assert (
        client.get("/api/oeffentlich").json()["monitor"]["seiten"][0]["dauer"] == 10
    )  # keine an: Standard
    r = client.post(
        url,
        data={"csrf": zeichen, "seite_dauer_0": "1", "seite_dauer_1": "12", "aktion": "speichern"},
    )
    assert "Anzeigedauer 3 bis 600" in r.text
    r = client.post(
        url, data={"csrf": zeichen, "seite_dauer_0": "5", "seite_dauer_1": "12", "aktion": "weg:0"}
    )
    assert r.text.count('<tr data-typ="ergebnis"') == 1


def test_live_abschnittszeit_im_oeffentlichen_stand(client, v):
    import json

    with client.app.state.sessionmaker() as s:
        x = s.get(Veranstaltung, v.id)
        sid = st.strecken_von(s, x)[0].id
        x.monitor_json = json.dumps({"seiten": [{"typ": "ergebnis", "an": True, "dauer": 10, "live": True}],
                                     "live_abschnitte": [f"{sid}:s1.0"]})  # fmt: skip
        s.commit()
    start = T0 + MIN
    senden(client, [ev("START", startnummer=1, zeit_ms=start)], START)
    senden(client, [durch(v, 1, start + 3 * MIN), durch(v, 1, start + 13 * MIN)], posten("kreisel"))
    senden(
        client,
        [durch(v, 1, start + 8 * MIN, "Brücke"), durch(v, 1, start + 17 * MIN, "Brücke")],
        posten("bruecke"),
    )
    u = client.get("/api/oeffentlich").json()["unterwegs"][0]
    assert u["abschnitte"] == {
        "0:s1.0": {"label": "Kreisel→Brücke (2)", "zeit": "4:00", "pfeil": "▼", "diff": "60 s"}
    }


# --- Ranglisten je Abschnitt ----------------------------------------------------


def runde_einrichten(client, v, top=3):
    """Abschnitt „Runde“ = Kreisel bis nächster Kreisel, Monitor-Seite „Rangliste Abschnitt“."""
    import json

    with client.app.state.sessionmaker() as s:
        x = s.get(Veranstaltung, v.id)
        strecke = st.strecken_von(s, x)[0]
        k = str(v.mp["Kreisel"])
        strecke.abschnitte_json = json.dumps({"segmente": {}, "eigene": [
            {"id": "a1", "name": "Runde", "von": k, "nach": k, "art": "wiederkehrend", "vergleich": True}]})  # fmt: skip
        x.monitor_json = json.dumps({"seiten": [{"typ": "rangliste", "an": True, "dauer": 15, "live": True,
                                                 "abschnitt": f"{strecke.id}:a1", "top": top}], "live_abschnitte": []})  # fmt: skip
        s.commit()


def test_rangliste_abschnitt_admin_oeffentlich_druck_csv_monitor(client, v):
    runde_einrichten(client, v, top=1)
    fahrt(client, v, 1)  # Runden je 10 min
    start = T0 + 2 * MIN  # Nr. 2: Runden 9 und 11 min
    senden(client, [ev("START", startnummer=2, zeit_ms=start)], START)
    senden(client, [durch(v, 2, start + m * MIN) for m in (3, 12, 23)], posten("kreisel"))
    senden(client, [durch(v, 2, start + m * MIN, "Brücke") for m in (7, 17)], posten("bruecke"))
    d = client.get("/api/oeffentlich").json()
    assert {"schluessel": "0:a1", "titel": "Runde"} in d["ranglisten"] and len(
        d["ranglisten"]
    ) == 5  # 4 Segmente + Runde
    seite = d["monitor"]["seiten"][0]
    assert seite["typ"] == "rangliste" and [z["startnummer"] for z in seite["zeilen"]] == [
        2
    ]  # Top 1
    html = client.get("/ergebnisse?rangliste=0:a1").text
    assert 'id="rangliste"' in html and "Rangliste Runde" in html and "9:00,0" in html
    assert "Runde (1)" in html
    assert "Rangliste Runde" in client.get("/ergebnisse/druck?rangliste=0:a1").text
    csv_ = client.get("/ergebnisse.csv?rangliste=0:a1").content.decode("utf-8-sig").splitlines()
    assert csv_[2].startswith("Rang;Startnummer") and csv_[3].split(";")[:2] == ["1", "2"]
    anmelden(client)
    erg = client.get(f"/admin/v/{v.id}/ergebnis?rangliste=0:a1").text
    assert 'id="rangliste"' in erg and "Runde (1)" in erg
    assert "Rangliste Runde" in client.get(f"/admin/v/{v.id}/drucken").text
    assert client.get(f"/admin/v/{v.id}/export/ergebnis.csv?rangliste=0:a1").status_code == 200
    # Zwischenzeiten nicht öffentlich: keine öffentlichen Ranglisten
    with client.app.state.sessionmaker() as s:
        s.get(Veranstaltung, v.id).zwischenzeiten_oeffentlich = False
        s.commit()
    assert client.get("/api/oeffentlich").json()["ranglisten"] == []
    assert 'id="rangliste"' not in client.get("/ergebnisse?rangliste=0:a1").text
    assert client.get("/ergebnisse.csv?rangliste=0:a1").status_code == 404


def test_zielmonitor_seite_rangliste_einstellen(client, v):
    runde_einrichten(client, v)
    anmelden(client)
    html = client.get(f"/admin/v/{v.id}/zielmonitor").text
    assert '<tr data-typ="rangliste"' in html and 'name="seite_top_0" value="3"' in html
    with client.app.state.sessionmaker() as s:
        sid = st.strecken_von(s, s.get(Veranstaltung, v.id))[0].id
    r = client.post(f"/admin/v/{v.id}/zielmonitor", data={"csrf": csrf_aus(html), "aktion": "speichern", "seite_an_0": "1",
                    "seite_dauer_0": "20", "seite_abschnitt_0": f"{sid}:a1", "seite_top_0": "5"})  # fmt: skip
    assert "gespeichert" in r.text and 'name="seite_top_0" value="5"' in r.text
    seite = client.get("/api/oeffentlich").json()["monitor"]["seiten"][0]
    assert (seite["typ"], seite["top"], seite["titel"], seite["dauer"]) == (
        "rangliste",
        5,
        "Runde",
        20,
    )


def test_zwischenzeiten_oeffentlich_im_reiter_speichern(client, v):
    anmelden(client)
    html = client.get(f"/admin/v/{v.id}/einstellungen/zielmonitor").text
    r = client.post(f"/admin/v/{v.id}/oeffentlichkeit", data={"csrf": csrf_aus(html)})
    assert r.url.path.endswith("/einstellungen/zielmonitor") and "Gespeichert" in r.text
    with client.app.state.sessionmaker() as s:
        assert not s.get(Veranstaltung, v.id).zwischenzeiten_oeffentlich
    # Monitor-Formular im Reiter kehrt dorthin zurück
    r = client.post(f"/admin/v/{v.id}/zielmonitor", data={"csrf": csrf_aus(html), "aktion": "speichern",
                    "ziel": f"/admin/v/{v.id}/einstellungen/zielmonitor", "seite_an_0": "1", "seite_dauer_0": "10"})  # fmt: skip
    assert r.url.path.endswith("/einstellungen/zielmonitor") and "Zielmonitor gespeichert" in r.text


# --- Strecke als Datei speichern und laden, Meta-Infos ----------------------------


def test_strecke_speichern_laden_und_meta(client, v):
    anmelden(client)
    with client.app.state.sessionmaker() as s:
        sid = st.strecken_von(s, s.get(Veranstaltung, v.id))[0].id
    seite = client.get(f"/admin/v/{v.id}/strecken/{sid}").text
    zeichen = csrf_aus(seite)
    assert 'id="strecke-datei"' in seite and 'id="komoot-oeffnen"' not in seite
    # Meta-Infos: Komoot nur mit https
    r = client.post(f"/admin/v/{v.id}/strecken/{sid}", data={"csrf": zeichen, "name": "Rundkurs",
                    "laenge": "16,0", "komoot": "http://komoot.com/tour/1", "klasse": []})  # fmt: skip
    assert "https-Adresse" in r.text
    alle = re.findall(r'name="klasse" value="([^"]+)" checked', seite)
    r = client.post(f"/admin/v/{v.id}/strecken/{sid}", data={"csrf": zeichen, "name": "Rundkurs",
                    "laenge": "16,0", "beschreibung": "Kottenforst", "komoot": "https://www.komoot.com/tour/9",
                    "klasse": alle})  # fmt: skip
    assert "Strecke gespeichert" in r.text and 'href="https://www.komoot.com/tour/9"' in r.text
    assert 'id="komoot-oeffnen"' in r.text and "Kottenforst" in r.text
    assert "komoot" not in client.get("/ergebnisse").text.lower()  # nur im Admin
    # Datei speichern
    d = client.get(f"/admin/v/{v.id}/strecken/{sid}/datei")
    assert d.status_code == 200 and "strecke-Rundkurs.toml" in d.headers["content-disposition"]
    text = d.text
    assert 'name = "Rundkurs"' in text and 'komoot = "https://www.komoot.com/tour/9"' in text
    # Datei laden: Vorschau, dann übernehmen mit anderem Namen (Namenskonflikt)
    laden = client.get(f"/admin/v/{v.id}/strecken/laden").text
    assert 'enctype="multipart/form-data"' in laden and 'class="btn btn-quiet abbrechen"' in laden
    r = client.post(f"/admin/v/{v.id}/strecken/laden", data={"csrf": zeichen},
                    files={"datei": ("strecke.toml", text.encode(), "application/toml")})  # fmt: skip
    assert (
        'id="vorschau"' in r.text
        and 'id="namenskonflikt"' in r.text
        and "wiederverwendet: Start, Kreisel, Brücke, Ziel" in r.text
    )
    assert "Start · Kreisel · Brücke · Kreisel · Brücke · Kreisel · Ziel" in r.text
    r = client.post(
        f"/admin/v/{v.id}/strecken/uebernehmen",
        data={"csrf": zeichen, "inhalt": text, "name": "Rundkurs"},
    )
    assert "schon eine Strecke" in r.text
    r = client.post(
        f"/admin/v/{v.id}/strecken/uebernehmen",
        data={"csrf": zeichen, "inhalt": text, "name": "Kopie"},
    )
    assert "aus der Datei übernommen" in r.text and r.url.path.startswith(
        f"/admin/v/{v.id}/strecken/"
    )
    with client.app.state.sessionmaker() as s:
        x = s.get(Veranstaltung, v.id)
        assert [y.name for y in st.strecken_von(s, x)] == ["Rundkurs", "Kopie"] and len(
            st.messpunkte_von(s, x)
        ) == 2
    # Datei mit Fehler: Übernehmen gesperrt, Hinweise sichtbar
    r = client.post(f"/admin/v/{v.id}/strecken/laden", data={"csrf": zeichen},
                    files={"datei": ("x.toml", b"version = 2\\nname = 'X'\\n", "application/toml")})  # fmt: skip
    assert 'id="vorschau-fehler"' in r.text and 'id="uebernehmen" disabled' in r.text


# --- Rundenblock automatisch als Auswertungsabschnitt ----------------------------


def test_rundenblock_abschnitt_im_editor_und_monitor(client, v):
    anmelden(client)
    with client.app.state.sessionmaker() as s:
        sid = st.strecken_von(s, s.get(Veranstaltung, v.id))[0].id
    seite = client.get(f"/admin/v/{v.id}/strecken/{sid}").text
    zeile = re.search(
        r'<tr data-index="0" data-abschnitt="b1" class="ab-block">(.*?)</tr>', seite, re.DOTALL
    ).group(1)
    assert (
        "<strong>Runde</strong>" in zeile and "jede Durchfahrt" in zeile and "automatisch" in zeile
    )
    assert "ab_vergleich_0" not in zeile and "ab_aus_0" not in zeile  # Nicht mehr hier
    assert "ab_entfernen_0" not in zeile  # nicht löschbar
    assert "Runde: Kreisel → Brücke → Kreisel, 2-mal" in zeile
    # im Zielmonitor wählbar: Live-Bereich und Ranglisten-Seite
    html = client.get(f"/admin/v/{v.id}/zielmonitor").text
    assert f'name="live_abschnitt" value="{sid}:b1"' in html
    r = client.post(f"/admin/v/{v.id}/zielmonitor", data={"csrf": csrf_aus(html), "aktion": "neu", "neuer_typ": "rangliste",
                    "seite_an_0": "1", "seite_dauer_0": "10", "live_abschnitt": f"{sid}:b1"})  # fmt: skip
    assert f'<option value="{sid}:b1"' in r.text
    fahrt(client, v, 1)
    d = client.get("/api/oeffentlich").json()
    assert kern(d["monitor"]["live"]) == [
        {"id": "0:b1", "strecke": 0, "abschnitt": "b1", "vergleich": False}
    ]
    assert {"schluessel": "0:b1", "titel": "Runde"} in d["ranglisten"]
    assert "10:00,0" in client.get("/ergebnisse?rangliste=0:b1").text  # beide Runden je 10 min
    # ausblenden („Spalten in den Ergebnissen“) → aus der Monitor-Auswahl verschwunden
    with client.app.state.sessionmaker() as s:
        alle = st.abschnitte_von(st.strecken_von(s, s.get(Veranstaltung, v.id))[0])
    r = client.post(f"/admin/v/{v.id}/spalten", data={"csrf": csrf_aus(seite), "spalten_strecke": str(sid),
                    "anzeigen": [f"{sid}:{a.id}" for a in alle if not a.block]})  # fmt: skip
    assert "Ergebnistabelle gespeichert" in r.text
    with client.app.state.sessionmaker() as s:
        b = [
            a
            for a in st.abschnitte_von(st.strecken_von(s, s.get(Veranstaltung, v.id))[0])
            if a.block
        ]
        assert [a.aus for a in b] == [True]
    monitor = client.get(f"/admin/v/{v.id}/zielmonitor").text
    assert (
        f'name="live_abschnitt" value="{sid}:b1"' not in monitor
    )  # nur noch in „Spalten“ (abgewählt)
    assert f'name="anzeigen" value="{sid}:b1" aria-label="Strecke 1: Runde anzeigen" >' in monitor


def test_rundenblock_abschnitt_hinweis_statt_duplikat(client, v):
    runde_einrichten(client, v)  # eigener Abschnitt „Runde“ = Kreisel bis nächster Kreisel
    anmelden(client)
    with client.app.state.sessionmaker() as s:
        x = st.strecken_von(s, s.get(Veranstaltung, v.id))[0]
        sid = x.id
        assert [a.id for a in st.abschnitte_von(x) if not a.segment] == ["a1"]
    seite = client.get(f"/admin/v/{v.id}/strecken/{sid}").text
    assert "Der eigene Abschnitt „Runde“ wertet schon dasselbe aus" in seite
    assert 'class="ab-block"' not in seite


def test_rundenblock_abschnitt_in_streckendatei(client, v):
    from app import strecken_datei

    with client.app.state.sessionmaker() as s:
        x = st.strecken_von(s, s.get(Veranstaltung, v.id))[0]
        x.abschnitte_json = json.dumps({"bloecke": {"1": {"aus": True, "vergleich": False}}})
        s.commit()
        text = strecken_datei.strecke_toml(s, s.get(Veranstaltung, v.id), x)
    # Darstellung gehört nicht mehr in die Datei; der Rundenblock ergibt sich aus dem Verlauf
    assert "block_abschnitt" not in text and '[[teil]]\nname = "Runde"\nwdh = 2' in text
    assert strecken_datei.lesen(text.encode()).hinweise == []


# --- Anfang und Ende als Auswahl aus der Abfolge ---------------------------------


def test_abschnitt_anfang_ende_auswahl_und_monitor(client, v):
    anmelden(client)
    k, b = str(v.mp["Kreisel"]), str(v.mp["Brücke"])
    with client.app.state.sessionmaker() as s:
        x = st.strecken_von(s, s.get(Veranstaltung, v.id))[0]
        sid = x.id
        x.abschnitte_json = json.dumps({"eigene": [
            {"id": "a1", "name": "2. Runde", "von": k, "nach": k, "art": "einmalig", "von_nr": 2, "nach_nr": 3},
            {"id": "a2", "name": "Anstieg", "von": k, "nach": b, "art": "wiederkehrend"}]})  # fmt: skip
        s.commit()
    seite = client.get(f"/admin/v/{v.id}/strecken/{sid}").text
    assert "Nr. am Anfang" not in seite and 'name="ab_von_nr_' not in seite
    von = re.search(r'<select name="ab_von_1"[^>]*>(.*?)</select>', seite, re.DOTALL).group(1)
    texte = re.findall(r'<option value="([^"]*)"\s*(selected)?>([^<]*)</option>', von)
    assert [(w, t) for w, _, t in texte] == [
        ("1:Start", "Start"),
        ("1:Kreisel", "Kreisel · 1. Durchfahrt (nach Start→Kreisel)"),
        ("1:Brücke", "Brücke · 1. Durchfahrt"),
        ("2:Kreisel", "Kreisel · 2. Durchfahrt (nach Runde 1)"),
        ("2:Brücke", "Brücke · 2. Durchfahrt"),
        ("3:Kreisel", "Kreisel · 3. Durchfahrt (nach Runde 2)"),
        ("1:Ziel", "Ziel"),
    ]
    assert [w for w, sel, _ in texte if sel] == ["2:Kreisel"]
    assert '<option value="3:Kreisel" selected>' in re.search(
        r'<select name="ab_nach_1".*?</select>', seite, re.DOTALL
    ).group(0)
    # „jede Durchfahrt“: nur die Punkte, ohne Nummer
    jede = re.search(r'<select name="ab_von_2"[^>]*>(.*?)</select>', seite, re.DOTALL).group(1)
    assert re.findall(r">([^<]+)</option>", jede) == ["Start", "Kreisel", "Brücke", "Ziel"]
    # so sendet der Editor die Auswahl: „Nr:Punkt“; Ende vor Anfang wird abgewiesen
    daten = {"csrf": csrf_aus(seite), "ab_anzahl": "2", "ab_block_0": "1", "ab_id_0": "b1",
             "ab_vergleich_0": "1", "ab_id_1": "a1", "ab_name_1": "1. Runde", "ab_art_1": "einmalig",
             "ab_von_1": "1:Kreisel", "ab_nach_1": "2:Kreisel"}  # fmt: skip
    assert "Strecke gespeichert" in client.post(f"/admin/v/{v.id}/strecken/{sid}", data=daten).text
    with client.app.state.sessionmaker() as s:
        a = [
            x
            for x in st.abschnitte_von(st.strecken_von(s, s.get(Veranstaltung, v.id))[0])
            if x.id == "a1"
        ]
        assert [(x.von, x.von_nr, x.nach, x.nach_nr) for x in a] == [(k, 1, k, 2)]
    falsch = {**daten, "ab_von_1": "2:Kreisel", "ab_nach_1": "1:Brücke"}
    r = client.post(
        f"/admin/v/{v.id}/strecken/{sid}", data=falsch, headers={"Accept": "application/json"}
    )
    assert r.json()["ok"] is False and "liegt nicht nach" in r.json()["fehler"]
    # alle eigenen und automatischen Abschnitte stehen in der Monitor-Auswahl (Live und Rangliste)
    client.post(f"/admin/v/{v.id}/strecken/{sid}", data={**daten, "ab_anzahl": "3", "ab_id_2": "",
                "ab_name_2": "Anstieg", "ab_art_2": "wiederkehrend", "ab_von_2": "1:Kreisel", "ab_nach_2": "1:Brücke"})  # fmt: skip
    with client.app.state.sessionmaker() as s:
        alle = [x.id for x in st.abschnitte_von(st.strecken_von(s, s.get(Veranstaltung, v.id))[0])]
    assert alle == ["s0.0", "s1.0", "s1.1", "s2.0", "b1", "a1", "a2"]
    html = client.get(f"/admin/v/{v.id}/zielmonitor").text
    assert all(f'name="live_abschnitt" value="{sid}:{a}"' in html for a in alle)
    r = client.post(f"/admin/v/{v.id}/zielmonitor", data={"csrf": csrf_aus(html), "aktion": "neu", "neuer_typ": "rangliste",
                    "seite_an_0": "1", "seite_dauer_0": "10"})  # fmt: skip
    assert all(f'<option value="{sid}:{a}"' in r.text for a in alle)


# --- Gespeicherte Monitor-Einstellungen wirken -----------------------------------


def test_monitor_einstellungen_speichern_wirkt_in_api_und_seite(client, v):
    """Einstellung ändern → speichern → Monitor-API (Vorschau und Live) und gerenderte Seiten zeigen
    den neuen Stand: Seitenfolge, Dauer, Live-Bereich, Abschnittsfelder."""
    anmelden(client)
    with client.app.state.sessionmaker() as s:
        sid = st.strecken_von(s, s.get(Veranstaltung, v.id))[0].id
    url = f"/admin/v/{v.id}/zielmonitor"
    html = client.get(url).text
    # Enter im Formular speichert: erster Absendeknopf ist „Speichern“ (nicht „Auf“ – deaktiviert)
    form = re.search(r'<form[^>]*id="zielmonitor-form".*?</form>', html, re.DOTALL).group(0)
    assert (
        re.search(r"<button[^>]*>", form)
        .group(0)
        .startswith('<button type="submit" name="aktion" value="speichern"')
    )
    zeichen = csrf_aus(html)
    r = client.post(url, data={"csrf": zeichen, "aktion": "neu", "neuer_typ": "rangliste", "seite_an_0": "1",
                               "seite_dauer_0": "10", "seite_live_0": "1"})  # fmt: skip
    daten = {"csrf": zeichen, "seite_an_0": "1", "seite_dauer_0": "8", "seite_an_1": "1", "seite_dauer_1": "20",
             "seite_live_1": "1", "seite_abschnitt_1": f"{sid}:b1", "seite_top_1": "5",
             "live_abschnitt": [f"{sid}:s1.0", f"{sid}:b1"]}  # fmt: skip
    r = client.post(url, data={**daten, "aktion": "auf:1"})
    assert "Zielmonitor gespeichert" in r.text and 'id="zielmonitor-gespeichert"' in r.text
    # gerenderte Admin-Seite zeigt den gespeicherten Stand
    assert re.findall(r'<tr data-typ="(\w+)"', r.text) == ["rangliste", "ergebnis"]
    assert (
        'name="seite_dauer_0" value="20"' in r.text and 'name="seite_dauer_1" value="8"' in r.text
    )
    assert f'<option value="{sid}:b1" selected>' in r.text
    assert f'name="live_abschnitt" value="{sid}:b1" checked' in r.text
    erwartet = (
        [("rangliste", 20, True, "Runde", 5), ("ergebnis", 8, False, None, None)],
        [{"id": "0:s1.0", "strecke": 0, "abschnitt": "s1.0", "vergleich": False},
         {"id": "0:b1", "strecke": 0, "abschnitt": "b1", "vergleich": False}],
    )  # fmt: skip
    for adresse in (
        "/api/oeffentlich",
        f"/api/oeffentlich?veranstaltung={v.id}",
    ):  # Live und Vorschau
        m = client.get(adresse).json()["monitor"]
        seiten = [
            (x["typ"], x["dauer"], x["live"], x.get("titel"), x.get("top")) for x in m["seiten"]
        ]
        assert (seiten, kern(m["live"])) == erwartet
    assert client.get(f"/monitor?veranstaltung={v.id}").status_code == 200
    # Ranglisten-Zeilen gehören nicht zur Konfiguration: der Monitor startet nur bei echter Änderung neu
    assert "konfSchluessel(daten.monitor)" in client.get("/static/js/monitor.js").text
