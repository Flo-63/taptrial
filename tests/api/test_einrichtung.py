"""Admin: geführte Einrichtung, neue Reiterstruktur, Kopplung Klassen-/Wertungssatz."""

import pytest
from sqlalchemy import select

from app import saetze as sz
from app import strecken
from app.models import Klassensatz, Veranstaltung, Wertungssatz
from tests.api.test_admin_saetze import KF, csrf, post, satz, tabelle
from tests.conftest import csv_importieren

CSV = b"startnummer;vorname;nachname;geschlecht;geburtsjahr\n1;Anna;A;w;1990\n2;Ben;B;m;1980\n"


@pytest.fixture
def admin(client):
    client.post("/admin/anmelden", data={"token": "tok-admin"})
    client.csrf = csrf(client.get("/admin").text)
    return client


def platz(admin, v, ks, ws, schluessel, name, laenge=""):
    """„Platzhalter-Strecke anlegen“ für einen Längen-Platz im Assistenten."""
    return post(admin, f"/admin/einrichtung/{v.id}/2", klassensatz_id=str(ks), wertungssatz_id=str(ws or ""),
                platz_anlegen=schluessel, **{f"platz_name:{schluessel}": name, f"platz_laenge:{schluessel}": laenge})  # fmt: skip


def namen_laengen(admin, v):
    with admin.app.state.sessionmaker() as s:
        return [
            (x.name, x.laenge_km, len(x.klassen))
            for x in strecken.strecken_von(s, s.get(Veranstaltung, v.id))
        ]


def veranstaltung(admin) -> Veranstaltung:
    with admin.app.state.sessionmaker() as s:
        return s.scalars(select(Veranstaltung).order_by(Veranstaltung.id.desc())).first()


def test_assistent_komplett(admin):
    assert 'id="neue-veranstaltung"' in admin.get("/admin").text
    html = admin.get("/admin/einrichtung/neu").text
    assert 'id="fortschritt"' in html and "1. Name und Datum" in html
    assert "Datum ungültig" in post(admin, "/admin/einrichtung/neu", name="X", datum="kaputt").text

    r = post(admin, "/admin/einrichtung/neu", name="Kottenforst", datum="2026-10-10")
    assert "angelegt (noch nicht aktiv)" in r.text and "2. Klassen &amp; Wertung" in r.text
    v = veranstaltung(admin)
    assert v.einrichtung_schritt == 2 and not v.aktiv
    # Zwischenstand: Übersicht bietet Fortsetzen an
    assert f"/admin/einrichtung/{v.id}/2" in admin.get("/admin").text

    # Schritt 2: Wertung ohne Strecke (pro_km): ein Platz für alle Klassen; ohne Länge → Fehler
    ks, ws = satz(admin, Klassensatz, "DRA 2026"), satz(admin, Wertungssatz, "DRA A.1 flach")
    a2 = satz(admin, Wertungssatz, "DRA A.2 Berg Straße")
    r = post(admin, f"/admin/einrichtung/{v.id}/2", klassensatz_id=str(ks), wertungssatz_id=str(a2))
    assert 'data-platz="Strecke 1"' in r.text and "Strecke für alle Klassen" in r.text
    platz(admin, v, ks, a2, "Strecke 1", "Strecke 1")
    r = post(admin, f"/admin/einrichtung/{v.id}/2", klassensatz_id=str(ks), wertungssatz_id=str(a2))
    assert "Streckenlänge Pflicht" in r.text and veranstaltung(admin).einrichtung_schritt == 2
    # A.1 legt keine Strecken an; die Plätze zeigen die tatsächliche Zuordnung –
    # hier noch alles an der gewählten „Strecke 1“, je Platz „Ändern“
    r = post(admin, f"/admin/einrichtung/{v.id}/2", klassensatz_id=str(ks), wertungssatz_id=str(ws))
    assert 'data-platz="5 km"' in r.text and 'data-platz="20 km"' in r.text
    assert "Strecke für 5 km" in r.text and "Klassen Jungen 8-11, Jungen 12-14" in r.text
    assert r.text.count('class="platz-aendern"') == 2 and [
        n for n, _, _ in namen_laengen(admin, v)
    ] == ["Strecke 1"]
    r = platz(admin, v, ks, ws, "5 km", "Jugend", "5,1")
    assert (
        "Platzhalter-Strecke „Jugend“ angelegt" in r.text and "Strecke „Strecke 1“ bleibt" in r.text
    )
    r = platz(admin, v, ks, ws, "20 km", "20 km", "20,3")
    assert 'id="strecken-tabelle"' in r.text and 'id="weiter" disabled' not in r.text
    assert namen_laengen(admin, v) == [("Jugend", 5.1, 6), ("20 km", 20.3, 15)]  # „Strecke 1“ weg
    r = post(admin, f"/admin/einrichtung/{v.id}/2", klassensatz_id=str(ks), wertungssatz_id=str(ws))
    assert "3. Teilnehmer" in r.text and 'id="anzahl-teilnehmer">0<' in r.text
    assert veranstaltung(admin).einrichtung_schritt == 3

    # Zurück zu Schritt 1 behält den Stand und ändert den Namen
    assert 'value="Kottenforst"' in admin.get(f"/admin/einrichtung/{v.id}/1").text
    r = post(admin, f"/admin/einrichtung/{v.id}/1", name="Kottenforst 2026", datum="2026-10-11")
    assert "2. Klassen" in r.text and veranstaltung(admin).einrichtung_schritt == 3

    # Schritt 3: Import mit Vorschau kehrt in den Assistenten zurück; manuell anlegen ebenso
    vorschau = admin.post(f"/admin/v/{v.id}/import", data={"csrf": admin.csrf, "assistent": "1"},
                          files={"datei": ("t.csv", CSV)})  # fmt: skip
    assert 'name="assistent" value="1"' in vorschau.text
    r = post(admin, f"/admin/v/{v.id}/import/uebernehmen", modus="ergaenzen", assistent="1")
    assert "2 Teilnehmer importiert" in r.text and 'id="anzahl-teilnehmer">2<' in r.text
    r = post(admin, f"/admin/v/{v.id}/teilnehmer", tid="", startnummer="3", vorname="Cara",
             nachname="C", geschlecht="w", geburtsjahr="1975", verein="",
             ziel=f"/admin/einrichtung/{v.id}/3")  # fmt: skip
    assert 'id="anzahl-teilnehmer">3<' in r.text

    r = post(admin, f"/admin/einrichtung/{v.id}/3")
    assert 'id="zielmonitor"' in r.text and 'id="monitor-vorschau"' in r.text
    r = post(admin, f"/admin/v/{v.id}/zielmonitor", weiter="1", seite_an_0="1", seite_dauer_0="10",
             seite_live_0="1", ziel=f"/admin/einrichtung/{v.id}/4")  # fmt: skip
    assert 'id="zusammenfassung"' in r.text and "Kottenforst 2026" in r.text
    assert "DRA A.1 flach" in r.text and "20,3 km" in r.text and "Teilnehmer: 3" in r.text
    assert ">Jugend</span> 5,1 km" in r.text
    assert f"/start?token={v.token_start}" in r.text and "<svg" in r.text

    r = post(admin, f"/admin/einrichtung/{v.id}/5", aktivieren="1")
    assert "Einrichtung abgeschlossen" in r.text and "aktive Veranstaltung" in r.text
    v = veranstaltung(admin)
    assert v.aktiv and v.einrichtung_schritt is None
    assert "/admin/einrichtung/" + str(v.id) not in admin.get("/admin").text


def test_assistent_ohne_aktivieren_und_ohne_teilnehmer(admin):
    post(admin, "/admin/einrichtung/neu", name="Probe", datum="2026-10-10")
    v = veranstaltung(admin)
    ks = satz(admin, Klassensatz, "DRA 2026")
    post(
        admin,
        f"/admin/einrichtung/{v.id}/2",
        klassensatz_id=str(ks),
        wertungssatz_id="",
        strecke_km="",
    )
    platz(admin, v, ks, None, "Strecke 1", "Strecke 1")
    post(
        admin,
        f"/admin/einrichtung/{v.id}/2",
        klassensatz_id=str(ks),
        wertungssatz_id="",
        strecke_km="",
    )
    r = post(admin, f"/admin/einrichtung/{v.id}/3")
    assert "Noch keine Teilnehmer" in r.text
    post(admin, f"/admin/v/{v.id}/zielmonitor", weiter="1", seite_an_0="1", seite_dauer_0="10",
         ziel=f"/admin/einrichtung/{v.id}/4")  # fmt: skip
    r = post(admin, f"/admin/einrichtung/{v.id}/5")
    assert "eingerichtet (nicht aktiv)" in r.text and not veranstaltung(admin).aktiv
    assert admin.get(f"/admin/einrichtung/{v.id}/9").status_code == 404


@pytest.fixture
def v(admin):
    post(admin, "/admin/veranstaltungen", name="Kottenforst", datum="2026-10-10", aktivieren="1")
    return veranstaltung(admin)


def test_rahmen_mit_seitenleiste(admin, v):
    """Kopfzeile, Seitenleiste mit Gruppen, alle Punkte immer sichtbar."""
    html = admin.get(f"/admin/v/{v.id}").text
    for gruppe in ("Veranstaltung", "Zeitnahme", "Auswertung", "Verwaltung"):
        assert f'<div class="nav-group">{gruppe}</div>' in html, gruppe
    for seite, text in (("einstellungen", "Einstellungen"), ("teilnehmer", "Teilnehmer"),
                        ("geraete", "Geräte"), ("aufgaben", "Aufgaben"), ("protokoll", "Protokoll"),
                        ("ergebnis", "Ergebnis"), ("drucken", "Drucken &amp; Export"),
                        ("status", "Status &amp; Datenschutz")):  # fmt: skip
        assert f'href="/admin/v/{v.id}/{seite}" data-seite="{seite}"' in html, seite
        assert f"<span>{text}</span>" in html, text
        seite_html = admin.get(f"/admin/v/{v.id}/{seite}").text
        assert 'aria-current="page"' in seite_html and "<h1>" in seite_html, seite
    for knopf in ("nav-veranstaltungen", "nav-vorlagen", "nav-hilfe", "menue-knopf"):
        assert f'id="{knopf}"' in html, knopf
    aufgaben = admin.get(f"/admin/v/{v.id}/aufgaben").text
    for teil in ('id="konflikte"', 'id="sonderfaelle"', 'id="tauschen"', 'id="geraetedatei-link"'):
        assert teil in aufgaben, teil
    assert "offiziell" in admin.get(f"/admin/v/{v.id}/ergebnis").text
    drucken = admin.get(f"/admin/v/{v.id}/drucken").text
    assert "ergebnis.csv" in drucken and "protokoll.csv" in drucken
    status = admin.get(f"/admin/v/{v.id}/status").text.lower()
    for teil in ("abschließen", "löschen"):
        assert teil in status, teil
    # alte Adressen leiten weiter
    for alt, neu in (("korrekturen", "aufgaben"), ("rennen", "aufgaben"),
                     ("stammdaten", "einstellungen"), ("klassen", "einstellungen/klassen")):  # fmt: skip
        r = admin.get(f"/admin/v/{v.id}/{alt}", follow_redirects=False)
        assert r.status_code in (302, 303, 307) and r.headers["location"].endswith(neu)


def test_wertung_folgt_klassensatz(admin, v):
    ks = satz(admin, Klassensatz, "DRA 2026")
    ws = satz(admin, Wertungssatz, "DRA A.1 flach")
    post(admin, f"/admin/v/{v.id}/regeln", klassensatz_id=str(ks), wertungssatz_id=str(ws),
         strecke_km="20,0")  # fmt: skip
    html = admin.get(f"/admin/v/{v.id}/klassen").text
    assert f'data-klassensatz="{ks}"' in html and 'id="wertung-zurueckgesetzt"' in html
    # anderer Klassensatz → unpassende Wertung wird mit Hinweis zurückgesetzt
    post(admin, "/admin/saetze/klassen/neu", name="Offen",
         **tabelle([("Männer", "m", 0, 99), ("Frauen", "w", 0, 99)], KF))  # fmt: skip
    neu = satz(admin, Klassensatz, "Offen")
    r = post(admin, f"/admin/v/{v.id}/regeln", klassensatz_id=str(neu), wertungssatz_id=str(ws),
             strecke_km="20,0")  # fmt: skip
    assert "zurückgesetzt" in r.text and 'id="warnung"' in r.text
    with admin.app.state.sessionmaker() as s:
        x = s.get(Veranstaltung, v.id)
        assert x.klassensatz_id == neu and x.wertungssatz_id is None
        assert sz.regeln(s, x).wertung is None
    # Import nutzt danach den neuen Klassensatz
    _, r = csv_importieren(admin, v.id, CSV, admin.csrf)
    assert "2 Teilnehmer importiert" in r.text
    html = admin.get(f"/admin/v/{v.id}/teilnehmer").text
    assert "Männer" in html and "Frauen" in html


def test_assistent_fragt_strecken_einfach_ab(admin, v):
    """Schritt 2 fragt Strecken einfach ab (Name, Länge, Klassen); Segmente danach auf der Strecken-Seite."""
    html = admin.get(f"/admin/einrichtung/{v.id}/2").text
    assert 'id="strecken-tabelle"' in html and 'id="zuordnung"' in html
    assert 'id="segmente-hinweis"' in html and "Seite der Strecke" in html
    for alt in ('id="teile"', 'id="abschnitte"', 'id="messpunkte"', "tl_anzahl", "ab_anzahl"):
        assert alt not in html, alt


def test_assistent_strecke_mit_messpunkten_und_zurueck(admin):
    """Je Strecke „einfach“ oder „mit Messpunkten“; „Messpunkte einrichten“ speichert den Schritt,
    öffnet die Strecken-Seite und führt von dort zurück in den Assistenten."""
    from app import segmente as sg

    post(admin, "/admin/einrichtung/neu", name="Mit Posten", datum="2026-10-11")
    v = veranstaltung(admin)
    html = platz(admin, v, v.klassensatz_id, None, "Strecke 1", "Strecke 1").text
    assert 'name="strecke_art_0" value="einfach" checked' in html and 'id="strecke-laden"' in html
    assert 'name="laden" value="neu"' in html  # Speichert erst den Schritt
    with admin.app.state.sessionmaker() as s:
        sid = strecken.strecken_von(s, s.get(Veranstaltung, v.id))[0].id
    daten = {"klassensatz_id": str(v.klassensatz_id), "wertungssatz_id": "", "strecken_anzahl": "1",
             "strecke_id_0": str(sid), "strecke_name_0": "Rund", "strecke_laenge_0": "12,0",
             "strecke_art_0": "messpunkte", "zuordnung_anzahl": "0", "einrichten": str(sid)}  # fmt: skip
    r = post(admin, f"/admin/einrichtung/{v.id}/2", **daten)
    assert r.url.path == f"/admin/v/{v.id}/strecken/{sid}" and "Schritt 2 gespeichert" in r.text
    assert "Zurück zum Assistenten" in r.text and f'href="/admin/einrichtung/{v.id}/2"' in r.text
    with admin.app.state.sessionmaker() as s:
        x = s.get(Veranstaltung, v.id)
        assert (
            x.einrichtung_schritt == 2 and strecken.strecken_von(s, x)[0].name == "Rund"
        )  # Fortschritt erhalten
    # Messpunkt anlegen und Streckenverlauf speichern – der Rückweg bleibt erhalten
    post(admin, f"/admin/v/{v.id}/messpunkte", mp_anzahl="1", mp_id_0="", mp_name_0="Kreisel")
    r = post(admin, f"/admin/v/{v.id}/strecken/{sid}", zurueck=f"/admin/einrichtung/{v.id}/2", tl_anzahl="2",
             tl_von_0="Start", tl_nach_0="Kreisel", tl_von_1="Kreisel", tl_nach_1="Ziel")  # fmt: skip
    assert "Strecke gespeichert" in r.text and "Zurück zum Assistenten" in r.text
    html = admin.get(f"/admin/einrichtung/{v.id}/2").text
    assert 'name="strecke_art_0" value="messpunkte" checked' in html
    assert "data-mit-segmenten=1" in html and 'name="strecke_art_bestaetigt_0"' in html
    # „einfach“ ohne Bestätigung: Verlauf bleibt, Hinweis
    r = post(
        admin,
        f"/admin/einrichtung/{v.id}/2",
        **{**daten, "strecke_art_0": "einfach", "einrichten": ""},
    )
    assert "nicht bestätigt" in r.text
    with admin.app.state.sessionmaker() as s:
        assert sg.aus_json(strecken.strecken_von(s, s.get(Veranstaltung, v.id))[0].teile_json) != ()
    # bestätigt: Segmente dieser Strecke werden entfernt, keine Änderung sonst
    r = post(
        admin,
        f"/admin/einrichtung/{v.id}/2",
        **{**daten, "strecke_art_0": "einfach", "strecke_art_bestaetigt_0": "1", "einrichten": ""},
    )
    assert r.url.path == f"/admin/einrichtung/{v.id}/3" and "Segmente entfernt" in r.text
    with admin.app.state.sessionmaker() as s:
        assert sg.aus_json(strecken.strecken_von(s, s.get(Veranstaltung, v.id))[0].teile_json) == ()


def test_assistent_einfach_wie_bisher(admin):
    post(admin, "/admin/einrichtung/neu", name="Einfach", datum="2026-10-11")
    v = veranstaltung(admin)
    platz(admin, v, v.klassensatz_id, None, "Strecke 1", "Strecke 1")
    with admin.app.state.sessionmaker() as s:
        sid = strecken.strecken_von(s, s.get(Veranstaltung, v.id))[0].id
    r = post(admin, f"/admin/einrichtung/{v.id}/2", klassensatz_id=str(v.klassensatz_id), wertungssatz_id="",
             strecken_anzahl="1", strecke_id_0=str(sid), strecke_name_0="Strecke 1", strecke_laenge_0="",
             strecke_art_0="einfach", zuordnung_anzahl="0")  # fmt: skip
    assert r.url.path == f"/admin/einrichtung/{v.id}/3" and "Segmente entfernt" not in r.text


def test_assistent_schritt_zielmonitor_mit_vorgaben(admin):
    """Schritt 4 „Zielmonitor“ vor der Zusammenfassung – gleiche Bausteine wie der
    Reiter, Vorgaben nur bestätigen: Ergebnisseite an, Live an, je sichtbarem Abschnitt ein Feld,
    „mit Pfeil und Differenz“ nur bei Abschnitten mit mehreren Durchfahrten."""
    import json
    import re

    from app import segmente as sg
    from app.models import Messpunkt
    from app.simulation import beispiel_teile

    post(admin, "/admin/einrichtung/neu", name="Rund", datum="2026-10-13")
    v = veranstaltung(admin)
    ks = satz(admin, Klassensatz, "DRA 2026")
    platz(admin, v, ks, None, "Strecke 1", "Strecke 1")
    post(
        admin,
        f"/admin/einrichtung/{v.id}/2",
        klassensatz_id=str(ks),
        wertungssatz_id="",
        strecke_km="",
    )
    with admin.app.state.sessionmaker() as s:
        w = s.get(Veranstaltung, v.id)
        mps = [
            Messpunkt(veranstaltung_id=w.id, position=i, name=n) for i, n in enumerate(("P1", "P2"))
        ]
        s.add_all(mps)
        s.flush()
        strecke = strecken.strecken_von(s, w)[0]
        strecke.teile_json = sg.nach_json(
            beispiel_teile({"P1": str(mps[0].id), "P2": str(mps[1].id)})
        )
        strecke.abschnitte_json = json.dumps({"eigene": [{"id": "a1", "name": "Anfahrt", "von": "START",
                                                          "nach": str(mps[1].id), "art": "einmalig"}]})  # fmt: skip
        s.commit()
        sid = strecke.id
        alle = [f"{sid}:{a.id}" for a in strecken.abschnitte_von(strecke)]
    r = post(admin, f"/admin/einrichtung/{v.id}/3")
    assert r.url.path == f"/admin/einrichtung/{v.id}/4"
    html = r.text
    assert (
        re.search(r'<li class="aktuell"[^>]*>\s*4\. Zielmonitor', html) or "4. Zielmonitor" in html
    )
    assert 'id="zielmonitor-form"' in html and 'id="monitor-live"' in html  # Bausteine des Reiters
    assert f'href="/monitor?veranstaltung={v.id}"' in html  # Vorschau
    assert f'href="/admin/einrichtung/{v.id}/3" id="zurueck"' in html and 'id="weiter"' in html
    assert (
        'name="seite_an_0" value="1" checked' in html
        and 'name="seite_live_0" value="1" checked' in html
    )
    gewaehlt = re.findall(r'name="live_abschnitt" value="([^"]+)" checked', html)
    pfeile = re.findall(r'name="live_vergleich" value="([^"]+)" checked', html)
    assert gewaehlt == alle  # Segmente, Rundenblock, eigener Abschnitt
    # mehrere Durchfahrten: Segmente im Rundenblock und der Rundenblock; einmalige ohne Pfeil
    assert pfeile == [f"{sid}:s1.0", f"{sid}:s1.1", f"{sid}:b1"]
    with admin.app.state.sessionmaker() as s:
        assert s.get(Veranstaltung, v.id).monitor_json is None  # noch nichts gespeichert
    # nur bestätigen: „Weiter“ speichert die Vorgabe und führt zur Zusammenfassung
    daten = {"weiter": "1", "seite_an_0": "1", "seite_dauer_0": "10", "seite_live_0": "1",
             "live_abschnitt": gewaehlt, "live_vergleich": pfeile, "ziel": f"/admin/einrichtung/{v.id}/4"}  # fmt: skip
    r = post(admin, f"/admin/v/{v.id}/zielmonitor", **daten)
    assert r.url.path == f"/admin/einrichtung/{v.id}/5" and 'id="zusammenfassung"' in r.text
    with admin.app.state.sessionmaker() as s:
        w = s.get(Veranstaltung, v.id)
        konf = json.loads(w.monitor_json)
        assert w.einrichtung_schritt == 5
    assert konf["live_abschnitte"] == alle and konf["live_vergleich"] == pfeile
    assert konf["seiten"][0]["typ"] == "ergebnis" and konf["seiten"][0]["live"]
    # Zurück zu Schritt 4 zeigt den gespeicherten Stand (nicht erneut die Vorgabe)
    post(admin, f"/admin/v/{v.id}/zielmonitor", seite_an_0="1", seite_dauer_0="10", seite_live_0="1",
         live_abschnitt=[alle[0]], ziel=f"/admin/einrichtung/{v.id}/4")  # fmt: skip
    html = admin.get(f"/admin/einrichtung/{v.id}/4").text
    assert re.findall(r'name="live_abschnitt" value="([^"]+)" checked', html) == [alle[0]]
    # Abschluss wie bisher
    r = post(admin, f"/admin/einrichtung/{v.id}/5")
    assert "eingerichtet (nicht aktiv)" in r.text
