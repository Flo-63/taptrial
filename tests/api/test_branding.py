"""Branding: Ist ein Vereinsname gesetzt, steht er vorn (Startseite, Ergebnisse „Verein ·
Veranstaltung“, Admin-Kopf, Zielmonitor, Druckansichten, Urkunden, Seitentitel); „TapTrial“ nur
klein. Ohne Vereinsnamen neutral. Eine Änderung gilt sofort (kein Cache, Monitor über die API)."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import demo, installation
from app.einstellungen import Einstellungen
from app.main import create_app
from app.models import Veranstaltung
from tests.api.test_admin_saetze import post
from tests.api.test_einrichtung import admin, veranstaltung  # noqa: F401 (Fixture)

VEREIN = "RC Beispiel"
PROJEKT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def demo_client(tmp_path_factory):
    """Abgeschlossene Demo-Veranstaltung mit Ergebnissen, Urkunden und Druckansichten."""
    db = str(tmp_path_factory.mktemp("branding") / "demo.sqlite3")
    assert demo.main([], db_pfad=db) == 0
    with TestClient(create_app(Einstellungen(db_pfad=db, token_admin="tok-admin"))) as c:
        c.post("/admin/anmelden", data={"token": "tok-admin"})
        with c.app.state.sessionmaker() as s:
            c.vid = s.scalars(select(Veranstaltung)).one().id
        yield c


def verein_setzen(client, name: str) -> None:
    csrf = re.search(r'name="csrf" value="([^"]+)"', client.get("/admin/saetze/branding").text)
    r = client.post("/admin/saetze/branding", data={"csrf": csrf.group(1), "vereinsname": name})
    assert "Branding gespeichert" in r.text


def seiten(c) -> dict[str, str]:
    v = c.vid
    return {
        "start": c.get("/").text,
        "ergebnisse": c.get(f"/ergebnisse?veranstaltung={v}").text,
        "ergebnisse_inhalt": c.get(f"/ergebnisse/inhalt?veranstaltung={v}").text,
        "druck": c.get(f"/ergebnisse/druck?veranstaltung={v}").text,
        "admin": c.get(f"/admin/v/{v}").text,
        "monitor": c.get("/monitor").text,
        "urkunden": c.get(f"/admin/v/{v}/urkunden").text,
        "aushang": c.get(f"/admin/v/{v}/aushang").text,
        "papierprotokoll": c.get(f"/admin/v/{v}/papierprotokoll").text,
        "geraetelinks": c.get(f"/admin/v/{v}/geraetelinks-druck").text,
    }


def test_mit_vereinsname_vorn_an_allen_stellen(demo_client):
    verein_setzen(demo_client, VEREIN)
    try:
        s = seiten(demo_client)
        assert re.search(
            r'class="start-verein mit-verein" id="start-verein">RC Beispiel<', s["start"]
        )
        assert "<title>RC Beispiel · TapTrial</title>" in s["start"]
        for name in ("ergebnisse", "ergebnisse_inhalt"):
            assert re.search(
                r'<h1><span class="kopf-verein" id="kopf-verein">RC Beispiel</span> · '
                r"Demo-Zeitfahren Kottenforst",
                s[name],
            ), name
        assert "<title>Ergebnisse – RC Beispiel · TapTrial</title>" in s["ergebnisse"]
        assert re.search(
            r'<span class="druck-verein">RC Beispiel</span> · <strong>Demo', s["druck"]
        )
        assert (
            '<span id="marke-verein">RC Beispiel</span><small class="marke-app">TapTrial</small>'
            in s["admin"]
        )
        assert '<span class="marke-zeichen" aria-hidden="true">RB</span>' in s["admin"]
        assert "– RC Beispiel · TapTrial</title>" in s["admin"]
        assert '<span id="mon-verein" class="mon-verein">RC Beispiel</span>' in s["monitor"]
        assert '<p class="urkunde-verein">RC Beispiel</p>' in s["urkunden"]
        assert '<div class="logo">Logo</div>' not in s["urkunden"]
        for name in ("aushang", "papierprotokoll", "geraetelinks"):
            assert '<span class="druck-verein">RC Beispiel</span> · Demo' in s[name], name
        # TapTrial nur klein: Fußzeilen der Druckansichten
        for name in ("druck", "aushang", "papierprotokoll", "geraetelinks"):
            assert "erstellt mit TapTrial" in s[name], name
        assert demo_client.get("/api/oeffentlich").json()["verein"] == VEREIN
    finally:
        verein_setzen(demo_client, "")


def test_ohne_vereinsname_neutral(demo_client):
    s = seiten(demo_client)
    assert re.search(r'class="start-verein" id="start-verein">TapTrial<', s["start"])
    assert "<title>TapTrial – Zeitnahme per Handy</title>" in s["start"]
    for name, text in s.items():
        assert "kopf-verein" not in text and "druck-verein" not in text, name
        assert "RC Beispiel" not in text, name
    assert (
        '<span class="marke-zeichen" aria-hidden="true">TT</span><span>TapTrial</span>'
        in s["admin"]
    )
    assert '<span id="mon-verein" class="mon-verein"></span>' in s["monitor"]
    assert '<div class="logo">Logo</div>' in s["urkunden"]
    assert "<title>Ergebnisse – TapTrial</title>" in s["ergebnisse"]
    assert demo_client.get("/api/oeffentlich").json()["verein"] == ""


def test_aenderung_sofort_sichtbar_und_nicht_zwischengespeichert(demo_client):
    """Nach dem Speichern sofort sichtbar – HTML nie aus dem Browser-Cache."""
    for url in ("/", "/ergebnisse", "/monitor", "/admin/saetze/branding", "/hilfe/admin"):
        assert demo_client.get(url).headers["cache-control"] == "no-cache", url
    verein_setzen(demo_client, "RCB")
    try:
        assert 'id="start-verein">RCB<' in demo_client.get("/").text
        assert demo_client.get("/api/oeffentlich").json()["verein"] == "RCB"
    finally:
        verein_setzen(demo_client, "")
    assert 'class="start-verein" id="start-verein">TapTrial<' in demo_client.get("/").text
    # Service Worker hält nur Geräteseiten, Kurzanleitungen und /static – keine Seite mit Verein
    sw = demo_client.get("/sw.js").text
    for pfad in ('"/"', '"/ergebnisse"', '"/monitor"', '"/admin'):
        assert pfad not in sw, pfad


@pytest.mark.parametrize(
    ("name", "kuerzel"),
    [
        ("RCB", "RCB"),
        ("RC Beispiel", "RB"),
        ("Radsportclub Bonn 1903 e. V.", "RB1"),
        ("tus", "TUS"),
    ],
)
def test_kuerzel(name, kuerzel):
    assert installation.kuerzel(name) == kuerzel


def test_als_naechstes_dezent_ohne_vereinsname(admin):  # noqa: F811
    post(admin, "/admin/einrichtung/neu", name="Kottenforst", datum="2026-10-10")
    v = veranstaltung(admin)
    with admin.app.state.sessionmaker() as s:
        s.get(Veranstaltung, v.id).aktiv = True
        s.commit()
    zeile = re.search(
        r'<p class="hint aufgabe-dezent" data-aufgabe="vereinsname">.*?</p>',
        admin.get(f"/admin/v/{v.id}").text,
        re.DOTALL,
    )
    assert zeile and "Vereinsname noch nicht eingetragen" in zeile.group(0)
    assert 'href="/admin/saetze/branding"' in zeile.group(0)
    verein_setzen(admin, VEREIN)
    assert 'data-aufgabe="vereinsname"' not in admin.get(f"/admin/v/{v.id}").text


def test_branding_mit_hilfe_anker(admin):  # noqa: F811
    seite = admin.get("/admin/saetze/branding").text
    assert re.search(r'href="/hilfe/admin\?zurueck=[^"]*#branding"', seite)
    assert "{#branding}" in (PROJEKT / "docs/handbuch/ADMIN.md").read_text(encoding="utf-8")
