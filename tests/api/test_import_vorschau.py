"""CSV-Import mit Vorschau und Bearbeitung."""

import re

import pytest
from sqlalchemy import select

from app.models import ImportEntwurf, Teilnehmer, Veranstaltung

KOPF = "startnummer;vorname;nachname;geschlecht;geburtsjahr;verein\n"


def csrf(html):
    return re.search(r'name="csrf" value="([0-9a-f]+)"', html).group(1)


@pytest.fixture
def admin(client):
    client.post("/admin/anmelden", data={"token": "tok-admin"})
    client.csrf = csrf(client.get("/admin").text)
    client.post("/admin/veranstaltungen", data={"csrf": client.csrf, "name": "R", "datum": "2026-10-10",
                                                "aktivieren": "1"})  # fmt: skip
    with client.app.state.sessionmaker() as s:
        client.vid = s.scalars(select(Veranstaltung)).one().id
    return client


def hochladen(admin, csv: str):
    return admin.post(f"/admin/v/{admin.vid}/import", data={"csrf": admin.csrf},
                      files={"datei": ("liste.csv", csv.encode())})  # fmt: skip


def pruefen(admin, zeilen, modus="ergaenzen", token=None):
    return admin.post(f"/admin/v/{admin.vid}/import/entwurf", json={"zeilen": zeilen, "modus": modus},
                      headers={"X-CSRF-Token": token or admin.csrf})  # fmt: skip


def teilnehmer(admin):
    with admin.app.state.sessionmaker() as s:
        return s.scalars(select(Teilnehmer).order_by(Teilnehmer.startnummer)).all()


def test_vorschau_markiert_fehler_und_warnungen_je_zelle(admin):
    r = hochladen(admin, KOPF + "1;Anna;Muster;w;1990;\n1;anna;muster;x;1990;\n3;;C;m;neunzehn;\n")
    assert r.status_code == 200 and "Import-Vorschau: liste.csv" in r.text
    assert "4 Fehler, 1 Warnungen" in r.text
    zeile2 = r.text.split('data-index="1"')[1].split("</tr>")[0]
    assert (
        'class="zelle zelle-fehler' in zeile2
        and "Startnummer 1 doppelt (bereits in Zeile 1)" in zeile2
    )
    assert "ungültiges Geschlecht „x“" in zeile2 and "gleicher Name wie in Zeile 1" in zeile2
    zeile3 = r.text.split('data-index="2"')[1].split("</tr>")[0]
    assert "Vorname fehlt" in zeile3 and "ungültiges Geburtsjahr" in zeile3
    assert 'id="importieren" disabled' in r.text


def test_live_pruefung_speichert_entwurf(admin):
    hochladen(admin, KOPF + "1;Anna;Muster;w;1990;\n2;Ben;B;q;1985;\n")
    zeilen = [{"startnummer": "1", "vorname": "Anna", "nachname": "Muster", "geschlecht": "w",
               "geburtsjahr": "1990"},
              {"startnummer": "2", "vorname": "Ben", "nachname": "B", "geschlecht": "M", "geburtsjahr": "1985"}]  # fmt: skip
    p = pruefen(admin, zeilen).json()
    assert p == {
        "fehler": [],
        "warnungen": [],
        "anzahl_fehler": 0,
        "anzahl_warnungen": 0,
        "zeilen": 2,
    }
    # Entwurf ist serverseitig gespeichert: Vorschau zeigt den korrigierten Stand
    seite = admin.get(f"/admin/v/{admin.vid}/import/vorschau").text
    assert "0 Fehler, 0 Warnungen" in seite and 'value="M"' in seite
    assert 'id="offener-entwurf"' in admin.get(f"/admin/v/{admin.vid}/teilnehmer").text
    p = pruefen(admin, [*zeilen, {"startnummer": "", "vorname": "C"}]).json()
    assert p["anzahl_fehler"] == 4 and [2, "startnummer", "ungültige Startnummer „“"] in p["fehler"]


def test_live_pruefung_braucht_csrf(admin):
    hochladen(admin, KOPF + "1;A;B;m;1990;\n")
    assert pruefen(admin, [], token="falsch").status_code == 403


def test_korrigierte_csv_herunterladen(admin):
    hochladen(admin, KOPF + "1;Anna;Muster;w;1990;RC\n")
    pruefen(admin, [{"startnummer": "7", "vorname": "Anna", "nachname": "Neu", "geschlecht": "w",
                     "geburtsjahr": "1990", "verein": "RC", "abzeichen": "ja"}])  # fmt: skip
    r = admin.get(f"/admin/v/{admin.vid}/import/entwurf.csv")
    assert 'filename="liste-korrigiert.csv"' in r.headers["content-disposition"]
    zeilen = r.content.decode("utf-8-sig").splitlines()
    assert (
        zeilen[0]
        == "startnummer;vorname;nachname;geschlecht;geburtsjahr;verein;abzeichen;ausser_konkurrenz"
    )
    assert zeilen[1] == "7;Anna;Neu;w;1990;RC;ja;"


def test_uebernehmen_ergaenzen_und_ersetzen_mit_bestaetigung(admin):
    hochladen(admin, KOPF + "1;A;A;m;1990;\n2;B;B;w;1990;\n")
    r = admin.post(
        f"/admin/v/{admin.vid}/import/uebernehmen", data={"csrf": admin.csrf, "modus": "ergaenzen"}
    )
    assert "2 Teilnehmer importiert" in r.text and len(teilnehmer(admin)) == 2
    with admin.app.state.sessionmaker() as s:
        assert s.scalars(select(ImportEntwurf)).first() is None  # Entwurf nach Import gelöscht
    # ergänzen mit vorhandener Startnummer: Fehler in der Vorschau
    vorschau = hochladen(admin, KOPF + "2;C;C;m;1980;\n")
    assert "Startnummer 2 ist bereits vergeben" in vorschau.text
    assert (
        "Startnummer 2 ist bereits vergeben"
        not in admin.get(f"/admin/v/{admin.vid}/import/vorschau?modus=ersetzen").text
    )
    r = admin.post(
        f"/admin/v/{admin.vid}/import/uebernehmen", data={"csrf": admin.csrf, "modus": "ersetzen"}
    )
    assert "bitte bestätigen" in r.text and len(teilnehmer(admin)) == 2
    r = admin.post(f"/admin/v/{admin.vid}/import/uebernehmen",
                   data={"csrf": admin.csrf, "modus": "ersetzen", "bestaetigung": "1"})  # fmt: skip
    assert "1 Teilnehmer importiert" in r.text
    assert [(t.startnummer, t.vorname) for t in teilnehmer(admin)] == [(2, "C")]


def test_entwurf_verwerfen_und_kopffehler(admin):
    hochladen(admin, KOPF + "1;A;A;m;1990;\n")
    r = admin.post(f"/admin/v/{admin.vid}/import/verwerfen", data={"csrf": admin.csrf})
    assert "verworfen" in r.text
    r = admin.get(f"/admin/v/{admin.vid}/import/vorschau")
    assert "Kein Import-Entwurf" in r.text
    r = hochladen(admin, "vorname;nachname\nA;B\n")
    assert "Datei nicht lesbar" in r.text and "Spalte „startnummer“ fehlt" in r.text
