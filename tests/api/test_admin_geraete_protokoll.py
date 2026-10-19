"""Geräte (inkl. Monitor-QR) und Protokoll mit Filtern (ADMIN-REVIEW Schritt 7)."""

import re

from tests.api import test_admin
from tests.api.test_admin import MIN, T0, geraet, post

admin, v, keine_sperre = test_admin.admin, test_admin.v, test_admin.keine_sperre  # Fixtures


def test_geraete_karten_mit_monitor_und_bestaetigung(admin, v):
    html = admin.get(f"/admin/v/{v.id}/geraete").text
    for titel in ("Start", "Ziel", "Rennleitung", "Zielmonitor"):
        assert f'data-titel="{titel}"' in html, titel
    karte = html.split('data-titel="Zielmonitor"')[1].split("</section>")[0]
    assert "http://testserver/monitor" in karte and "<svg" in karte and "token" not in karte
    assert html.count('class="btn btn-secondary btn-sm kopieren"') == 4  # Link kopieren
    assert 'id="uhren"' in html and "Noch keine Messung" in html
    # Token erneuern fragt per Dialog nach (umkehrbar: neuer Link)
    assert re.search(r'<form[^>]*action="/admin/v/\d+/token"[^>]*data-bestaetigen=', html)
    assert 'id="bestaetigung-dialog"' in html


def test_geraete_tabelle_mit_letztem_sync(admin, v):
    admin.get("/api/stand", headers={"Authorization": f"Bearer {v.token_ziel}",
                                     "X-Geraet": "ziel-ab12", "X-Uhr-Abweichung": "900"})  # fmt: skip
    html = admin.get(f"/admin/v/{v.id}/geraete").text
    zeile = html.split('id="uhren"')[1].split("</table>")[0]
    assert "ziel-ab12" in zeile and "Ziel" in zeile and "+0,9 s" in zeile and "b-warn" in zeile


def test_geschlossene_veranstaltung_nennt_grund(admin, v):
    post(admin, f"/admin/v/{v.id}/abschliessen", bestaetigung="1", name="Kottenforst")
    html = admin.get(f"/admin/v/{v.id}/geraete").text
    assert "Gesperrt" in html and 'data-titel="Start"' not in html


def test_protokoll_filter_nummer_und_art(admin, v):
    geraet(admin, v, "START", startnummer=1, zeit_ms=T0)
    geraet(admin, v, "START", startnummer=2, zeit_ms=T0 + MIN)
    z = geraet(admin, v, "ZIEL", startnummer=1, zeit_ms=T0 + 30 * MIN)
    post(admin, f"/admin/v/{v.id}/korrektur", aktion="stornieren", stempel=z["uuid"], notiz="weg")
    basis = f"/admin/v/{v.id}/protokoll"

    def arten(**params):
        html = admin.get(basis, params=params).text.split('id="protokoll"')[1]
        return re.findall(r'data-art="([A-Z_]+)"', html)

    assert arten() == ["START", "START", "ZIEL", "STORNO"]
    assert arten(nr="1") == ["START", "ZIEL", "STORNO"]
    assert arten(art="START") == ["START", "START"]
    assert arten(art="korrekturen") == ["STORNO"]
    assert arten(nr="2", art="ZIEL") == []
    html = admin.get(basis).text
    assert f'href="/admin/v/{v.id}/export/protokoll.csv"' in html
    assert f'href="/admin/v/{v.id}/geraetedatei"' in html  # Gerätedatei einlesen


def test_geraetedatei_eigene_seite(admin, v):
    html = admin.get(f"/admin/v/{v.id}/geraetedatei").text
    assert f'action="/admin/v/{v.id}/geraetedatei"' in html and 'type="file"' in html
    assert f'href="/admin/v/{v.id}/geraetedatei"' in admin.get(f"/admin/v/{v.id}/aufgaben").text


def test_geraete_karten_einheitlich(admin, v):
    """Titel, QR-Code, Buttons, darunter der gekürzte Link mit Tooltip (Test 08.10., Punkt 11)."""
    html = admin.get(f"/admin/v/{v.id}/geraete").text
    karten = html.split('<section class="card geraet"')[1:]
    assert len(karten) == 4
    for karte in karten:
        karte = karte.split("</section>")[0]
        qr, knoepfe, url = (
            karte.index(x) for x in ('class="qr"', "geraet-knoepfe", 'class="url" title="http')
        )
        assert qr < knoepfe < url
