"""Start- und Zielorte im Admin, Geräte je Ort."""

import re

from app import strecken as st
from app.models import Veranstaltung
from tests.api import test_admin
from tests.api.test_admin import post

admin, v, keine_sperre = test_admin.admin, test_admin.v, test_admin.keine_sperre  # Fixtures


def orte_speichern(admin, v, *neu):
    """Bisherige Orte behalten, neue (Name, Art) anhängen – wie das Formular „Orte“."""
    with admin.app.state.sessionmaker() as s:
        orte = st.orte_von(s, s.get(Veranstaltung, v.id))
    daten = {"mp_anzahl": str(len(orte) + len(neu))}
    for j, m in enumerate(orte):
        daten |= {f"mp_id_{j}": str(m.id), f"mp_name_{j}": m.name}
    for j, (name, art) in enumerate(neu, start=len(orte)):
        daten |= {f"mp_id_{j}": "", f"mp_name_{j}": name, f"mp_art_{j}": art}
    return post(admin, f"/admin/v/{v.id}/messpunkte", **daten)


def ids(admin, v) -> dict[str, int]:
    with admin.app.state.sessionmaker() as s:
        return {m.name: m.id for m in st.orte_von(s, s.get(Veranstaltung, v.id))}


def strecken(admin, v):
    with admin.app.state.sessionmaker() as s:
        return st.strecken_von(s, s.get(Veranstaltung, v.id))


def test_seite_orte_mit_standard_und_drei_arten(admin, v):
    html = admin.get(f"/admin/v/{v.id}/messpunkte").text
    start = re.search(r'<tr [^>]*data-ort="Start"[^>]*>.*?</tr>', html, re.DOTALL).group(0)
    assert (
        ">Start <span" in start
        and "Standard" in start
        and "Gesperrt: genutzt von Strecke 1" in start
    )
    assert 'data-ort="Ziel"' in html and 'id="ort-neu-art"' in html
    r = orte_speichern(admin, v, ("Nord", "start"), ("Kreisel", "punkt"), ("Hof", "ziel"))
    assert "Messpunkte gespeichert" in r.text and "Start „Nord“ nutzt noch keine Strecke" in r.text
    html = admin.get(f"/admin/v/{v.id}/messpunkte").text
    arten = re.findall(r'<tr [^>]*data-art="([a-z]+)" data-ort="([^"]+)"', html)
    assert arten == [("start", "Start"), ("start", "Nord"), ("punkt", "Kreisel"), ("ziel", "Ziel"),
                     ("ziel", "Hof")]  # fmt: skip
    r = orte_speichern(admin, v, ("Start", "punkt"))
    assert "nur für einen Messpunkt der Art „Start“" in r.text


def test_strecke_waehlt_start_und_zielort(admin, v):
    orte_speichern(admin, v, ("Nord", "start"), ("Hof", "ziel"))
    o = ids(admin, v)
    sid = strecken(admin, v)[0].id
    seite = admin.get(f"/admin/v/{v.id}/strecken/{sid}").text
    assert re.search(rf'<option value="{o["Start"]}" selected>Start</option>', seite)
    assert f'<option value="{o["Nord"]}" >Nord</option>' in seite and 'id="s-zielort"' in seite
    r = admin.post(f"/admin/v/{v.id}/strecken/{sid}", headers={"accept": "application/json"},
                   data={"csrf": admin.csrf, "startort": str(o["Nord"]), "zielort": str(o["Hof"])})  # fmt: skip
    assert r.json()["ok"], r.text
    s = strecken(admin, v)[0]
    assert (s.startort_id, s.zielort_id) == (o["Nord"], o["Hof"])
    seite = admin.get(f"/admin/v/{v.id}/strecken/{sid}").text
    assert re.search(rf'<option value="{o["Nord"]}" selected>Nord</option>', seite)
    assert '<li class="pf-fest">Nord</li>' in seite and '<li class="pf-fest">Hof</li>' in seite
    # falsche Art: Fehler am Reiter „Verlauf“
    r = admin.post(f"/admin/v/{v.id}/strecken/{sid}", headers={"accept": "application/json"},
                   data={"csrf": admin.csrf, "startort": str(o["Hof"])})  # fmt: skip
    assert r.json() == {
        "ok": False,
        "fehler": "Unbekannter Messpunkt für „Start“.",
        "reiter": "verlauf",
        "feld": None,
    }
    # Orte in Benutzung: nicht entfernbar, Grund steht dabei
    html = admin.get(f"/admin/v/{v.id}/messpunkte").text
    nord = re.search(r'<tr [^>]*data-ort="Nord"[^>]*>.*?</tr>', html, re.DOTALL).group(0)
    assert "Gesperrt: genutzt von" in nord and "disabled" in nord


# --- Geräte je Ort ---------------------------------------------------------------

import uuid

from app.models import Messpunkt, Strecke, StreckeKlasse

T0 = 1_791_000_000_000


def zwei_orte(admin, v):
    """Zweite Strecke „Nord“ (Startort Nord, Zielort Hof) mit der Klasse von Carl (Nr. 3)."""
    orte_speichern(admin, v, ("Nord", "start"), ("Hof", "ziel"))
    o = ids(admin, v)
    with admin.app.state.sessionmaker() as s:
        from app import korrekturen as k

        w = s.get(Veranstaltung, v.id)
        klasse = next(z.klasse for z in k.ergebnis(s, w).zeilen if z.startnummer == 3)
        a = st.strecken_von(s, w)[0]
        a.klassen = [sk for sk in a.klassen if sk.klasse != klasse]
        s.add(Strecke(veranstaltung_id=v.id, position=1, name="Nord", startort_id=o["Nord"], zielort_id=o["Hof"],
                      klassen=[StreckeKlasse(klasse=klasse)]))  # fmt: skip
        s.commit()
        tokens = {m.name: m for m in st.orte_von(s, w)}
    return {n: (m.token, m.token_rennleitung, m.id) for n, m in tokens.items()}


def hdr(token):
    return {"Authorization": f"Bearer {token}"}


def ev(art, **kw):
    return {"uuid": str(uuid.uuid4()), "art": art, "geraet": "g-" + art.lower(),
            "erstellt_ms": kw.get("zeit_ms") or T0, **kw}  # fmt: skip


def test_startgeraet_je_startort_eigene_fahrer(admin, v):
    t = zwei_orte(admin, v)
    nord = admin.get("/api/stand", headers=hdr(t["Nord"][0])).json()
    assert (
        nord["rolle"] == "start"
        and nord["ort"]["name"] == "Nord"
        and nord["ort"]["strecken"] == [1]
    )
    assert [x["startnummer"] for x in nord["teilnehmer"]] == [3]
    alt = admin.get(
        "/api/stand", headers=hdr(v.token_start)
    ).json()  # bisheriger Link: Standard-Startort
    assert alt["ort"]["name"] == "Start" and [x["startnummer"] for x in alt["teilnehmer"]] == [1, 2]
    r = admin.post("/api/ereignisse", json={"ereignisse": [ev("START", startnummer=3, zeit_ms=T0)]},
                   headers=hdr(t["Nord"][0]) | {"X-Geraet": "handy-nord", "X-Uhr-Abweichung": "120"})  # fmt: skip
    assert r.status_code == 200
    nord = admin.get("/api/stand", headers=hdr(t["Nord"][0])).json()
    assert nord["gestartet"] == [3]
    assert admin.get("/api/stand", headers=hdr(v.token_start)).json()["gestartet"] == []
    protokoll = admin.get("/api/ereignisse", headers=hdr("tok-admin")).json()["ereignisse"]
    assert protokoll[0]["ort_id"] == t["Nord"][2] and protokoll[0]["wert"] is None
    geraete = admin.get(f"/admin/v/{v.id}/geraete").text
    assert "Start Nord" in geraete and "handy-nord" in geraete


def test_zielgeraet_je_zielort_und_rennleitung_getrennt(admin, v):
    t = zwei_orte(admin, v)
    hof = admin.get("/api/stand", headers=hdr(t["Hof"][0])).json()
    assert hof["rolle"] == "ziel" and hof["ort"]["strecken"] == [1]
    assert len(hof["teilnehmer"]) == 3  # Rennleitung auf dem Gerät sieht alle
    assert hof["ziel_orte"] == {str(t["Ziel"][2]): [0], str(t["Hof"][2]): [1]}
    for token in (t["Hof"][0], v.token_ziel):
        assert admin.post("/api/ereignisse", json={"ereignisse": [ev("ZIEL", zeit_ms=T0 + 60_000)]},
                          headers=hdr(token)).status_code == 200  # fmt: skip
    offen = sorted(
        o["ort"] for o in admin.get("/api/stand", headers=hdr(v.token_ziel)).json()["offene_ziele"]
    )
    assert offen == sorted([str(t["Hof"][2]), str(t["Ziel"][2])])
    # „Ziel und Rennleitung getrennt“ nur am Zielort Hof
    r = post(
        admin, f"/admin/v/{v.id}/rennleitung", zusammen="", bestaetigung="1", ort=str(t["Hof"][2])
    )
    assert "getrennt" in r.text
    z = ev("ZIEL", zeit_ms=T0 + 120_000)
    assert (
        admin.post(
            "/api/ereignisse", json={"ereignisse": [z]}, headers=hdr(t["Hof"][0])
        ).status_code
        == 200
    )
    zuordnung = ev("ZUORDNUNG", bezug_uuid=z["uuid"], startnummer=3)
    assert admin.post("/api/ereignisse", json={"ereignisse": [zuordnung]},
                      headers=hdr(t["Hof"][0])).status_code == 422  # fmt: skip
    rl = admin.get("/api/stand", headers=hdr(t["Hof"][1])).json()
    assert rl["rolle"] == "rennleitung" and rl["rennleitung_getrennt"]
    assert admin.post("/api/ereignisse", json={"ereignisse": [zuordnung]},
                      headers=hdr(t["Hof"][1])).status_code == 200  # fmt: skip
    assert not admin.get("/api/stand", headers=hdr(v.token_ziel)).json()["rennleitung_getrennt"]
    geraete = admin.get(f"/admin/v/{v.id}/geraete").text
    assert f"/rennleitung?token={t['Hof'][1]}" in geraete and 'id="erneuern-rennleitung-' in geraete
    geraete = admin.get(f"/admin/v/{v.id}/geraete").text  # Option je Ziel-Karte
    assert 'data-zielort="Hof"' in geraete and 'data-zielort="Ziel"' in geraete


def test_geraete_gruppiert_als_naechstes_und_token(admin, v):
    t = zwei_orte(admin, v)
    html = admin.get(f"/admin/v/{v.id}/geraete").text
    gruppen = re.findall(r'data-gruppe="([^"]+)"', html)
    assert gruppen == [
        "Start",
        "Start Nord",
        "Ziel",
        "Ziel Hof",
        "Öffentlich",
    ]
    assert (
        f"/start?token={t['Nord'][0]}&amp;ort={t['Nord'][2]}" in html  # Eigenes Profil
        and f"/ziel?token={t['Hof'][0]}&amp;ort={t['Hof'][2]}&amp;sicht=rennleitung" in html
    )
    assert "Strecke: Nord" in html  # mehrere Startorte: Karte nennt die Strecken
    uebersicht = admin.get(f"/admin/v/{v.id}").text
    assert (
        "Startgerät Nord noch nicht verbunden" in uebersicht
        and "Zielgerät Hof noch nicht verbunden" in uebersicht
    )
    r = post(admin, f"/admin/v/{v.id}/token", rolle=f"start-{t['Nord'][2]}")
    assert "Start-Token „Nord“ erneuert" in r.text
    assert admin.get("/api/stand", headers=hdr(t["Nord"][0])).status_code == 401
    with admin.app.state.sessionmaker() as s:
        neu = s.get(Messpunkt, t["Nord"][2]).token
    assert admin.get("/api/stand", headers=hdr(neu)).status_code == 200
    # Ort entfernt: Gerät bekommt 410 (Fingerabdruck)
    with admin.app.state.sessionmaker() as s:
        nord = s.scalars(
            __import__("sqlalchemy").select(Strecke).where(Strecke.name == "Nord")
        ).one()
        nord.startort_id = None
        s.commit()
    with admin.app.state.sessionmaker() as s:
        orte = st.orte_von(s, s.get(Veranstaltung, v.id))
    daten = {"mp_anzahl": str(len(orte))}
    for j, m in enumerate(orte):
        daten |= {f"mp_id_{j}": str(m.id), f"mp_name_{j}": m.name}
        if m.name == "Nord":
            daten[f"mp_entfernen_{j}"] = "1"
    assert "Messpunkte gespeichert" in post(admin, f"/admin/v/{v.id}/messpunkte", **daten).text
    assert admin.get("/api/stand", headers=hdr(neu)).status_code == 410


# --- Strecke aus Datei ersetzen ----------------------------------------------------


def test_strecke_aus_datei_ersetzen_oder_neu(admin, v):
    sid = strecken(admin, v)[0].id
    text = admin.get(f"/admin/v/{v.id}/strecken/{sid}/datei").text.replace(
        "messpunkte = []", 'beschreibung = "Neu vermessen"\nmesspunkte = []'
    )
    vorschau = admin.post(f"/admin/v/{v.id}/strecken/laden", data={"csrf": admin.csrf},
                          files={"datei": ("s.toml", text.encode(), "application/toml")}).text  # fmt: skip
    # Nichts vorausgewählt (Pflichtwahl); „Ersetzen“ nennt, was verloren geht
    assert 'id="laden-modus"' in vorschau and "checked" not in re.search(
        r'<fieldset[^>]*id="laden-modus".*?</fieldset>', vorschau, re.DOTALL
    ).group(0)
    assert 'id="modus-ersetzen" required' in vorschau
    assert re.search(r'id="ersetzen-verlust">Start/Ziel von ‚[^‘]+‘ werden ersetzt\.', vorschau)
    assert 'data-bestaetigen-wenn="#modus-ersetzen:checked"' in vorschau
    assert f'name="ersetzen" value="{sid}"' in vorschau
    inhalt = re.search(
        r'<textarea name="inhalt" hidden>(.*?)</textarea>', vorschau, re.DOTALL
    ).group(1)
    import html as h

    inhalt = h.unescape(inhalt)
    r = post(admin, f"/admin/v/{v.id}/strecken/uebernehmen", inhalt=inhalt, ersetzen=str(sid))
    assert "Bitte wählen" in r.text  # ohne Wahl nichts übernommen
    r = post(admin, f"/admin/v/{v.id}/strecken/uebernehmen", inhalt=inhalt, modus="ersetzen",
             ersetzen=str(sid))  # fmt: skip
    assert "Ersetzen bitte bestätigen" in r.text
    assert len(strecken(admin, v)) == 1 and strecken(admin, v)[0].beschreibung != "Neu vermessen"
    r = post(
        admin,
        f"/admin/v/{v.id}/strecken/uebernehmen",
        inhalt=inhalt,
        modus="ersetzen",
        ersetzen=str(sid),
        bestaetigt="1",
    )
    assert "aus der Datei ersetzt – die Klassenzuordnung bleibt" in r.text
    with admin.app.state.sessionmaker() as s:
        liste = st.strecken_von(s, s.get(Veranstaltung, v.id))
        assert len(liste) == 1 and liste[0].beschreibung == "Neu vermessen" and liste[0].klassen
    r = post(
        admin, f"/admin/v/{v.id}/strecken/uebernehmen", inhalt=inhalt, modus="neu", name="Kopie"
    )
    assert (
        "aus der Datei übernommen" in r.text and [s.name for s in strecken(admin, v)][-1] == "Kopie"
    )
    # Assistent: „aus Datei laden“ je Strecke speichert erst den Schritt, dann laden
    assistent = admin.get(f"/admin/einrichtung/{v.id}/2").text
    assert f'name="laden" value="{sid}"' in assistent
    r = admin.post(f"/admin/einrichtung/{v.id}/2", data={"csrf": admin.csrf, "klassensatz_id": str(v.klassensatz_id),
                                                         "laden": str(sid)}, follow_redirects=False)  # fmt: skip
    assert r.headers["location"].startswith(
        f"/admin/v/{v.id}/strecken/laden?zurueck=/admin/einrichtung/{v.id}/2&ersetzen={sid}"
    )
    laden = admin.get(f"/admin/v/{v.id}/strecken/laden?ersetzen={sid}").text
    assert f'name="ersetzen" value="{sid}"' in laden


# --- Strecke mit Klassen entfernen ---------------------------------------------------


def test_strecke_mit_klassen_entfernen_klassen_gehen_an_gewaehlte(admin, v):
    sid = strecken(admin, v)[0].id
    seite = admin.get(f"/admin/v/{v.id}/strecken/{sid}").text  # letzte Strecke: Grund statt Knopf
    assert "Gesperrt: die letzte Strecke einer Veranstaltung bleibt" in seite
    assert re.search(r'id="entfernen-ok" disabled', seite)
    r = post(admin, f"/admin/v/{v.id}/strecken/{sid}/entfernen")
    assert "Die letzte Strecke einer Veranstaltung bleibt" in r.text
    r = admin.post(f"/admin/v/{v.id}/strecken/neu", data={"csrf": admin.csrf})
    neu = int(r.url.path.rsplit("/", 1)[1])
    with admin.app.state.sessionmaker() as s:
        klassen = sorted(
            k.klasse for k in st.strecken_von(s, s.get(Veranstaltung, v.id))[0].klassen
        )
    r = post(
        admin, f"/admin/v/{v.id}/strecken/{sid}/entfernen", klassen_an=str(sid)
    )  # nicht sich selbst
    assert "Bitte wählen" in r.text
    r = post(admin, f"/admin/v/{v.id}/strecken/{sid}/entfernen", klassen_an=str(neu))
    assert "ihre Klassen fährt jetzt „Strecke 2“" in r.text
    with admin.app.state.sessionmaker() as s:
        liste = st.strecken_von(s, s.get(Veranstaltung, v.id))
        assert [x.id for x in liste] == [neu] and sorted(
            k.klasse for k in liste[0].klassen
        ) == klassen


def test_ort_bleibt_bei_umbuchen_und_korrektur(admin, v):
    """Eine Zeit vom Start „Start 5km“ behält ihren Ort beim Umbuchen und bei der
    manuellen Korrektur; Geräte können den Ort nicht über „wert“ vortäuschen."""
    from app import korrekturen as k

    t = zwei_orte(admin, v)
    with admin.app.state.sessionmaker() as s:
        m = s.get(Messpunkt, t["Nord"][2])
        m.name = "Start 5km"
        s.commit()
    start = ev("START", startnummer=1, zeit_ms=T0)
    assert admin.post("/api/ereignisse", json={"ereignisse": [start]},
                      headers=hdr(t["Nord"][0])).status_code == 200  # fmt: skip
    falsch = ev("ZIEL", zeit_ms=T0 + 60_000, wert=str(t["Hof"][2]))  # Standard-Zielgerät mit „wert“
    assert admin.post("/api/ereignisse", json={"ereignisse": [falsch]},
                      headers=hdr(v.token_ziel)).status_code == 200  # fmt: skip
    with admin.app.state.sessionmaker() as s:
        w = s.get(Veranstaltung, v.id)
        assert [o.ort for o in k.ergebnis(s, w).offene_ziele] == [""]  # Standard-Ziel
        k.umbuchen(s, w, start["uuid"], 3, "falsche Nummer")
        erg = k.ergebnis(s, w)
        assert [(x.startnummer, x.ort) for x in erg.startstempel] == [(3, str(t["Nord"][2]))]
        neu = k.manuelle_zeit(s, w, 3, "START", "10:00:05,0", None, "Startzeit korrigiert")
        assert neu.ort_id == t["Nord"][2]
        # ohne bisherigen Stempel: Start der eigenen Strecke (Carl, Nr. 3, fährt „Nord“)
        assert (
            k.manuelle_zeit(s, w, 3, "ZIEL", "10:30:00,0", None, "Zielzeit").ort_id == t["Hof"][2]
        )
        assert k.manuelle_zeit(s, w, 1, "START", "10:00:00,0", None, "Start").ort_id is None


def test_geraetedatei_behaelt_den_ort(admin, v):
    """Die Exportdatei eines Geräts nennt seinen Ort; beim Einlesen bekommen dessen
    Start-/Zielzeiten „ort_id“ (sonst gingen sie an den Standard-Start/-Ziel)."""
    import json

    t = zwei_orte(admin, v)
    start = ev("START", startnummer=3, zeit_ms=T0)
    inhalt = json.dumps({"geraet": "start-ab12", "veranstaltung": {"id": v.id},
                         "ort": {"id": t["Nord"][2], "name": "Nord", "art": "start"},
                         "eintraege": [start]})  # fmt: skip
    post(admin, f"/admin/v/{v.id}/geraetedatei/uebernehmen", inhalt=inhalt)
    protokoll = admin.get("/api/ereignisse", headers=hdr("tok-admin")).json()["ereignisse"]
    assert [(e["uuid"], e["ort_id"]) for e in protokoll] == [(start["uuid"], t["Nord"][2])]


def test_standard_start_entfernbar_wenn_ungenutzt(admin, v):
    """Beide Strecken nutzen eigene Startorte → „Start“ ist nicht mehr gesperrt und
    lässt sich entfernen; danach erscheint er weder unter „Messpunkte“ noch unter „Geräte“ noch in
    der Auswahl der Strecke, der bisherige Start-Link bleibt gültig."""
    from app.models import Strecke

    orte_speichern(admin, v, ("Start 5k", "start"), ("Start 20k", "start"))
    o = ids(admin, v)
    with admin.app.state.sessionmaker() as s:
        w = s.get(Veranstaltung, v.id)
        if len(st.strecken_von(s, w)) < 2:
            s.add(Strecke(veranstaltung_id=w.id, position=1, name="20 km"))
            s.flush()
        a, b = st.strecken_von(s, w)[:2]
        st.strecke_orte_setzen(s, w, a, str(o["Start 5k"]), None)
        st.strecke_orte_setzen(s, w, b, str(o["Start 20k"]), None)
        for x in st.strecken_von(s, w)[2:]:
            st.strecke_orte_setzen(s, w, x, str(o["Start 20k"]), None)
        s.commit()
        sid = a.id
    html = admin.get(f"/admin/v/{v.id}/messpunkte").text
    start = re.search(r'<tr [^>]*data-ort="Start"[^>]*>.*?</tr>', html, re.DOTALL).group(0)
    assert "Gesperrt" not in start and "disabled" not in start
    daten = {
        "mp_anzahl": "1",
        "mp_id_0": str(o["Start"]),
        "mp_name_0": "Start",
        "mp_entfernen_0": "1",
    }
    r = post(admin, f"/admin/v/{v.id}/messpunkte", **daten)
    assert "Messpunkte gespeichert" in r.text
    html = admin.get(f"/admin/v/{v.id}/messpunkte").text
    assert (
        'data-ort="Start"' not in html
        and 'data-ort="Start 5k"' in html
        and 'data-ort="Ziel"' in html
    )
    geraete = admin.get(f"/admin/v/{v.id}/geraete").text
    assert f"/start?token={v.token_start}" not in geraete and "&amp;ort=" in geraete
    seite = admin.get(f"/admin/v/{v.id}/strecken/{sid}").text
    assert f'<option value="{o["Start"]}"' not in seite
    assert re.search(rf'<option value="{o["Start 5k"]}" selected>Start 5k</option>', seite)
    # bisheriger Link des Standard-Starts: weiter gültig (Migration und alte Links unverändert)
    r = admin.get("/api/stand", headers={"Authorization": f"Bearer {v.token_start}"})
    assert r.status_code == 200
    # „Ziel“ wird noch genutzt: gesperrt mit Grund
    ziel = re.search(r'<tr [^>]*data-ort="Ziel"[^>]*>.*?</tr>', html, re.DOTALL).group(0)
    assert "Gesperrt: genutzt von" in ziel
