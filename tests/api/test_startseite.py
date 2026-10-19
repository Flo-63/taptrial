from datetime import date

from app.models import Veranstaltung


def test_startseite_ohne_veranstaltung(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 200
    assert "Derzeit keine Veranstaltung" in r.text


def test_startseite_mit_aktiver_veranstaltung_und_drei_links(client):
    with client.app.state.sessionmaker() as s:
        s.add(Veranstaltung(name="Zeitfahren Kottenforst", datum=date(2026, 10, 10), aktiv=True,
                            token_start="geheim-start", token_ziel="geheim-ziel"))  # fmt: skip
        s.add(Veranstaltung(name="Alt", datum=date(2025, 10, 10), aktiv=False))
        s.commit()
    html = client.get("/").text
    assert "Zeitfahren Kottenforst" in html and "10.10.2026" in html and "Alt" not in html
    for href, text in (("/ergebnisse", "Ergebnisse"), ("/monitor", "Zielmonitor"),
                       ("/admin", "Anmeldung Admin")):  # fmt: skip
        assert f'href="{href}"' in html and text in html
    assert "Link bzw. QR-Code von der Rennleitung" in html
    assert "geheim" not in html and "token" not in html  # keine Gerätelinks


def test_startseite_nicht_im_service_worker_cache(client):
    sw = client.get("/sw.js").text
    zeile = next(z for z in sw.splitlines() if z.startswith("const ASSETS"))
    assert '"/"' not in zeile and '"/start"' in zeile
