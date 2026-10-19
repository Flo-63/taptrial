"""Release 1: Nach außen heißt die App „TapTrial – Zeitnahme per Handy“; der Vereinsname kommt aus
dem Branding (global), eine frische Installation ist neutral (kein RCB). Interne Namen bleiben."""

import re
from pathlib import Path

from app import marke
from tests.api.test_einrichtung import admin  # noqa: F401 (Fixture)

APP = Path(__file__).resolve().parents[2] / "app"


def test_name_und_untertitel():
    assert marke.NAME == "TapTrial" and marke.UNTERTITEL == "Zeitnahme per Handy"


def test_frische_installation_neutral(client):
    start = client.get("/").text
    assert "<title>TapTrial – Zeitnahme per Handy</title>" in start
    assert re.search(r'id="start-verein">TapTrial<', start)
    for pfad in ("/", "/ergebnisse", "/monitor", "/hilfe/start", "/start", "/admin"):
        text = client.get(pfad).text
        assert "RCB" not in text, pfad
        assert "– TapTrial</title>" in text or "TapTrial – Zeitnahme" in text, pfad
    manifest = client.get("/start.webmanifest").json()
    assert manifest["name"] == "TapTrial – Start" and manifest["short_name"] == "Start"
    assert client.app.title == "TapTrial"


def test_keine_sichtbaren_rcb_texte_in_vorlagen():
    for datei in (APP / "templates").glob("*.html"):
        assert "RCB" not in datei.read_text(encoding="utf-8"), datei.name


def test_vereinsname_aus_dem_branding(admin):  # noqa: F811
    seite = admin.get("/admin/saetze/branding").text
    assert 'name="vereinsname"' in seite and "Kommt mit dem Branding" not in seite
    csrf = re.search(r'name="csrf" value="([^"]+)"', seite).group(1)
    r = admin.post("/admin/saetze/branding", data={"csrf": csrf, "vereinsname": "  RC  Beispiel "})
    assert "Branding gespeichert" in r.text and 'value="RC Beispiel"' in r.text
    assert re.search(r'id="start-verein">RC Beispiel<', admin.get("/").text)
    r = admin.post("/admin/saetze/branding", data={"csrf": "falsch", "vereinsname": "X"})
    assert r.status_code in (400, 403)
    admin.post("/admin/saetze/branding", data={"csrf": csrf, "vereinsname": ""})
    assert re.search(r'id="start-verein">TapTrial<', admin.get("/").text)


def test_druckansichten_mit_fusszeile():
    for name in ("ergebnisse_druck", "urkunden", "papierprotokoll", "aushang_ergebnisse",
                 "geraetelinks_druck"):  # fmt: skip
        text = (APP / "templates" / f"{name}.html").read_text(encoding="utf-8")
        assert "_druckfuss.html" in text or "erstellt mit {{ APP_NAME }}" in text, name
    assert "erstellt mit {{ APP_NAME }}" in (APP / "templates" / "_druckfuss.html").read_text()


def test_interne_namen_bleiben():
    """Bestehende Installationen und Geräte dürfen nicht brechen: Umgebungsvariablen, Gerätespeicher."""
    assert "RCB_TOKEN_ADMIN" in (APP / "einstellungen.py").read_text(
        encoding="utf-8"
    ) or "RCB_" in (APP / "einstellungen.py").read_text(encoding="utf-8")
    assert 'DB_NAME = "rcb-zeitfahren"' in (APP / "static/js/geraet.js").read_text(encoding="utf-8")


def test_druckfuss_erscheint(client):
    assert "erstellt mit TapTrial" in client.get("/ergebnisse/druck").text
