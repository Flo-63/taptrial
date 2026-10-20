"""Start- und Zielprotokoll auf Papier."""

import re

from tests.api import test_admin

admin, v, keine_sperre = test_admin.admin, test_admin.v, test_admin.keine_sperre  # Fixtures


def test_papierprotokoll(admin, v):
    assert (
        f'href="/admin/v/{v.id}/papierprotokoll?zurueck='
        in admin.get(f"/admin/v/{v.id}/drucken").text
    )
    html = admin.get(f"/admin/v/{v.id}/papierprotokoll").text
    assert "Start- und Zielprotokoll" in html and "Kottenforst" in html and "10.10.2026" in html
    kopf = re.search(r"<thead>(.*?)</thead>", html, re.DOTALL).group(1)
    for spalte in (
        "Nr.",
        "Name",
        "Klasse",
        "Strecke",
        "Uhrzeit Start",
        "Uhrzeit Ziel",
        "Bemerkung",
    ):
        assert spalte in kopf, spalte
    zeilen = re.findall(r'<tr class="(fahrer|leer)"(?: data-nr="(\d+)")?', html)
    assert [nr for art, nr in zeilen if art == "fahrer"] == ["1", "2", "3"]  # nach Startnummer
    assert [art for art, _ in zeilen][-10:] == ["leer"] * 10  # 10 leere Zeilen für Nachmeldungen
    assert "Anna A" in html and "Damen 31-40" in html and "Strecke 1" in html
    assert "Nachtragen im Admin als manuelle Zeit" in html
    assert "size: A4 portrait" in html and "counter(page)" in html  # A4 hochkant, Seitenzahl
