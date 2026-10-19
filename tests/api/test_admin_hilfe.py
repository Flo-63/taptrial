"""Hilfe im Admin: gleicher Tab, Rücksprung, passender Abschnitt je Seite (Test 08.10., Punkt 12)."""

import html as h
import re

from tests.api import test_admin

admin, v, keine_sperre = test_admin.admin, test_admin.v, test_admin.keine_sperre  # Fixtures


def hilfe_link(html: str) -> str:
    tag = re.search(r'<a [^>]*id="nav-hilfe"[^>]*>', html).group(0)
    assert "target=" not in tag  # gleicher Tab
    return h.unescape(re.search(r'href="([^"]+)"', tag).group(1))


def test_hilfe_springt_zum_passenden_abschnitt(admin, v):
    handbuch = admin.get("/hilfe/admin").text
    seiten = {"": "uebersicht", "/einstellungen": "einstellungen", "/einstellungen/klassen": "klassen-wertung",
              "/strecken": "strecken", "/messpunkte": "messpunkte", "/einstellungen/zielmonitor": "zielmonitor-ergebnisse",
              "/teilnehmer": "teilnehmer",
              "/teilnehmer/neu": "teilnehmer", "/import": "import", "/geraete": "geraete",
              "/aufgaben": "aufgaben", "/protokoll": "protokoll", "/geraetedatei": "protokoll",
              "/ergebnis": "ergebnis", "/drucken": "drucken", "/status": "status"}  # fmt: skip
    for pfad, anker in seiten.items():
        link = hilfe_link(admin.get(f"/admin/v/{v.id}{pfad}").text)
        assert link == f"/hilfe/admin?zurueck=/admin/v/{v.id}{pfad}#{anker}", pfad
        assert f'id="{anker}"' in handbuch, anker
    assert 'id="oeffentlichkeit"' in handbuch  # Segmente 60: früherer Anker springt weiter
    r = admin.get(f"/admin/v/{v.id}/einstellungen/oeffentlichkeit", follow_redirects=False)
    assert (
        r.status_code == 307
        and r.headers["location"] == f"/admin/v/{v.id}/einstellungen/zielmonitor"
    )
    seite = admin.get(f"/admin/v/{v.id}/einstellungen/zielmonitor").text
    assert (
        ">Zielmonitor und Ergebnisse</a>" in seite
        and "Öffentlichkeit &amp; Zielmonitor" not in seite
    )
    reihe = [
        seite.index(t)
        for t in ('id="zielmonitor"', 'id="monitor-live"', 'id="zwischenzeiten-oeffentlich"')
    ]
    assert reihe == sorted(
        reihe
    )  # Zielmonitor → Live-Bereich → Zwischenzeiten öffentlich (→ Ergebnistabelle)
    for anker in (
        "segmente",
        "zielmonitor",
        "strecke",
    ):  # Unterabschnitte der Einstellungen (Segmente 20)
        assert f'id="{anker}"' in handbuch, anker
    with admin.app.state.sessionmaker() as s:
        from app import strecken as st
        from app.models import Veranstaltung

        sid = st.strecken_von(s, s.get(Veranstaltung, v.id))[0].id
    assert hilfe_link(admin.get(f"/admin/v/{v.id}/strecken/{sid}").text).endswith("#strecke")
    for pfad, anker in (("/admin", "veranstaltungen"), ("/admin/saetze", "vorlagen"),
                        ("/admin/einrichtung/neu", "einrichtung")):  # fmt: skip
        assert hilfe_link(admin.get(pfad).text).endswith(f"#{anker}"), pfad
        assert f'id="{anker}"' in handbuch, anker


def test_hilfe_aus_dem_admin_mit_rueckweg(client):
    html = client.get("/hilfe/admin", params={"zurueck": "/admin/v/1/geraete"}).text
    assert 'href="/admin/v/1/geraete" id="zurueck-admin">Zurück zum Admin' in html
    assert 'class="hilfe lang aus-admin"' in html
    html = client.get("/hilfe/admin", params={"zurueck": "https://boese.example/admin"}).text
    assert "boese.example" not in html and 'id="zurueck-admin"' not in html
    kurz = client.get("/hilfe/ziel").text  # Kurzanleitungen der Geräte unverändert
    assert "aus-admin" not in kurz and "history.back()" in kurz
