import json
from datetime import date

import pytest

from app.models import Veranstaltung


@pytest.fixture(autouse=True)
def veranstaltung(client):
    with client.app.state.sessionmaker() as s:
        s.add(
            Veranstaltung(
                name="Test",
                datum=date(2026, 10, 10),
                aktiv=True,
                token_start="tok-start",
                token_ziel="tok-ziel",
            )
        )
        s.commit()


def test_startseite_ohne_token_erreichbar(client):
    r = client.get("/start")
    assert r.status_code == 200
    assert 'id="btn-start"' in r.text
    assert 'href="/start.webmanifest"' in r.text


def test_nur_gueltiger_token_wird_in_seite_uebernommen(client):
    assert "token=tok-start" in client.get("/start?token=tok-start").text
    r = client.get("/start?token=<script>")
    assert '<script>"' not in r.text and "token=" not in r.text.split("manifest")[1][:40]
    assert "token=tok-ziel" not in client.get("/start?token=tok-ziel").text


def test_manifest(client):
    m = client.get("/start.webmanifest?token=tok-start").json()
    assert m["start_url"] == "/start?token=tok-start"
    assert m["display"] == "standalone"
    assert client.get("/start.webmanifest?token=falsch").json()["start_url"] == "/start"
    assert client.get("/admin.webmanifest").status_code == 404
    # Segmente 44: Messpunkt und Rennleitungs-Sicht bleiben im Startbildschirm-Link (eigenes Profil)
    m = client.get("/ziel.webmanifest?token=tok-ziel&ort=7x&sicht=rennleitung").json()
    assert m["start_url"] == "/ziel?token=tok-ziel&ort=7&sicht=rennleitung"
    seite = client.get("/ziel?token=tok-ziel&ort=7&sicht=rennleitung").text
    assert 'href="/ziel.webmanifest?token=tok-ziel&amp;ort=7&amp;sicht=rennleitung"' in seite


def test_service_worker(client):
    r = client.get("/sw.js")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/javascript")
    assert "__VERSION__" not in r.text
    zeile = next(z for z in r.text.splitlines() if z.startswith("const ASSETS"))
    assets = json.loads(zeile.split("=", 1)[1].strip().rstrip(";"))
    assert "/start" in assets and "/static/js/geraet.js" in assets
    for pfad in assets:
        assert client.get(pfad).status_code == 200, pfad


def test_zielseite(client):
    r = client.get("/ziel?token=tok-ziel")
    assert r.status_code == 200
    assert 'id="btn-ohne-nummer"' in r.text and "token=tok-ziel" in r.text
    assert "/ziel" in client.get("/sw.js").text


def _png_groesse(daten: bytes) -> tuple[int, int]:
    assert daten[:8] == b"\x89PNG\r\n\x1a\n"
    return int.from_bytes(daten[16:20], "big"), int.from_bytes(daten[20:24], "big")


def test_png_icons(client):
    m = client.get("/ziel.webmanifest").json()
    pngs = {i["sizes"]: i["src"] for i in m["icons"] if i["type"] == "image/png"}
    assert set(pngs) == {"192x192", "512x512"}
    for groesse, src in pngs.items():
        b, h = _png_groesse(client.get(src).content)
        assert f"{b}x{h}" == groesse
    seite = client.get("/start").text
    assert 'rel="apple-touch-icon" href="/static/icons/icon-180.png"' in seite
    assert _png_groesse(client.get("/static/icons/icon-180.png").content) == (180, 180)
