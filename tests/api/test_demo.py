"""Segmente 68: Demo-Veranstaltung über die normale App-Logik – Skript gegen leere DB, alle
Ergebnisansichten fehlerfrei, abgeschlossen und nicht aktiv, wiederholbar (--ersetzen)."""

import csv
import io
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import demo, ranglisten, strecken
from app import korrekturen as k
from app import monitor_konfig as mk
from app import segmente as sg
from app.auswertung import Status
from app.db import erzeuge_engine, erzeuge_sessionmaker
from app.einstellungen import Einstellungen
from app.main import create_app
from app.models import Ereignis, Veranstaltung, Wertungssatz, aktive_veranstaltung

PROJEKT = Path(__file__).resolve().parents[2]


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "demo.sqlite3")


def sitzung(db):
    engine = erzeuge_engine(db)
    return engine, erzeuge_sessionmaker(engine)()


def anmelden(client):
    client.post("/admin/anmelden", data={"token": "tok-admin"})
    return re.search(r'name="csrf" value="([0-9a-f]+)"', client.get("/admin").text).group(1)


def test_skript_gegen_leere_db_alle_ansichten(db):
    r = subprocess.run([sys.executable, "scripts/demo.py", "--db", db], cwd=PROJEKT,
                       capture_output=True, text=True, check=False)  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert "Demo-Zeitfahren Kottenforst" in r.stdout and "nicht aktiv" in r.stdout
    engine, s = sitzung(db)
    with s:
        v = s.scalars(select(Veranstaltung)).one()
        assert (v.name, v.datum.isoformat(), v.zustand, v.aktiv) == (
            "Demo-Zeitfahren Kottenforst", "2026-10-04", "abgeschlossen", False)  # fmt: skip
        assert v.einrichtung_schritt is None  # Zielmonitor wie die Vorgabe im Assistenten
        assert json.loads(v.monitor_json) == mk.vorschlag(strecken.strecken_von(s, v))
        erg = k.ergebnis(s, v)
        rl = ranglisten.verfuegbar(erg)
    engine.dispose()
    assert erg.offiziell and rl
    with TestClient(create_app(Einstellungen(db_pfad=db, token_admin="tok-admin"))) as c:
        anmelden(c)
        for url in (f"/admin/v/{v.id}/ergebnis", f"/admin/v/{v.id}/drucken", f"/admin/v/{v.id}/urkunden",
                    f"/admin/v/{v.id}/export/ergebnis.csv", f"/admin/v/{v.id}/export/protokoll.csv",
                    f"/ergebnisse?veranstaltung={v.id}", f"/ergebnisse/druck?veranstaltung={v.id}",
                    f"/ergebnisse.csv?veranstaltung={v.id}",
                    *(f"/admin/v/{v.id}/ergebnis?rangliste={key}" for key, _ in rl),
                    *(f"/ergebnisse.csv?veranstaltung={v.id}&rangliste={key}" for key, _ in rl)):  # fmt: skip
            antwort = c.get(url)
            assert antwort.status_code == 200, url
        tabelle = list(csv.reader(io.StringIO(c.get(f"/admin/v/{v.id}/export/ergebnis.csv").text.lstrip("﻿")),
                                  delimiter=";"))  # fmt: skip
        i = next(i for i, z in enumerate(tabelle) if "Startnummer" in z)  # nach den Titelzeilen
        kopf, zeilen = tabelle[i], [z for z in tabelle[i + 1 :] if z]
        assert any("P1→P2" in spalte for spalte in kopf)  # Abschnitts-/Segmentzeiten im Export
        status = [z[kopf.index("Status")] for z in zeilen]
        assert status[-2:] == ["DNF", "DNS"]  # DNF/DNS am Ende
        urkunden = re.findall(
            r'<section class="urkunde" data-nr="(\d+)"', c.get(f"/admin/v/{v.id}/urkunden").text
        )
        assert "5" in urkunden and "12" not in urkunden and "13" not in urkunden
        # danach lässt sich eine zweite Veranstaltung aktiv setzen (die Demo blockiert nichts)
        csrf = anmelden(c)
        c.post("/admin/veranstaltungen", data={"csrf": csrf, "name": "Echtes Rennen", "datum": "2026-10-18",
                                                "aktivieren": "1"})  # fmt: skip
    engine, s = sitzung(db)
    with s:
        assert aktive_veranstaltung(s).name == "Echtes Rennen"
    engine.dispose()


def test_rennverlauf_aus_der_echten_auswertung(db):
    assert demo.main(["--db", db]) == 0
    engine, s = sitzung(db)
    with s:
        v = s.scalars(select(Veranstaltung)).one()
        erg = k.ergebnis(s, v)
        ereignisse = list(s.scalars(select(Ereignis).where(Ereignis.veranstaltung_id == v.id)))
        orte = [(m.name, m.art) for m in strecken.orte_von(s, v)]
        liste = strecken.strecken_von(s, v)
        p1 = next(m for m in strecken.orte_von(s, v) if m.name == "P1")
    engine.dispose()
    assert orte == [("Start 20k", "start"), ("Start Jugend", "start"), ("P1", "punkt"), ("P2", "punkt"),
                    ("Ziel", "ziel")]  # fmt: skip
    assert [(x.name, x.laenge_km) for x in liste] == [("5 km", 5.0), ("20 km", 20.0)]
    z = {x.startnummer: x for x in erg.zeilen}
    zwanzig = sorted(
        (x for x in erg.zeilen if x.strecke == 1 and x.status == Status.OK),
        key=lambda x: x.rang_gesamt,
    )
    assert zwanzig[0].startnummer == 5  # Cancellara am schnellsten
    assert {x.startnummer for x in zwanzig[:8]} == set(range(1, 9))  # Legenden vor den Comics
    assert (z[12].status, z[13].status) == (Status.DNF, Status.DNS)
    assert [x.startnummer for x in erg.zeilen[-2:]] == [12, 13]
    assert {z[14].strecke, z[15].strecke} == {0}  # Jugend 5 km
    # Schnitte 28–42 km/h; Segmentzeiten ±3 % zum Tempo des Fahrers
    for x in erg.zeilen:
        if x.fahrzeit_ms is None:
            continue
        km = liste[x.strecke].laenge_km
        kmh = km / (x.fahrzeit_ms / 3_600_000)
        assert 28 <= kmh <= 42, x.startnummer
        laengen = [d.segment.km for d in sg.abfolge(strecken.teile_von(liste[x.strecke]))]
        for seg, l in zip(x.segmentzeiten, laengen, strict=False):
            if seg.zeit_ms is not None:
                assert abs(seg.zeit_ms / (l / kmh * 3_600_000) - 1) <= 0.031, x.startnummer
    # Donald: vergessener Klick an P2 (fehlende Segmentzeit, Gesamtzeit da)
    assert z[9].status == Status.OK and any(seg.zeit_ms is None for seg in z[9].segmentzeiten)
    # Daisy: Doppeltipp an P1 – automatisch verworfen, Segmentzeiten vollständig
    daisy = [
        e.uuid
        for e in ereignisse
        if e.art == "DURCHFAHRT" and e.startnummer == 10 and e.wert == str(p1.id)
    ]
    assert len(daisy) == 4 and list(erg.nicht_gezaehlt.values()) == ["Doppeltipp"]
    assert all(seg.zeit_ms for seg in z[10].segmentzeiten)
    # Asterix: Zielzeit ohne Nummer, in der Rennleitung zugeordnet
    ohne = next(e for e in ereignisse if e.art == "ZIEL" and e.startnummer is None)
    assert any(
        e.art == "ZUORDNUNG" and e.bezug_uuid == ohne.uuid and e.startnummer == 11
        for e in ereignisse
    )
    assert z[11].ziel_ms == ohne.zeit_ms and erg.offene_ziele == []
    # Abzeichen aus der Wertung: einige ja (Gold/Silber/Bronze), andere nicht
    assert {z[5].stufe, z[11].stufe, z[9].stufe} == {"GOLD", "SILBER", "BRONZE"}
    assert z[12].stufe is None and z[13].stufe is None and z[6].stufe is None
    # Ereignisse wie von den Geräten (Start, Start Jugend, Posten, Ziel/Rennleitung) plus Admin
    assert {e.geraet for e in ereignisse} == {"start-demo", "start-jugend-demo", "posten-p1-demo",
                                             "posten-p2-demo", "ziel-demo", "admin"}  # fmt: skip
    assert all(
        e.erstellt_ms // 86_400_000 == ereignisse[0].erstellt_ms // 86_400_000 for e in ereignisse
    )
    assert [e.wert for e in ereignisse if e.art == "ERGEBNIS"] == ["OFFIZIELL"]


def test_wiederholbar_nur_mit_ersetzen(db, capsys):
    assert demo.main(["--db", db]) == 0
    engine, s = sitzung(db)
    with s:
        alt = s.scalars(select(Veranstaltung)).one()
        alt_token = alt.token_start
    engine.dispose()
    assert demo.main(["--db", db]) == 1
    assert "gibt es schon" in capsys.readouterr().err
    assert demo.main(["--db", db, "--ersetzen"]) == 0
    engine, s = sitzung(db)
    with s:
        alle = list(s.scalars(select(Veranstaltung)))
        assert (
            len(alle) == 1
            and alle[0].token_start != alt_token
            and alle[0].zustand == "abgeschlossen"
        )
        from app.models import GeloeschterToken, token_fingerabdruck

        assert s.scalar(select(GeloeschterToken).where(  # über die normale Löschfunktion entfernt
            GeloeschterToken.fingerabdruck == token_fingerabdruck(alt_token)))  # fmt: skip
    engine.dispose()


def test_ohne_dra_vorlage_abbruch(db, capsys):
    engine, s = sitzung(db)
    from app import saetze
    from app.db import tabellen_anlegen

    tabellen_anlegen(engine)
    with s:
        saetze.standardsaetze_anlegen(s)
        s.scalars(
            select(Wertungssatz).where(Wertungssatz.name == "DRA A.1 flach")
        ).one().name = "Eigene Wertung"
        s.commit()
    engine.dispose()
    assert demo.main(["--db", db]) == 1
    assert "DRA A.1 flach" in capsys.readouterr().err
    engine, s = sitzung(db)
    with s:
        assert list(s.scalars(select(Veranstaltung))) == []
    engine.dispose()


def test_aktive_veranstaltung_bleibt_aktiv_laufende_blockiert(db, capsys):
    engine, s = sitzung(db)
    from app.db import tabellen_anlegen

    tabellen_anlegen(engine)
    with s:
        from app import saetze

        saetze.standardsaetze_anlegen(s)
        echt = k.veranstaltung_anlegen(
            s, "Echtes Rennen", demo.DATUM.replace(day=18), aktivieren=True
        )
        echt_id = echt.id
    engine.dispose()
    assert demo.main(["--db", db]) == 0
    engine, s = sitzung(db)
    with s:
        assert aktive_veranstaltung(s).id == echt_id
        echt = s.get(Veranstaltung, echt_id)
        k.teilnehmer_speichern(s, echt, None, {"startnummer": "1", "vorname": "A", "nachname": "B",
                                               "geschlecht": "m", "geburtsjahr": "1980"})  # fmt: skip
        from app import api
        from app.auth import Zugang

        api.ereignisse_annehmen(s, Zugang("start", echt), api.Batch(ereignisse=[
            {"uuid": "s1", "art": "START", "startnummer": 1, "zeit_ms": 1, "erstellt_ms": 1, "geraet": "g"}]))  # fmt: skip
    engine.dispose()
    assert demo.main(["--db", db, "--ersetzen"]) == 1  # laufende Veranstaltung wird nicht verdrängt
    assert "läuft" in capsys.readouterr().err
    engine, s = sitzung(db)
    with s:
        assert aktive_veranstaltung(s).id == echt_id
        assert [x.name for x in s.scalars(select(Veranstaltung)) if x.name == demo.NAME] == [
            demo.NAME
        ]
    engine.dispose()


def test_verwaltung_demo(db, monkeypatch):
    from app import verwaltung

    monkeypatch.setenv("RCB_DB_PFAD", db)
    assert verwaltung.main(["demo"]) == 0
    assert verwaltung.main(["demo"]) == 1
    assert verwaltung.main(["demo", "--ersetzen"]) == 0
