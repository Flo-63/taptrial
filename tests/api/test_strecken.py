"""Mehrere Strecken: Admin, Geräte-API, öffentliche Listen, CSV, Druck."""

import re
from datetime import date

import pytest

from app import saetze
from app import strecken as st
from app.models import Teilnehmer, Veranstaltung, Wertungssatz
from tests.api.test_oeffentlich import MIN, T0, fahrt, senden
from tests.conftest import csv_importieren

# Nr, Geschlecht, Jahrgang: 1–3 Jugend (5 km), 4–7 Erwachsene (20 km)
FAHRER = [(1, "m", 2013), (2, "w", 2012), (3, "m", 2011), (4, "w", 1990), (5, "m", 1985),
          (6, "m", 1987), (7, "m", 1988)]  # fmt: skip


@pytest.fixture
def v(client):
    with client.app.state.sessionmaker() as s:
        saetze.standardsaetze_anlegen(s)
        v = Veranstaltung(name="Kottenforst", datum=date(2026, 10, 10), aktiv=True,
                          token_start="tok-start", token_ziel="tok-ziel",
                          klassensatz_id=saetze.standard_klassensatz(s).id)  # fmt: skip
        s.add(v)
        s.flush()
        for nr, g, j in FAHRER:
            s.add(Teilnehmer(veranstaltung_id=v.id, startnummer=nr, vorname=f"Vor{nr}",
                             nachname=f"Nach{nr}", geschlecht=g, geburtsjahr=j, abzeichen=True))  # fmt: skip
        s.commit()
        a1 = saetze.nach_name(s, Wertungssatz, "DRA A.1 flach")
        # Strecken wie der Vorschlag aus A.1 (der Satzwechsel legt sie nicht mehr an)
        st.anlegen(s, v, st.vorschlag(saetze.regeln(s, v).klassen, saetze.wertung_von(a1)))
        saetze.regeln_setzen(s, v, v.klassensatz_id, a1.id, None)
        return v


@pytest.fixture
def admin(client):
    client.post("/admin/anmelden", data={"token": "tok-admin"})
    client.csrf = re.search(r'name="csrf" value="([0-9a-f]+)"', client.get("/admin").text).group(1)
    return client


def rennen(client):
    fahrt(client, 1, 11)
    fahrt(client, 2, 12)
    fahrt(client, 3, 10)
    fahrt(client, 4, 36)
    fahrt(client, 5, 34)
    fahrt(client, 6, 38)
    senden(client, {"art": "START", "startnummer": 7, "zeit_ms": T0 + MIN}, rolle="start")


def test_strecken_liste_und_seite_je_strecke(admin, v):
    """Seite „Strecken“ mit Kurzinfo und „Öffnen“; Seite je Strecke speichert ihre Grunddaten."""
    html = admin.get(f"/admin/v/{v.id}/strecken").text
    assert 'id="messpunkte-karte"' not in html and 'id="strecken-liste"' in html
    assert 'data-strecke="5 km"' in html and "20,0 km" in html and "Start · Ziel" in html
    with admin.app.state.sessionmaker() as s:
        ids = [x.id for x in st.strecken_von(s, s.get(Veranstaltung, v.id))]
    assert f'href="/admin/v/{v.id}/strecken/{ids[0]}"' in html
    seite = admin.get(f"/admin/v/{v.id}/strecken/{ids[0]}").text
    for teil in (
        "grunddaten",
        "streckenverlauf",
        "auswertungsabschnitte",
        "strecke-entfernen",
        "zurueck-strecken",
    ):
        assert f'id="{teil}"' in seite, teil
    assert 'name="klasse"' not in seite and '#strecke"' in seite  # Zuordnung zentral
    # Grunddaten: Name und Länge
    r = admin.post(f"/admin/v/{v.id}/strecken/{ids[0]}", data={
        "csrf": admin.csrf, "name": "Jugend", "laenge": "5,2"})  # fmt: skip
    assert "Strecke gespeichert" in r.text and r.url.path == f"/admin/v/{v.id}/strecken/{ids[0]}"
    with admin.app.state.sessionmaker() as s:
        x = st.strecken_von(s, s.get(Veranstaltung, v.id))[0]
        assert (x.name, x.laenge_km) == ("Jugend", 5.2) and "Jungen 12-14" in {
            k.klasse for k in x.klassen
        }
    # Klassenzuordnung: eine Tabelle für alle Strecken, ein Speichern
    html = admin.get(f"/admin/v/{v.id}/strecken").text
    assert (
        'id="klassenzuordnung"' in html and f'action="/admin/v/{v.id}/strecken/zuordnung"' in html
    )
    assert re.search(rf'name="klasse:Damen 31-40"[^>]*>\s*<option value="{ids[0]}" >', html)
    r = admin.post(f"/admin/v/{v.id}/strecken/zuordnung", data={
        "csrf": admin.csrf, "klasse:Damen 31-40": str(ids[0])})  # fmt: skip
    assert "Klassenzuordnung gespeichert" in r.text and r.url.path == f"/admin/v/{v.id}/strecken"
    with admin.app.state.sessionmaker() as s:
        kurz, lang = st.strecken_von(s, s.get(Veranstaltung, v.id))
        assert "Damen 31-40" in {k.klasse for k in kurz.klassen}
        assert "Herren 31-40" in {k.klasse for k in lang.klassen}  # nicht übermittelt: bleibt
    # Strecke mit Klassen: nur mit ausdrücklich gewählter Strecke für die Klassen
    seite = admin.get(f"/admin/v/{v.id}/strecken/{ids[0]}").text
    assert 'id="entfernen-dialog"' in seite and "Die Klassen von ‚Jugend‘ übernimmt:" in seite
    r = admin.post(f"/admin/v/{v.id}/strecken/{ids[0]}/entfernen", data={"csrf": admin.csrf})
    assert "Bitte wählen, welche Strecke die Klassen von „Jugend“ übernimmt" in r.text
    # Strecke hinzufügen → eigene Seite; ohne Klasse ein Hinweis; wieder entfernen
    r = admin.post(f"/admin/v/{v.id}/strecken/neu", data={"csrf": admin.csrf})
    assert "Strecke „Strecke 3“ angelegt" in r.text and r.url.path.startswith(
        f"/admin/v/{v.id}/strecken/"
    )
    neu = int(r.url.path.rsplit("/", 1)[1])
    r = admin.post(f"/admin/v/{v.id}/strecken/{neu}/entfernen", data={"csrf": admin.csrf})
    assert "entfernt" in r.text and r.url.path.endswith("/strecken")
    with admin.app.state.sessionmaker() as s:
        assert len(st.strecken_von(s, s.get(Veranstaltung, v.id))) == 2
    # nach der ersten Zeit: gesperrt mit Grund
    fahrt(admin, 1, 11)
    html = admin.get(f"/admin/v/{v.id}/strecken").text
    assert 'id="strecken-eingefroren"' in html and re.search(r'id="strecke-neu" disabled', html)
    seite = admin.get(f"/admin/v/{v.id}/strecken/{ids[0]}").text
    assert 'id="strecken-eingefroren"' in seite and "Gesperrt: es gibt bereits Zeiten" in seite
    assert re.search(r'name="klasse:Jungen 12-14"[^>]*disabled', html)
    r = admin.post(f"/admin/v/{v.id}/strecken/{ids[0]}/entfernen", data={"csrf": admin.csrf})
    assert "eingefroren" in r.text
    assert admin.get(f"/admin/v/{v.id}/strecken/99999").status_code == 404


def test_regeln_formular_mit_allen_strecken_weiter_moeglich(admin, v):
    """Der Assistent speichert Strecken weiter als Tabelle über /regeln."""
    with admin.app.state.sessionmaker() as s:
        ids = [x.id for x in st.strecken_von(s, s.get(Veranstaltung, v.id))]
    daten = {"csrf": admin.csrf, "klassensatz_id": str(v.klassensatz_id),
             "wertungssatz_id": str(saetze_id(admin)), "strecken_anzahl": "3",
             "strecke_id_0": str(ids[0]), "strecke_name_0": "Jugend", "strecke_laenge_0": "5,2",
             "strecke_id_1": str(ids[1]), "strecke_name_1": "Lang", "strecke_laenge_1": "20,1",
             "strecke_id_2": "", "strecke_name_2": "Extra", "strecke_laenge_2": "", "zuordnung_anzahl": "0"}  # fmt: skip
    r = admin.post(f"/admin/v/{v.id}/regeln", data=daten)
    assert "gespeichert" in r.text and "Strecke „Extra“ hat keine Klasse" in r.text
    daten.update({"strecke_entfernen_0": "1", "strecke_id_2": ""})
    r = admin.post(f"/admin/v/{v.id}/regeln", data=daten)
    assert "hat Teilnehmer, aber keine Strecke" in r.text


def saetze_id(client, name="DRA A.1 flach"):
    with client.app.state.sessionmaker() as s:
        return saetze.nach_name(s, Wertungssatz, name).id


def test_stand_fuer_geraete(client, v):
    rennen(client)
    stand = client.get("/api/stand", headers={"Authorization": "Bearer tok-start"}).json()
    assert [(x["name"], x["laenge_km"]) for x in stand["strecken"]] == [
        ("5 km", 5.0),
        ("20 km", 20.0),
    ]
    assert {t["startnummer"]: t["strecke"] for t in stand["teilnehmer"]} == {
        1: 0, 2: 0, 3: 0, 4: 1, 5: 1, 6: 1, 7: 1}  # fmt: skip
    assert stand["konflikte"] == 0  # Plausibilität je Strecke: Jugendzeiten kein Hinweis


def test_oeffentlich_je_strecke(client, v):
    rennen(client)
    d = client.get("/api/oeffentlich").json()
    assert d["mehrere_strecken"] and [x["name"] for x in d["strecken"]] == ["5 km", "20 km"]
    kurz, lang = d["strecken"]
    assert [(z["startnummer"], z["rang"]) for z in kurz["gesamt"]] == [(3, 1), (1, 2), (2, 3)]
    assert [(z["startnummer"], z["rang"]) for z in lang["gesamt"]] == [(5, 1), (4, 2), (6, 3)]
    assert kurz["laenge"] == "5,0 km" and lang["farbe"] == 1
    assert {k["name"]: k["strecke"] for k in d["klassen"]}["Herren 41-50"] == "20 km"
    assert d["letzte"][0]["strecke"] in ("5 km", "20 km")
    assert d["unterwegs"][0]["strecke"] == "20 km" and d["unterwegs"][0]["farbe"] == 1
    html = client.get("/ergebnisse").text
    assert 'id="strecke-1"' in html and 'data-strecke="20 km"' in html and "Gesamt 5 km" in html
    druck = client.get("/ergebnisse/druck").text
    assert druck.count('class="seite"') == len(d["klassen"])
    assert 'strecke-farbe-0">Strecke 5 km</span>' in druck


def test_csv_mit_strecke(admin, v):
    rennen(admin)
    for csv in (admin.get("/ergebnisse.csv"), admin.get(f"/admin/v/{v.id}/export/ergebnis.csv")):
        text = csv.content.decode("utf-8-sig")
        zeilen = text.splitlines()
        assert "Leistungswertung: Deutsches Radsportabzeichen A.1" in zeilen[0]
        assert "5 km: 5,0 km; 20 km: 20,0 km" in text
        assert zeilen[2].startswith("Strecke;Rang gesamt;Rang Klasse")
        daten = [z.split(";") for z in zeilen[3:]]
        assert [z[0] for z in daten][:3] == ["5 km"] * 3 and daten[0][1:4] == ["1", "1", "3"]
        assert daten[3][0] == "20 km" and daten[3][1] == "1"


def test_admin_ergebnis_mit_strecke(admin, v):
    rennen(admin)
    html = admin.get(f"/admin/v/{v.id}/ergebnis").text
    assert "Ränge je Strecke" in html and 'data-strecke="5 km"' in html
    assert html.index('data-nr="3"') < html.index('data-nr="5"')  # nach Strecke gruppiert


def test_eine_strecke_ohne_kennzeichen(client):
    with client.app.state.sessionmaker() as s:
        v = Veranstaltung(name="X", datum=date(2026, 10, 10), aktiv=True, token_start="tok-start",
                          token_ziel="tok-ziel")  # fmt: skip
        s.add(v)
        s.flush()
        s.add(Teilnehmer(veranstaltung_id=v.id, startnummer=1, vorname="A", nachname="B",
                         geschlecht="m", geburtsjahr=1990))  # fmt: skip
        s.commit()
    d = client.get("/api/oeffentlich").json()
    assert not d["mehrere_strecken"] and len(d["strecken"]) == 1
    assert "strecke-marke" not in client.get("/ergebnisse").text
    assert "Strecke;" not in client.get("/ergebnisse.csv").content.decode("utf-8-sig")


def test_import_ersetzen_nennt_geaenderte(admin, v):
    csv = b"startnummer;vorname;nachname;geschlecht;geburtsjahr\n11;Neu;A;m;1990\n12;Neu;B;w;1991\n"
    csv_importieren(admin, v.id, csv, admin.csrf)
    with admin.app.state.sessionmaker() as s:
        tid = s.query(Teilnehmer).filter_by(veranstaltung_id=v.id, startnummer=11).one().id
    # nach dem Import: ein Teilnehmer geändert, einer neu angelegt
    admin.post(f"/admin/v/{v.id}/teilnehmer", data={"csrf": admin.csrf, "tid": str(tid), "startnummer": "11",
               "vorname": "Geändert", "nachname": "A", "geschlecht": "m", "geburtsjahr": "1990", "verein": ""})  # fmt: skip
    admin.post(f"/admin/v/{v.id}/teilnehmer", data={"csrf": admin.csrf, "tid": "", "startnummer": "13",
               "vorname": "Nach", "nachname": "C", "geschlecht": "m", "geburtsjahr": "1980", "verein": ""})  # fmt: skip
    admin.post(
        f"/admin/v/{v.id}/import", data={"csrf": admin.csrf}, files={"datei": ("t.csv", csv)}
    )
    html = admin.get(f"/admin/v/{v.id}/import/vorschau?modus=ersetzen").text
    assert 'id="seit-import-geaendert"' in html and "2 davon wurden seit dem letzten Import" in html
    r = admin.post(
        f"/admin/v/{v.id}/import/uebernehmen", data={"csrf": admin.csrf, "modus": "ersetzen"}
    )
    assert "bitte bestätigen" in r.text and "2 Teilnehmer wurden seit dem letzten Import" in r.text
    r = admin.post(f"/admin/v/{v.id}/import/uebernehmen",
                   data={"csrf": admin.csrf, "modus": "ersetzen", "bestaetigung": "1"})  # fmt: skip
    assert "2 Teilnehmer importiert" in r.text
    admin.post(
        f"/admin/v/{v.id}/import", data={"csrf": admin.csrf}, files={"datei": ("t.csv", csv)}
    )
    assert (
        'id="seit-import-geaendert"'
        not in admin.get(f"/admin/v/{v.id}/import/vorschau?modus=ersetzen").text
    )


def test_wertung_und_laenge_in_einem_formular(admin, client):
    """Eingefroren ohne Wertung: Wertung einschalten und Länge eintragen gelingt zusammen;
    fehlt die Länge, bleibt alles unverändert (eine Transaktion)."""
    with client.app.state.sessionmaker() as s:
        v = Veranstaltung(name="X", datum=date(2026, 10, 10), aktiv=True, token_start="tok-start",
                          token_ziel="tok-ziel", klassensatz_id=saetze.standard_klassensatz(s).id)  # fmt: skip
        s.add(v)
        s.flush()
        s.add(Teilnehmer(veranstaltung_id=v.id, startnummer=1, vorname="A", nachname="B",
                         geschlecht="m", geburtsjahr=1990))  # fmt: skip
        s.commit()
    fahrt(admin, 1, 30)  # friert ein (eine Strecke ohne Länge)
    assert 'id="strecken-eingefroren"' in admin.get(f"/admin/v/{v.id}/strecken").text
    with admin.app.state.sessionmaker() as s:
        sid = st.strecken_von(s, s.get(Veranstaltung, v.id))[0].id
    daten = {"csrf": admin.csrf, "wertungssatz_id": str(saetze_id(admin)), "strecken_anzahl": "1",
             "strecke_id_0": str(sid), "strecke_name_0": "Strecke 1", "strecke_laenge_0": ""}  # fmt: skip
    r = admin.post(f"/admin/v/{v.id}/regeln", data=daten)
    assert "Streckenlänge Pflicht" in r.text
    with admin.app.state.sessionmaker() as s:
        assert s.get(Veranstaltung, v.id).wertungssatz_id is None
    daten["strecke_laenge_0"] = "20,0"
    r = admin.post(f"/admin/v/{v.id}/regeln", data=daten)
    assert "Leistungswertung und Strecken gespeichert" in r.text
    with admin.app.state.sessionmaker() as s:
        x = s.get(Veranstaltung, v.id)
        assert x.wertungssatz_id is not None and st.strecken_von(s, x)[0].laenge_km == 20.0


def test_satzwechsel_laesst_strecken_stehen(admin, v):
    """Wertung aus legt keine Strecke „Strecke 1“ mehr an – die Strecken bleiben samt
    gemessener Längen, ohne Rückfrage."""
    html = admin.get(f"/admin/v/{v.id}/klassen").text
    assert 'name="laengen_verwerfen"' not in html and "Die Strecken bleiben" in html
    daten = {"csrf": admin.csrf, "klassensatz_id": str(v.klassensatz_id), "wertungssatz_id": ""}
    r = admin.post(f"/admin/v/{v.id}/regeln", data=daten)
    assert "Leistungswertung ausgeschaltet" in r.text
    with admin.app.state.sessionmaker() as s:
        liste = st.strecken_von(s, s.get(Veranstaltung, v.id))
        assert [(x.name, x.laenge_km) for x in liste] == [("5 km", 5.0), ("20 km", 20.0)]


def test_abbrechen_fuehrt_zur_aufrufenden_seite(admin, v):
    """„Abbrechen“ ohne Speichern zurück – zur aufrufenden Seite (?zurueck=) oder zum Standard."""
    with admin.app.state.sessionmaker() as s:
        sid = st.strecken_von(s, s.get(Veranstaltung, v.id))[0].id
    # Strecken-Editor: „Zurück“ in der Aktionsleiste, „Abbrechen“ verwirft auf der Seite
    standard = f'href="/admin/v/{v.id}/strecken" id="zurueck-strecken"'
    seite = admin.get(f"/admin/v/{v.id}/strecken/{sid}").text
    assert standard in seite and 'id="aenderungen-verwerfen"' in seite
    seite = admin.get(
        f"/admin/v/{v.id}/strecken/{sid}", params={"zurueck": f"/admin/v/{v.id}"}
    ).text
    assert f'href="/admin/v/{v.id}" id="zurueck-strecken"' in seite
    seite = admin.get(
        f"/admin/v/{v.id}/strecken/{sid}", params={"zurueck": "https://example.org/admin/"}
    ).text
    assert 'href="https:' not in seite.split('id="aktionsleiste"')[1] and standard in seite
    for pfad in ("/einstellungen", "/einstellungen/klassen", "/messpunkte", "/einstellungen/zielmonitor",
                 "/teilnehmer/neu", "/import", "/geraetedatei"):  # fmt: skip
        assert 'class="btn btn-quiet abbrechen"' in admin.get(f"/admin/v/{v.id}{pfad}").text, pfad
    for pfad in ("/admin/saetze/klassen/neu", "/admin/saetze/wertung/neu", "/admin/saetze/import"):
        assert 'class="btn btn-quiet abbrechen"' in admin.get(pfad).text, pfad


def test_alles_speichern_atomar_mit_reiter_und_feld(admin, v):
    """„Alles speichern“ übernimmt alle Reiter auf einmal; ein Prüffehler speichert nichts
    und nennt Reiter und Feld (JSON für den Editor)."""
    with admin.app.state.sessionmaker() as s:
        sid = st.strecken_von(s, s.get(Veranstaltung, v.id))[0].id
    json = {"Accept": "application/json"}
    basis = {"csrf": admin.csrf, "name": "Neu", "laenge": "5,0", "beschreibung": "", "komoot": ""}
    # Fehler im Verlauf: Name bleibt unverändert, Reiter „verlauf“ markiert
    r = admin.post(f"/admin/v/{v.id}/strecken/{sid}", headers=json, data=basis | {
        "tl_anzahl": "1", "tl_von_0": "Start", "tl_nach_0": "Nirgendwo"})  # fmt: skip
    d = r.json()
    assert d["ok"] is False and d["reiter"] == "verlauf" and "Nirgendwo" in d["fehler"]
    with admin.app.state.sessionmaker() as s:
        assert st.strecken_von(s, s.get(Veranstaltung, v.id))[0].name == "5 km"
    # Fehler in den Grunddaten: Feld genannt
    d = admin.post(
        f"/admin/v/{v.id}/strecken/{sid}", headers=json, data=basis | {"laenge": "x"}
    ).json()
    assert (d["reiter"], d["feld"]) == ("grunddaten", "s-laenge")
    d = admin.post(
        f"/admin/v/{v.id}/strecken/{sid}", headers=json, data=basis | {"name": ""}
    ).json()
    assert (d["reiter"], d["feld"]) == ("grunddaten", "s-name")
    d = admin.post(f"/admin/v/{v.id}/strecken/{sid}", headers=json,
                   data=basis | {"komoot": "http://x.de"}).json()  # fmt: skip
    assert (d["reiter"], d["feld"]) == ("grunddaten", "s-komoot")
    d = admin.post(f"/admin/v/{v.id}/strecken/{sid}", headers=json, data=basis | {
        "tl_anzahl": "0", "ab_anzahl": "1", "ab_name_0": "X", "ab_von_0": "Ziel", "ab_nach_0": "Start"}).json()  # fmt: skip
    assert d["reiter"] == "abschnitte"
    # alles in Ordnung: ein Speichern für alle Reiter, Antwort mit Ziel
    d = admin.post(f"/admin/v/{v.id}/strecken/{sid}", headers=json, data=basis | {
        "beschreibung": "Flach", "tl_anzahl": "0", "ab_anzahl": "0"}).json()  # fmt: skip
    assert d["ok"] is True and d["ziel"].startswith(f"/admin/v/{v.id}/strecken/{sid}?ok=")
    with admin.app.state.sessionmaker() as s:
        x = st.strecken_von(s, s.get(Veranstaltung, v.id))[0]
        assert (x.name, x.laenge_km, x.beschreibung) == ("Neu", 5.0, "Flach")
