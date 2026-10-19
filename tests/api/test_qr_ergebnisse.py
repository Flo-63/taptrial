"""QR-Code zu den Live-Ergebnissen (AUSBAU B11): Zielmonitor-Ecke und A4-Aushang im Admin."""

import re

from tests.api import test_admin
from tests.api.test_admin import post

admin, v, keine_sperre = test_admin.admin, test_admin.v, test_admin.keine_sperre  # Fixtures


def test_monitor_zeigt_qr_auf_ergebnisse(client):
    html = client.get("/monitor").text
    assert 'id="mon-qr"' in html and "<svg" in html and "aufs Handy" in html
    assert (
        'data-url="http://testserver/ergebnisse"' in html
    )  # öffentliche Adresse, keine Veranstaltung


def test_aushang_im_admin(admin, v):
    geraete = admin.get(f"/admin/v/{v.id}/drucken").text
    assert f'href="/admin/v/{v.id}/aushang?zurueck=' in geraete
    html = admin.get(f"/admin/v/{v.id}/aushang").text
    assert "Live-Ergebnisse" in html and "Kottenforst" in html and "10.10.2026" in html
    assert "http://testserver/ergebnisse" in html and "<svg" in html
    assert "?veranstaltung=" not in html
    assert "@page" in html and "A4" in html  # Druck A4
    assert admin.get(f"/admin/v/{v.id}/aushang", follow_redirects=False).status_code == 200
    _ = post, re
