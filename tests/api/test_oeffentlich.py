"""Öffentliche Ergebnisse, CSV, Druckansicht, Monitor-Daten und Leistungswertung."""

import uuid
from datetime import date

import pytest

from app import korrekturen as k
from app.models import Teilnehmer, Veranstaltung

T0 = 1_791_000_000_000
MIN = 60_000
A1 = "radsportabzeichen-a1-flach.toml"
# Nr, Geschlecht, Jahrgang → Klasse (Stichtag 2026)
FAHRER = [
    (1, "m", 1990),  # Herren 31-40
    (2, "m", 1991),  # Herren 31-40
    (3, "m", 1988),  # Herren 31-40
    (4, "w", 1990),  # Damen 31-40
    (5, "m", 1989),  # Herren 31-40
    (6, "m", 1987),  # Herren 31-40
    (7, "w", 1995),  # Damen 31-40
    (8, "m", 1986),  # Herren 31-40
]


@pytest.fixture
def v(client):
    with client.app.state.sessionmaker() as s:
        v = Veranstaltung(name="Kottenforst", datum=date(2026, 10, 10), aktiv=True,
                          token_start="tok-start", token_ziel="tok-ziel")  # fmt: skip
        s.add(v)
        s.flush()
        for nr, g, j in FAHRER:
            s.add(Teilnehmer(veranstaltung_id=v.id, startnummer=nr, vorname=f"Vor{nr}",
                             nachname=f"Nach{nr}", geschlecht=g, geburtsjahr=j,
                             verein="RC Bonn" if nr % 2 else None, abzeichen=nr != 8))  # fmt: skip
        s.commit()
        return v


def senden(client, *ereignisse, rolle="ziel"):
    token = {"ziel": "tok-ziel", "start": "tok-start", "admin": "tok-admin"}[rolle]
    batch = []
    for e in ereignisse:
        e = {"uuid": str(uuid.uuid4()), "geraet": rolle, "erstellt_ms": e.get("zeit_ms") or T0, **e}
        batch.append(e)
    r = client.post("/api/ereignisse", json={"ereignisse": batch},
                    headers={"Authorization": f"Bearer {token}"})  # fmt: skip
    assert r.status_code == 200, r.text


def fahrt(client, nr, minuten, ms=0):
    senden(client, {"art": "START", "startnummer": nr, "zeit_ms": T0}, rolle="start")
    senden(client, {"art": "ZIEL", "startnummer": nr, "zeit_ms": T0 + minuten * MIN + ms})


def szenario(client):
    """Nr. 2 und 3 zeitgleich (gleiche Zehntel), 4 Damen, 5 DNF, 6 DNS, 7 unterwegs, 8 offen."""
    fahrt(client, 1, 30)
    fahrt(client, 2, 35, 40)
    fahrt(client, 3, 35, 90)  # 35:00,09 → gleiche Zehntel wie 35:00,04
    fahrt(client, 4, 40)
    senden(client, {"art": "START", "startnummer": 5, "zeit_ms": T0}, rolle="start")
    senden(client, {"art": "STATUS", "startnummer": 5, "wert": "DNF"})
    senden(client, {"art": "STATUS", "startnummer": 6, "wert": "DNS"}, rolle="start")
    senden(client, {"art": "START", "startnummer": 7, "zeit_ms": T0 + MIN}, rolle="start")


def daten(client):
    r = client.get("/api/oeffentlich")
    assert r.status_code == 200
    return r.json()


def test_ohne_aktive_veranstaltung(client):
    assert client.get("/ergebnisse").status_code == 200
    assert "Derzeit keine Ergebnisse" in client.get("/ergebnisse").text
    assert daten(client) == {"veranstaltung": None, "verein": ""}
    assert client.get("/monitor").status_code == 200
    assert client.get("/ergebnisse.csv").status_code == 404


def test_ohne_token_erreichbar_und_nur_e6_felder(client, v):
    szenario(client)
    d = daten(client)
    text = str(d)
    assert "1990" not in text and "tok-" not in text and "geschlecht" not in text
    assert set(d["gesamt"][0]) == {
        "startnummer", "name", "verein", "klasse", "zeit", "rang", "rang_klasse", "status",
        "status_text", "vorbehalt", "ausser_konkurrenz", "stufe", "stufe_text", "start_ms", "ziel_ms",
        "strecke", "farbe", "segmente",
    }  # fmt: skip


def test_sortierung_gleichstand_und_status_unten(client, v):
    szenario(client)
    d = daten(client)
    gesamt = [(z["startnummer"], z["rang"], z["zeit"], z["status_text"]) for z in d["gesamt"]]
    assert gesamt == [
        (1, 1, "30:00,0", ""),
        (2, 2, "35:00,0", ""),
        (3, 2, "35:00,0", ""),  # Gleichstand: gleicher Rang
        (4, 4, "40:00,0", ""),
        (5, None, "", "DNF"),
        (6, None, "", "DNS"),
    ]
    assert [k["name"] for k in d["klassen"]] == ["Herren 31-40", "Damen 31-40"]
    herren = d["klassen"][0]["zeilen"]
    assert [(z["startnummer"], z["rang_klasse"]) for z in herren] == [
        (1, 1), (2, 2), (3, 2), (5, None), (6, None),
    ]  # fmt: skip
    assert d["zaehlung"] == {
        "gestartet": 6,
        "unterwegs": 1,
        "im_ziel": 4,
        "dns": 1,
        "dnf": 1,
        "dsq": 0,
    }


def test_monitor_bereiche(client, v):
    szenario(client)
    d = daten(client)
    assert [z["startnummer"] for z in d["letzte"]] == [4, 3, 2, 1]  # neuester Einlauf zuerst
    assert d["letzte"][0]["rang_klasse"] == 1 and d["letzte"][0]["klasse"] == "Damen 31-40"
    assert [(u["startnummer"], u["start_ms"]) for u in d["unterwegs"]] == [(7, T0 + MIN)]
    assert d["nicht_gestartet"] == {"anzahl": 1, "naechste": [8]}


def test_unterwegs_nach_startzeit_sortiert(client, v):
    senden(client, {"art": "START", "startnummer": 2, "zeit_ms": T0 + 5 * MIN},
           {"art": "START", "startnummer": 1, "zeit_ms": T0 + 6 * MIN},
           {"art": "START", "startnummer": 3, "zeit_ms": T0 + 4 * MIN}, rolle="start")  # fmt: skip
    assert [u["startnummer"] for u in daten(client)["unterwegs"]] == [3, 2, 1]


def test_dsq_vorlaeufig_und_bestaetigt(client, v):
    szenario(client)
    senden(
        client,
        {"art": "STATUS", "startnummer": 1, "wert": "DSQ_VORLAEUFIG", "notiz": "Windschatten"},
    )
    d = daten(client)
    z1 = next(z for z in d["gesamt"] if z["startnummer"] == 1)
    assert (z1["rang"], z1["vorbehalt"], z1["status_text"]) == (1, True, "unter Vorbehalt")
    assert "Windschatten" not in str(d)  # Grund ist intern
    html = client.get("/ergebnisse").text
    assert 'class="vorbehalt status-ok"' in html and "unter Vorbehalt" in html
    senden(
        client,
        {"art": "STATUS", "startnummer": 1, "wert": "DSQ", "notiz": "bestätigt"},
        rolle="admin",
    )
    d = daten(client)
    herren = [
        (z["startnummer"], z["rang_klasse"], z["status_text"]) for z in d["klassen"][0]["zeilen"]
    ]
    assert herren[0] == (2, 1, "") and (1, None, "DSQ") in herren
    assert herren.index((1, None, "DSQ")) > herren.index((5, None, "DNF"))  # DSQ am Klassenende
    assert d["zaehlung"]["dsq"] == 1


def test_ergebnisstatus_kopf_ueberall(client, v):
    fahrt(client, 1, 30)
    for pfad in ("/ergebnisse", "/ergebnisse/druck"):
        assert "Vorläufiges Ergebnis" in client.get(pfad).text
    assert daten(client)["kopf"] == "Vorläufiges Ergebnis"
    assert "Vorläufiges Ergebnis" in client.get("/ergebnisse.csv").content.decode("utf-8-sig")
    senden(client, {"art": "ERGEBNIS", "wert": "OFFIZIELL", "notiz": "geprüft"}, rolle="admin")
    d = daten(client)
    assert d["offiziell"] and d["kopf"].startswith("Offizielles Ergebnis, Stand ")
    for pfad in ("/ergebnisse", "/ergebnisse/druck"):
        assert "Offizielles Ergebnis, Stand" in client.get(pfad).text
    assert "Offizielles Ergebnis, Stand" in client.get("/ergebnisse.csv").content.decode(
        "utf-8-sig"
    )


def test_csv_inhalt(client, v):
    szenario(client)
    r = client.get("/ergebnisse.csv")
    assert r.headers["content-type"].startswith("text/csv")
    zeilen = r.content.decode("utf-8-sig").splitlines()
    assert zeilen[0].startswith("Kottenforst – 10.10.2026;Vorläufiges Ergebnis")
    assert (
        zeilen[2]
        == "Rang gesamt;Rang Klasse;Startnummer;Name;Verein;Klasse;Fahrzeit;Status;Bemerkung"
    )
    assert zeilen[3] == "1;1;1;Vor1 Nach1;RC Bonn;Herren 31-40;30:00,0;;"
    assert zeilen[5] == "2;2;3;Vor3 Nach3;RC Bonn;Herren 31-40;35:00,0;;"
    assert zeilen[-2].startswith(";;5;Vor5 Nach5") and zeilen[-2].endswith("DNF;")
    assert not any(";7;" in z or ";8;" in z for z in zeilen)  # unterwegs/offen nicht in der Liste


def test_druckansicht_eine_klasse_pro_seite(client, v):
    szenario(client)
    html = client.get("/ergebnisse/druck").text
    assert html.count('class="seite"') == 2
    assert 'data-klasse="Herren 31-40"' in html and 'data-klasse="Damen 31-40"' in html


# --- Leistungswertung --------------------------------------------------------


def satz_id(client, name="DRA A.1 flach"):
    from app import saetze
    from app.models import Wertungssatz

    with client.app.state.sessionmaker() as s:
        return saetze.nach_name(s, Wertungssatz, name).id


def wertung_an(client, v, strecke=20.0):
    from app import saetze

    with client.app.state.sessionmaker() as s:
        x = s.get(Veranstaltung, v.id)
        from app import strecken
        from app.models import Wertungssatz

        # Strecken wie der Vorschlag aus dem Satz (legt der Satzwechsel nicht mehr an)
        ws = s.get(Wertungssatz, int(satz_id(client)))
        strecken.anlegen(
            s, x, strecken.vorschlag(saetze.regeln(s, x).klassen, saetze.wertung_von(ws))
        )
        saetze.regeln_setzen(s, x, x.klassensatz_id, ws.id, strecke)

        for st in strecken.strecken_von(s, x):  # Vorschlag 5 km/20 km; Erwachsene: gemessen
            if st.laenge_km == 20.0:
                st.laenge_km = strecke
        s.commit()


def test_wertung_aus_nirgends_sichtbar(client, v):
    szenario(client)
    d = daten(client)
    assert d["wertung"] is None and all(z["stufe"] is None for z in d["gesamt"])
    assert "Abzeichen" not in client.get("/ergebnisse").text
    assert "Abzeichen" not in client.get("/ergebnisse/druck").text
    assert "Abzeichen" not in client.get("/ergebnisse.csv").content.decode("utf-8-sig")
    stand = client.get("/api/stand", headers={"Authorization": "Bearer tok-ziel"}).json()
    assert all(t["stufe"] is None for t in stand["teilnehmer"])


def test_wertung_an(client, v):
    wertung_an(client, v)
    fahrt(client, 1, 38)  # Herren 31-40: Gold bis 38:00
    fahrt(client, 2, 44)  # Bronze (bis 46:00)
    fahrt(client, 3, 50)  # keine
    fahrt(client, 4, 47, 900)  # Damen 31-40: Gold bis 48:00
    senden(client, {"art": "START", "startnummer": 5, "zeit_ms": T0}, rolle="start")
    senden(client, {"art": "ZIEL", "startnummer": 5, "zeit_ms": T0 + 30 * MIN})
    senden(client, {"art": "STATUS", "startnummer": 5, "wert": "DNF"})
    d = daten(client)
    stufen = {z["startnummer"]: z["stufe_text"] for z in d["gesamt"]}
    assert stufen == {1: "Gold", 2: "Bronze", 3: "–", 4: "Gold", 5: "–"}
    assert d["wertung"].startswith("Deutsches Radsportabzeichen A.1")
    assert "Abzeichen" in client.get("/ergebnisse").text
    csv = client.get("/ergebnisse.csv").content.decode("utf-8-sig")
    assert "Leistungswertung: Deutsches Radsportabzeichen A.1" in csv and "20,0 km" in csv
    assert ";Abzeichen;Bemerkung" in csv and "Herren 31-40;38:00,0;;Gold;" in csv
    stand = client.get("/api/stand", headers={"Authorization": "Bearer tok-ziel"}).json()
    assert next(t for t in stand["teilnehmer"] if t["startnummer"] == 1)["stufe"] == "GOLD"


def test_wertung_vorbehalt_manuell_und_konflikt(client, v):
    wertung_an(client, v)
    fahrt(client, 1, 30)
    senden(client, {"art": "STATUS", "startnummer": 1, "wert": "DSQ_VORLAEUFIG", "notiz": "x"})
    senden(client, {"art": "START", "startnummer": 2, "zeit_ms": T0}, rolle="start")
    senden(client, {"art": "MANUELLE_ZEIT", "startnummer": 2, "zeit_ms": T0 + 35 * MIN,
                    "notiz": "Handstoppuhr"}, rolle="admin")  # fmt: skip
    fahrt(client, 3, 30)
    senden(client, {"art": "ZIEL", "startnummer": 3, "zeit_ms": T0 + 31 * MIN})  # Konflikt
    d = daten(client)
    stufen = {z["startnummer"]: z["stufe_text"] for z in d["gesamt"]}
    assert (
        stufen[1] == "Gold (unter Vorbehalt)" and stufen[2] == "Gold (manuell)" and stufen[3] == "–"
    )
    senden(client, {"art": "STATUS", "startnummer": 1, "wert": "DSQ", "notiz": "ok"}, rolle="admin")
    assert {z["startnummer"]: z["stufe_text"] for z in daten(client)["gesamt"]}[1] == "–"


def test_wertung_zu_kurze_strecke(client, v):
    wertung_an(client, v, strecke=19.9)
    fahrt(client, 1, 30)
    assert daten(client)["gesamt"][0]["stufe"] is None


def test_wertung_im_admin_setzen(client, v):
    r = client.post("/admin/anmelden", data={"token": "tok-admin"})
    assert r.status_code == 200
    import re

    csrf = re.search(r'name="csrf" value="([0-9a-f]+)"', client.get("/admin").text).group(1)
    a2 = satz_id(client, "DRA A.2 Berg Straße")  # pro_km: keine Länge im Satz → Pflichtfeld
    r = client.post(
        f"/admin/v/{v.id}/regeln", data={"csrf": csrf, "wertungssatz_id": a2, "strecke_km": ""}
    )
    assert "Streckenlänge Pflicht" in r.text
    r = client.post(
        f"/admin/v/{v.id}/regeln",
        data={"csrf": csrf, "wertungssatz_id": satz_id(client), "strecke_km": "20,0"},
    )
    assert "Klassen, Leistungswertung und Strecken gespeichert" in r.text
    assert "20,0 km" in client.get(f"/admin/v/{v.id}/strecken").text
    assert "vorgeschlagen" not in r.text  # Keine Strecken still angelegt
    fahrt(client, 1, 30)
    assert "Abzeichen" in client.get(f"/admin/v/{v.id}/ergebnis").text
    r = client.post(
        f"/admin/v/{v.id}/regeln", data={"csrf": csrf, "wertungssatz_id": "", "strecke_km": ""}
    )
    assert "ausgeschaltet" in r.text
    assert "Abzeichen" not in client.get(f"/admin/v/{v.id}/ergebnis").text


def test_ungueltige_wertungsdatei_verhindert_start(einstellungen, tmp_path):
    import dataclasses

    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.wertung import WertungsConfigFehler

    (tmp_path / "w").mkdir()
    (tmp_path / "w" / "kaputt.toml").write_text('name = "X"\nart = "falsch"\n')
    e = dataclasses.replace(einstellungen, wertungen_verzeichnis=str(tmp_path / "w"))
    with pytest.raises(WertungsConfigFehler), TestClient(create_app(e)):
        pass


def test_bestimmte_veranstaltung_fuer_pdf_export(client, v):
    fahrt(client, 1, 30)
    with client.app.state.sessionmaker() as s:
        s.add(Veranstaltung(name="Neu aktiv", datum=date(2026, 11, 1), aktiv=False))
        s.get(Veranstaltung, v.id).zustand = "abgeschlossen"  # Laufende erst abschließen
        s.commit()
        k.aktiv_setzen(s, s.query(Veranstaltung).filter_by(name="Neu aktiv").one())
    assert "Neu aktiv" in client.get("/ergebnisse/druck").text
    alt = client.get(f"/ergebnisse/druck?veranstaltung={v.id}").text
    assert "Kottenforst" in alt and "Vor1 Nach1" in alt
    assert (
        client.get(f"/api/oeffentlich?veranstaltung={v.id}").json()["gesamt"][0]["startnummer"] == 1
    )


# --- außer Konkurrenz / Abzeichen-Merkmal -------------------------------------------


def merkmal(client, nr, **werte):
    with client.app.state.sessionmaker() as s:
        t = s.query(Teilnehmer).filter_by(startnummer=nr).one()
        for name, wert in werte.items():
            setattr(t, name, wert)
        s.commit()


def test_ausser_konkurrenz_ueberall_ohne_rang(client, v):
    merkmal(client, 2, ausser_konkurrenz=True)
    fahrt(client, 1, 31)
    fahrt(client, 2, 29)  # schnellste Zeit, aber a. K.
    fahrt(client, 3, 30)
    d = daten(client)
    gesamt = [(z["startnummer"], z["rang"], z["status_text"]) for z in d["gesamt"]]
    assert gesamt[:3] == [(2, None, "a. K."), (3, 1, ""), (1, 2, "")]  # nach Zeit einsortiert
    herren = d["klassen"][0]["zeilen"]
    assert [(z["startnummer"], z["rang_klasse"]) for z in herren][:3] == [(2, None), (3, 1), (1, 2)]
    assert [z["startnummer"] for z in d["letzte"]] == [1, 3, 2]  # neuester Einlauf zuerst
    assert next(z for z in d["letzte"] if z["startnummer"] == 2)["status_text"] == "a. K."
    for pfad in ("/ergebnisse", "/ergebnisse/druck"):
        assert "a. K." in client.get(pfad).text
    csv = client.get("/ergebnisse.csv").content.decode("utf-8-sig")
    assert ";;2;Vor2 Nach2;;Herren 31-40;29:00,0;a. K.;" in csv
    stand = client.get("/api/stand", headers={"Authorization": "Bearer tok-ziel"}).json()
    assert next(t for t in stand["teilnehmer"] if t["startnummer"] == 2)["ausser_konkurrenz"]


def test_abzeichen_nein_zeigt_strich(client, v):
    wertung_an(client, v)
    merkmal(client, 1, abzeichen=False)
    fahrt(client, 1, 30)
    fahrt(client, 2, 30)
    stufen = {z["startnummer"]: z["stufe_text"] for z in daten(client)["gesamt"]}
    assert stufen == {1: "–", 2: "Gold"}
    assert "keine Wertung" not in client.get("/ergebnisse").text
