"""Admin: Klassen- und Wertungssätze (Phase 12)."""

import re
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app import saetze as sz
from app.models import Klassensatz, Teilnehmer, Veranstaltung, Wertungssatz
from tests.conftest import csv_importieren

T0 = int(datetime(2026, 10, 10, 10, 0, tzinfo=ZoneInfo("Europe/Berlin")).timestamp() * 1000)
MIN = 60_000


def csrf(html):
    return re.search(r'name="csrf" value="([0-9a-f]+)"', html).group(1)


@pytest.fixture
def admin(client):
    client.post("/admin/anmelden", data={"token": "tok-admin"})
    client.csrf = csrf(client.get("/admin").text)
    return client


def post(c, pfad, **daten):
    return c.post(pfad, data={"csrf": c.csrf, **daten})


def tabelle(zeilen, felder):
    daten = {"anzahl": str(len(zeilen))}
    for i, z in enumerate(zeilen):
        for f, w in zip(felder, z, strict=True):
            daten[f"{f}_{i}"] = str(w)
    return daten


KF = ("name", "geschlecht", "alter_von", "alter_bis")
SF = ("klasse", "strecke_km", "gold", "silber", "bronze")


def satz(c, modell, name):
    with c.app.state.sessionmaker() as s:
        x = sz.nach_name(s, modell, name)
        return x.id if x else None


def test_uebersicht_mit_standardsaetzen(admin):
    html = admin.get("/admin/saetze").text  # leitet auf die Klassensätze weiter
    assert 'data-satz="DRA 2026"' in html
    html = admin.get("/admin/saetze/wertung").text
    for name in ("DRA A.1 flach", "DRA A.2 Berg Straße", "DRA A.3 Berg Gelände"):
        assert f'data-satz="{name}"' in html
    assert 'id="nav-vorlagen"' in admin.get("/admin").text  # Kopfzeile „Vorlagen“
    assert admin.get("/admin/saetze/klassen/1").status_code == 200
    assert (
        admin.get(
            f"/admin/saetze/wertung/{satz(admin, Wertungssatz, 'DRA A.2 Berg Straße')}"
        ).status_code
        == 200
    )


def test_klassensatz_anlegen_bearbeiten_mit_warnung_und_fehler(admin):
    r = post(admin, "/admin/saetze/klassen/neu", name="Vereinsmeisterschaft",
             **tabelle([("Herren", "M", 18, 39), ("Senioren", "m", 45, 99), ("Damen", "w", 18, 99)], KF))  # fmt: skip
    assert "gespeichert" in r.text and "Lücke bei Geschlecht m: Alter 40–44" in r.text
    sid = satz(admin, Klassensatz, "Vereinsmeisterschaft")
    # Überschneidung: Fehler, Eingaben bleiben stehen
    r = post(admin, f"/admin/saetze/klassen/{sid}", name="Vereinsmeisterschaft",
             **tabelle([("Herren", "m", 18, 50), ("Senioren", "m", 45, 99)], KF))  # fmt: skip
    assert r.status_code == 422 and "überschneiden" in r.text and 'value="50"' in r.text
    # ändern, hinzufügen, entfernen
    daten = tabelle([("Herren", "m", 18, 44), ("Senioren", "m", 45, 99), ("Damen", "w", 18, 99),
                     ("Jugend", "w", 10, 17)], KF)  # fmt: skip
    daten["entfernen_2"] = "1"
    r = post(admin, f"/admin/saetze/klassen/{sid}", name="Vereinsmeisterschaft", **daten)
    assert "gespeichert" in r.text and "Lücke bei" not in r.text
    with admin.app.state.sessionmaker() as s:
        klassen = [(k.name, k.alter_von, k.alter_bis) for k in s.get(Klassensatz, sid).klassen]
    assert klassen == [("Herren", 18, 44), ("Senioren", 45, 99), ("Jugend", 10, 17)]


def test_kopieren_loeschen_benutzt(admin):
    r = post(admin, "/admin/saetze/klassen/1/kopieren")
    assert "Kopie „Kopie von DRA 2026“ angelegt" in r.text
    kopie = satz(admin, Klassensatz, "Kopie von DRA 2026")
    r = post(admin, f"/admin/saetze/klassen/{kopie}/loeschen")
    assert "gelöscht" in r.text and satz(admin, Klassensatz, "Kopie von DRA 2026") is None
    r = post(admin, "/admin/saetze/klassen/1/loeschen")
    assert "wird noch verwendet" in r.text and satz(admin, Klassensatz, "DRA 2026")


def test_export_import_toml_und_csv(admin):
    toml = admin.get("/admin/saetze/klassen/1/export.toml")
    assert (
        toml.status_code == 200 and "[[klasse]]" in toml.text and 'name = "DRA 2026"' in toml.text
    )
    csv = admin.get("/admin/saetze/klassen/1/export.csv")
    assert csv.content.decode("utf-8-sig").startswith("name;geschlecht;alter_von;alter_bis")
    r = admin.post("/admin/saetze/klassen-import", data={"csrf": admin.csrf, "name": "Aus CSV"},
                   files={"datei": ("k.CSV", csv.content)})  # fmt: skip
    assert "importiert" in r.text
    r = admin.post("/admin/saetze/klassen-import", data={"csrf": admin.csrf, "name": "Aus TOML"},
                   files={"datei": ("k.toml", toml.content)})  # fmt: skip
    assert "importiert" in r.text
    with admin.app.state.sessionmaker() as s:
        original = sz.klassen_von(s.get(Klassensatz, 1))
        for name in ("Aus CSV", "Aus TOML"):
            assert sz.klassen_von(sz.nach_name(s, Klassensatz, name)) == original
    kaputt = b"NAME;GESCHLECHT;ALTER_VON;ALTER_BIS\nA;M;1;50\nB;m;40;60\n"
    r = admin.post("/admin/saetze/klassen-import", data={"csrf": admin.csrf, "name": "Kaputt"},
                   files={"datei": ("k.csv", kaputt)})  # fmt: skip
    assert "Import abgebrochen" in r.text and "überschneiden" in r.text


def test_wertungssatz_bearbeiten_export_import(admin):
    wid = satz(admin, Wertungssatz, "DRA A.1 flach")
    toml = admin.get(f"/admin/saetze/wertung/{wid}/export.toml").text
    assert 'art = "gesamtzeit"' in toml and "[[stufe]]" in toml
    csv = admin.get(f"/admin/saetze/wertung/{wid}/export.csv").content
    r = admin.post("/admin/saetze/wertung-import",
                   data={"csrf": admin.csrf, "name": "Flach CSV", "art": "gesamtzeit", "klassensatz_id": "1"},
                   files={"datei": ("w.csv", csv)})  # fmt: skip
    assert "importiert" in r.text
    r = admin.post("/admin/saetze/wertung-import", data={"csrf": admin.csrf, "klassensatz_id": "1"},
                   files={"datei": ("w.toml", toml.encode().replace(b"art =", b"ART ="))})  # fmt: skip
    assert "importiert" in r.text  # Name aus der Beschreibung, Schlüssel ohne Groß-/Kleinschreibung
    neu = satz(admin, Wertungssatz, "Flach CSV")
    r = post(admin, f"/admin/saetze/wertung/{neu}", name="Flach CSV", art="gesamtzeit", klassensatz_id="1",
             **tabelle([("Herren 31-40", "20", "0:46:00", "0:43:00", "0:38:00")], SF))  # fmt: skip
    assert r.status_code == 422 and "gold &lt; silber &lt; bronze" in r.text
    r = post(admin, f"/admin/saetze/wertung/{neu}", name="Flach CSV", art="gesamtzeit", klassensatz_id="1",
             **tabelle([("herren 31-40", "20,0", "0:38:00", "0:43:00", "0:46:00")], SF))  # fmt: skip
    assert "gespeichert" in r.text and "Ohne Stufen" in r.text


# --- Veranstaltung: Auswahl und Einfrieren -------------------------------------------


@pytest.fixture
def v(admin):
    post(admin, "/admin/veranstaltungen", name="Kottenforst", datum="2026-10-10", aktivieren="1")
    with admin.app.state.sessionmaker() as s:
        return s.scalars(select(Veranstaltung)).one()


def regeln(admin, v, **daten):
    return post(admin, f"/admin/v/{v.id}/regeln", **daten)


def test_klassensatz_waehlen_wirkt_auf_import(admin, v):
    post(admin, "/admin/saetze/klassen/neu", name="Nur Erwachsene",
         **tabelle([("Männer", "m", 18, 99), ("Frauen", "w", 18, 99)], KF))  # fmt: skip
    ks = satz(admin, Klassensatz, "Nur Erwachsene")
    r = regeln(admin, v, klassensatz_id=str(ks), wertungssatz_id="", strecke_km="")
    assert "ausgeschaltet" in r.text
    csv = b"startnummer;vorname;nachname;geschlecht;geburtsjahr\n1;Kind;K;m;2015\n2;Anna;A;w;1990\n"
    vorschau, _ = csv_importieren(admin, v.id, csv, admin.csrf)
    assert "keine Altersklasse für Geschlecht m, Alter 11" in vorschau.text
    _, r = csv_importieren(admin, v.id, csv.replace(b"1;Kind;K;m;2015\n", b""), admin.csrf)
    assert (
        "1 Teilnehmer importiert" in r.text
        and "Frauen" in admin.get(f"/admin/v/{v.id}/teilnehmer").text
    )
    # Wertungssatz mit Klassen außerhalb des Klassensatzes wird abgewiesen
    r = regeln(admin, v, klassensatz_id=str(ks), wertungssatz_id=str(satz(admin, Wertungssatz, "DRA A.1 flach")),
               strecke_km="20,0")  # fmt: skip
    assert "passt nicht zum Klassensatz" in r.text


def test_eingefroren_nach_erstem_ereignis(admin, v):
    wid = satz(admin, Wertungssatz, "DRA A.1 flach")
    regeln(admin, v, klassensatz_id="1", wertungssatz_id=str(wid), strecke_km="20,0")
    with admin.app.state.sessionmaker() as s:
        s.add(Teilnehmer(veranstaltung_id=v.id, startnummer=1, vorname="Ben", nachname="B", geschlecht="m",
                         geburtsjahr=1990, abzeichen=True))  # fmt: skip
        s.commit()
        tok_start, tok_ziel = (
            s.get(Veranstaltung, v.id).token_start,
            s.get(Veranstaltung, v.id).token_ziel,
        )
    html = admin.get(f"/admin/v/{v.id}/klassen").text
    assert 'id="eingefroren"' not in html
    for art, token, zeit in (("START", tok_start, T0), ("ZIEL", tok_ziel, T0 + 37 * MIN)):
        e = {"uuid": str(uuid.uuid4()), "art": art, "startnummer": 1, "zeit_ms": zeit, "erstellt_ms": zeit,
             "geraet": "g"}  # fmt: skip
        assert admin.post("/api/ereignisse", json={"ereignisse": [e]},
                          headers={"Authorization": f"Bearer {token}"}).status_code == 200  # fmt: skip
    html = admin.get(f"/admin/v/{v.id}/klassen").text
    assert 'id="eingefroren"' in html and "Klassensatz „DRA 2026“" in html
    assert re.search(r'<select name="klassensatz_id" id="wahl-klassensatz" disabled', html)
    vorher = admin.get(f"/admin/v/{v.id}/ergebnis").text.split('data-nr="1"')[1].split("</tr>")[0]
    assert "Gold" in vorher and "Herren 31-40" in vorher
    # Gold-Schwelle im Satz verschärfen → keine Auswirkung auf die eingefrorene Veranstaltung
    with admin.app.state.sessionmaker() as s:
        w = s.get(Wertungssatz, wid)
        zeilen = sz.wertung_zeilen(w)
        for z in zeilen:
            if z["klasse"] == "Herren 31-40":
                z["gold"] = "0:30:00"
        sz.wertungssatz_speichern(s, w, w.name, w.art, None, None, w.klassensatz_id, zeilen)
    nachher = admin.get(f"/admin/v/{v.id}/ergebnis").text.split('data-nr="1"')[1].split("</tr>")[0]
    assert nachher == vorher
    r = regeln(admin, v, klassensatz_id=str(satz(admin, Klassensatz, "DRA 2026")), wertungssatz_id=str(wid),
               strecke_km="20,0")  # fmt: skip
    assert "gespeichert" in r.text  # gleicher Klassensatz bleibt erlaubt


def test_wertungseditor_bietet_nur_freie_klassen(admin):
    post(admin, "/admin/saetze/klassen/neu", name="Zwei",
         **tabelle([("Männer", "m", 0, 99), ("Frauen", "w", 0, 99)], KF))  # fmt: skip
    ks = satz(admin, Klassensatz, "Zwei")
    zeile = ("Männer", "20", "0:30:00", "0:35:00", "0:40:00")
    r = post(admin, "/admin/saetze/wertung/neu", name="W", art="gesamtzeit", klassensatz_id=str(ks),
             **tabelle([zeile], SF))  # fmt: skip
    wid = satz(admin, Wertungssatz, "W")
    html = admin.get(f"/admin/saetze/wertung/{wid}").text
    selects = re.findall(r'<select name="klasse_\d+".*?</select>', html, re.DOTALL)
    assert len(selects) == 2  # gespeicherte Zeile + eine freie
    assert 'value="Männer" selected' in selects[0] and "Frauen" in selects[0]
    assert "Männer" not in selects[1] and 'value="Frauen"' in selects[1]
    assert 'id="zeile-neu" >' in html or 'id="zeile-neu"  >' in html
    assert 'id="alle-klassen" hidden' in html
    # alle Klassen belegt → keine freie Zeile, Knopf deaktiviert, Hinweis sichtbar
    r = post(admin, f"/admin/saetze/wertung/{wid}", name="W", art="gesamtzeit", klassensatz_id=str(ks),
             **tabelle([zeile, ("Frauen", "20", "0:33:00", "0:38:00", "0:43:00")], SF))  # fmt: skip
    assert "gespeichert" in r.text
    assert len(re.findall(r'<select name="klasse_\d+"', r.text)) == 2
    assert (
        re.search(r'id="zeile-neu"\s+disabled', r.text)
        and "Alle Klassen haben einen Eintrag" in r.text
    )
    assert not re.search(r'id="alle-klassen"\s+hidden', r.text)


def test_vorlagen_mit_seitenleiste(admin):
    """Gruppen „Wertung“ und „Gestaltung“ wie in einer Veranstaltung (Test 08.10., Punkt 13)."""
    html = admin.get("/admin/saetze/klassen").text
    assert (
        '<div class="nav-group">Wertung</div>' in html
        and '<div class="nav-group">Gestaltung</div>' in html
    )
    for seite, text in (("klassen", "Klassensätze"), ("wertung", "Wertungssätze"),
                        ("branding", "Branding"), ("urkunden", "Urkunden-Vorlagen")):  # fmt: skip
        assert (
            f'href="/admin/saetze/{seite}" data-seite="{seite}"' in html
            and f"<span>{text}</span>" in html
        )
        seite_html = admin.get(f"/admin/saetze/{seite}").text
        assert f'data-seite="{seite}"\n         aria-current="page"' in seite_html, seite
    assert "Kommt mit dem Branding (AUSBAU D1)." in admin.get("/admin/saetze/urkunden").text
    assert 'name="vereinsname"' in admin.get("/admin/saetze/branding").text  # Release 1
    assert (
        'aria-current="page"' in admin.get("/admin/saetze/klassen/1").text
    )  # Editor: Klassensätze aktiv
    assert (
        'data-seite="wertung"\n         aria-current="page"'
        in admin.get("/admin/saetze/wertung/neu").text
    )
