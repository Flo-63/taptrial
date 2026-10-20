import sqlite3
import uuid
from datetime import date

from sqlalchemy import select

from app.db import erzeuge_engine, erzeuge_sessionmaker, tabellen_anlegen
from app.models import Veranstaltung

T0 = 1_760_000_000_000
ADMIN = {"Authorization": "Bearer tok-admin"}


def anlegen(client, name, aktiv=False, zustand="laufend"):
    with client.app.state.sessionmaker() as s:
        v = Veranstaltung(name=name, datum=date(2026, 10, 10), aktiv=aktiv, zustand=zustand)
        s.add(v)
        s.commit()
        return v


def start(client, token, nr=1, **kw):
    e = {"uuid": str(uuid.uuid4()), "art": "START", "startnummer": nr, "zeit_ms": T0,
         "erstellt_ms": T0, "geraet": "start-x", **kw}  # fmt: skip
    return client.post(
        "/api/ereignisse", json={"ereignisse": [e]}, headers={"Authorization": f"Bearer {token}"}
    )


def test_tokens_werden_beim_anlegen_erzeugt(client):
    a, b = anlegen(client, "A"), anlegen(client, "B")
    tokens = {a.token_start, a.token_ziel, b.token_start, b.token_ziel}
    assert len(tokens) == 4 and all(len(t) >= 24 for t in tokens)
    assert a.anonymisieren_ab == date(2027, 4, 10) and a.zustand == "laufend"


def test_geraetetoken_bindet_an_seine_veranstaltung(client):
    aktiv = anlegen(client, "Aktiv", aktiv=True)
    andere = anlegen(client, "Andere")
    # Die nicht aktive Veranstaltung ist nicht freigegeben – Zeiten werden abgelehnt
    r = start(client, andere.token_start)
    assert r.status_code == 423 and r.json()["detail"]["grund"] == "nicht_freigegeben"
    assert start(client, aktiv.token_start).status_code == 200
    stand = client.get("/api/stand", headers={"Authorization": f"Bearer {andere.token_ziel}"})
    assert stand.json()["veranstaltung"]["name"] == "Andere"
    assert stand.json()["freigabe"]["frei"] is False
    protokoll = client.get(f"/api/ereignisse?veranstaltung_id={andere.id}", headers=ADMIN).json()
    assert protokoll["ereignisse"] == []
    aktive = client.get("/api/ereignisse", headers=ADMIN).json()["ereignisse"]
    assert [e["art"] for e in aktive] == ["START"]
    assert client.get("/api/stand", headers=ADMIN).json()["veranstaltung"]["name"] == "Aktiv"
    # Token der einen Veranstaltung darf nicht in die andere schreiben
    r = start(client, andere.token_start, veranstaltung_id=aktiv.id)
    assert r.status_code == 422


def test_abgeschlossen_token_ungueltig_und_schreibgeschuetzt(client):
    v = anlegen(client, "Fertig", aktiv=True, zustand="abgeschlossen")
    r = start(client, v.token_start)
    assert r.status_code == 410 and r.json()["detail"] == "Veranstaltung beendet"
    r = client.get("/api/stand", headers={"Authorization": f"Bearer {v.token_ziel}"})
    assert r.status_code == 410
    admin = {"uuid": "a1", "art": "STATUS", "startnummer": 1, "wert": "DNF", "erstellt_ms": T0,
             "geraet": "admin", "notiz": "x"}  # fmt: skip
    r = client.post("/api/ereignisse", json={"ereignisse": [admin]}, headers=ADMIN)
    assert r.status_code == 409 and "schreibgeschützt" in r.text
    assert client.get("/api/stand", headers=ADMIN).status_code == 200  # weiter lesbar


def test_anonymisiert_ebenfalls_410_unbekannt_401(client):
    v = anlegen(client, "Alt", zustand="anonymisiert")
    assert start(client, v.token_start).status_code == 410
    assert start(client, "gibt-es-nicht").status_code == 401
    assert (
        "token=" not in client.get(f"/start?token={v.token_start}").text.split("manifest")[1][:40]
    )


def test_umgebungstokens_start_ziel_nicht_mehr_gueltig(client, monkeypatch):
    anlegen(client, "A", aktiv=True)
    monkeypatch.setenv("RCB_TOKEN_START", "alt")
    assert start(client, "alt").status_code == 401


def test_alte_datenbank_wird_nachgeruestet(tmp_path, monkeypatch):
    pfad = tmp_path / "alt.sqlite3"
    con = sqlite3.connect(pfad)
    con.executescript(
        """
        CREATE TABLE veranstaltung (id INTEGER PRIMARY KEY, name VARCHAR(200), datum DATE,
            stichtag_alter DATE, aktiv BOOLEAN);
        CREATE TABLE teilnehmer (id INTEGER PRIMARY KEY, veranstaltung_id INTEGER,
            startnummer INTEGER, vorname VARCHAR(100), nachname VARCHAR(100),
            geschlecht VARCHAR(1), geburtsjahr INTEGER, verein VARCHAR(200));
        INSERT INTO veranstaltung VALUES (1, 'Alt aktiv', '2026-08-31', '2026-12-31', 1);
        INSERT INTO veranstaltung VALUES (2, 'Alt', '2025-10-10', '2025-12-31', 0);
        INSERT INTO veranstaltung VALUES (3, 'Auch aktiv', '2026-09-01', '2026-12-31', 1);
        """
    )
    con.commit()
    con.close()
    monkeypatch.setenv("RCB_TOKEN_START", "bisher-start")
    monkeypatch.setenv("RCB_TOKEN_ZIEL", "bisher-ziel")
    engine = erzeuge_engine(str(pfad))
    tabellen_anlegen(engine)
    tabellen_anlegen(engine)  # zweimal: idempotent
    with erzeuge_sessionmaker(engine)() as s:
        v1, v2, v3 = s.scalars(select(Veranstaltung).order_by(Veranstaltung.id)).all()
    engine.dispose()
    # die (einzige verbleibende) aktive behält die bisherigen Gerätelinks
    assert (v3.token_start, v3.token_ziel) == ("bisher-start", "bisher-ziel")
    for v in (v1, v2):
        assert v.token_start not in ("bisher-start", None) and len(v.token_ziel) >= 24
    assert v1.zustand == v2.zustand == "laufend"
    # mehrere aktive in alter Datei: nur die zuletzt angelegte bleibt aktiv, Index greift
    assert (v1.aktiv, v2.aktiv, v3.aktiv) == (False, False, True)
    assert v1.anonymisieren_ab == date(2027, 2, 28)  # 31.08. + 6 Monate
