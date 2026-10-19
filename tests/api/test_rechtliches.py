"""Release 3: Impressum und Datenschutz global als Link (https) oder Text; Links in der Fußzeile der
öffentlichen Seiten und der Hilfe (nicht auf dem Zielmonitor); fehlen sie, meldet „Als Nächstes“ es
dezent, sobald eine Veranstaltung aktiv ist."""

import re

from app.models import Veranstaltung
from tests.api.test_admin_saetze import post
from tests.api.test_einrichtung import admin, veranstaltung  # noqa: F401 (Fixture)

SEITE = "/admin/saetze/rechtliches"


def speichern(admin, **felder):  # noqa: F811
    csrf = re.search(r'name="csrf" value="([^"]+)"', admin.get(SEITE).text).group(1)
    return admin.post(SEITE, data={"csrf": csrf, **felder})


def test_link_oder_text(admin):  # noqa: F811
    assert "Impressum &amp; Datenschutz" in admin.get(SEITE).text
    assert admin.get("/impressum").status_code == 404  # frisch: nichts eingerichtet
    fuss = admin.get("/").text
    assert "data-rechtlich" not in fuss
    r = speichern(admin, impressum_url="https://verein.example/impressum",
                  datenschutz_text="Wir speichern Namen.\nUnd Zeiten.\n\nZweiter Absatz <b>x</b>")  # fmt: skip
    assert "gespeichert" in r.text
    for pfad in ("/", "/ergebnisse", "/hilfe/admin", "/ergebnisse/druck"):
        text = admin.get(pfad).text
        assert "https://verein.example/impressum" in text, pfad
        assert "/datenschutz" in text, pfad
    r = admin.get("/impressum", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "https://verein.example/impressum"
    ds = admin.get("/datenschutz").text
    assert "<p>Wir speichern Namen.<br>Und Zeiten.</p>" in ds and "&lt;b&gt;x&lt;/b&gt;" in ds
    assert (
        "Datenschutz" not in admin.get("/monitor").text.split("<script")[0]
    )  # nicht auf dem Monitor


def test_pruefung(admin):  # noqa: F811
    r = speichern(admin, impressum_url="https://x.example/i", impressum_text="auch Text")
    assert (
        r.status_code == 422
        and "nicht beides" in r.text
        and 'value="https://x.example/i"' in r.text
    )
    r = speichern(admin, datenschutz_url="http://unsicher.example")
    assert r.status_code == 422 and "https://" in r.text
    assert speichern(admin, impressum_url="", datenschutz_text="").status_code == 200


def test_als_naechstes_dezent_bei_aktiver_veranstaltung(admin):  # noqa: F811
    post(admin, "/admin/einrichtung/neu", name="Kottenforst", datum="2026-10-10")
    v = veranstaltung(admin)
    uebersicht = admin.get(f"/admin/v/{v.id}").text
    assert 'data-aufgabe="rechtliches"' not in uebersicht  # noch nicht aktiv
    with admin.app.state.sessionmaker() as s:
        s.get(Veranstaltung, v.id).aktiv = True
        s.commit()
    uebersicht = admin.get(f"/admin/v/{v.id}").text
    zeile = re.search(
        r'<p class="hint aufgabe-dezent" data-aufgabe="rechtliches">.*?</p>', uebersicht, re.DOTALL
    )
    assert zeile and "Impressum und Datenschutzerklärung fehlen" in zeile.group(0)
    assert 'class="aufgabe" data-aufgabe="rechtliches"' not in uebersicht  # keine echte Aufgabe
    speichern(admin, impressum_url="https://v.example/i")
    assert "Datenschutzerklärung fehlt" in admin.get(f"/admin/v/{v.id}").text
    speichern(admin, impressum_url="https://v.example/i", datenschutz_text="Text")
    assert 'data-aufgabe="rechtliches"' not in admin.get(f"/admin/v/{v.id}").text
