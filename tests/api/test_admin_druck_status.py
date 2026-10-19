"""Ergebnis, Drucken & Export (mit QR-Blatt), Status & Datenschutz, Druckleiste (Schritt 8)."""

import pytest

from tests.api import test_admin
from tests.api.test_admin import post

admin, v, keine_sperre = test_admin.admin, test_admin.v, test_admin.keine_sperre  # Fixtures


@pytest.mark.parametrize("pfad", ["papierprotokoll", "aushang", "urkunden", "geraetelinks-druck"])
def test_druckansichten_mit_leiste(admin, v, pfad):
    html = admin.get(f"/admin/v/{v.id}/{pfad}").text
    assert 'class="druckleiste nicht-drucken"' in html and 'id="drucken"' in html
    assert 'id="zurueck-admin">Zurück zum Admin' in html
    assert f'href="/admin/v/{v.id}/drucken" id="zurueck-admin"' in html  # Standard
    html = admin.get(
        f"/admin/v/{v.id}/{pfad}", params={"zurueck": f"/admin/v/{v.id}/ergebnis"}
    ).text
    assert f'href="/admin/v/{v.id}/ergebnis" id="zurueck-admin"' in html
    html = admin.get(
        f"/admin/v/{v.id}/{pfad}", params={"zurueck": "https://example.org/admin/x"}
    ).text
    assert "example.org" not in html  # kein offener Redirect


def test_ergebnisliste_druck_zurueck(admin, v):
    html = admin.get("/ergebnisse/druck", params={"veranstaltung": v.id,
                                                 "zurueck": f"/admin/v/{v.id}/drucken"}).text  # fmt: skip
    assert f'href="/admin/v/{v.id}/drucken" id="zurueck-admin">Zurück zum Admin' in html
    html = admin.get("/ergebnisse/druck").text
    assert 'href="/ergebnisse" id="zurueck-admin">Zurück zu den Ergebnissen' in html


def test_geraetelinks_blatt(admin, v):
    html = admin.get(f"/admin/v/{v.id}/geraetelinks-druck").text
    assert html.count("<svg") == 4 and f"token={v.token_start}" in html
    assert "nicht öffentlich aushängen" in html and 'data-titel="Zielmonitor"' in html
    drucken = admin.get(f"/admin/v/{v.id}/drucken").text
    assert f'href="/admin/v/{v.id}/geraetelinks-druck?zurueck=' in drucken
    assert 'target="_blank"' not in drucken.split("<main")[1]  # gleicher Tab


def test_status_lebenszyklus_und_gefahrenzone(admin, v):
    html = admin.get(f"/admin/v/{v.id}/status").text
    assert 'class="aktuell" data-schritt="2">2 · Läuft' in html
    for kennung in ("abschliessen", "anonymisieren", "loeschen"):
        teil = html.split(f'id="{kennung}-zeile"')[1].split('class="dz-row"')[0]
        assert 'name="name" required' in teil, kennung
    assert "Gesperrt: erst nach dem Abschließen möglich." in html
    r = post(admin, f"/admin/v/{v.id}/abschliessen", bestaetigung="1", name="falsch")
    assert "Namen der Veranstaltung" in r.text and r.url.path == f"/admin/v/{v.id}/status"
    r = post(admin, f"/admin/v/{v.id}/abschliessen", bestaetigung="1", name="Kottenforst")
    assert "abgeschlossen" in r.text and 'class="aktuell" data-schritt="3"' in r.text


def test_offiziell_mit_bestaetigungsdialog(admin, v):
    html = admin.get(f"/admin/v/{v.id}/ergebnis").text
    assert (
        'data-bestaetigen="Ergebnis offiziell erklären?' in html
        and 'id="offiziell-erklaeren"' in html
    )
