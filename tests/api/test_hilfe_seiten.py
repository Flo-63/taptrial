import pytest

from app.hilfe import SEITEN


@pytest.mark.parametrize("name", list(SEITEN))
def test_hilfe_seite(client, name):
    r = client.get(f"/hilfe/{name}")
    assert r.status_code == 200
    assert SEITEN[name][1] in r.text and "<h1>" in r.text
    assert "/static/css/hilfe.css" in r.text


def test_unbekannte_hilfe_und_bilder(client):
    assert client.get("/hilfe/gibtsnicht").status_code == 404
    assert client.get("/hilfe/bilder/start.png").headers["content-type"] == "image/png"
    assert client.get("/hilfe/bilder/..%2F..%2FADMIN.md").status_code == 404
    assert client.get("/hilfe/bilder/fehlt.png").status_code == 404


def test_hilfe_links_auf_geraete_und_admin(client):
    assert 'href="/hilfe/start"' in client.get("/start").text
    assert 'href="/hilfe/ziel"' in client.get("/ziel").text
    assert 'href="/hilfe/admin?zurueck=/admin#anmelden"' in client.get("/admin").text


def test_kurzanleitungen_offline_verfuegbar(client):
    sw = client.get("/sw.js").text
    for pfad in ("/hilfe/start", "/hilfe/ziel", "/hilfe/rennleitung", "/hilfe/bilder/start.png"):
        assert f'"{pfad}"' in sw
    assert '"/hilfe/admin"' not in sw
