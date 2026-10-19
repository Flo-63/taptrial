"""Veranstaltungen mit Seitenleiste und Filtern (Test 08.10., Punkt 14)."""

import re
from datetime import date

from app.models import Veranstaltung
from tests.api import test_admin
from tests.api.test_admin import post

admin, v, keine_sperre = test_admin.admin, test_admin.v, test_admin.keine_sperre  # Fixtures


def namen(html: str) -> list[str]:
    tabelle = html.split('id="veranstaltungen"')[1].split("</table>")[0]
    return re.findall(r'class="tbl-link" href="/admin/v/\d+">([^<]+)</a>', tabelle)


def zahl(html: str, filter: str) -> int:
    teil = html.split(f'data-seite="{filter}"')[1].split("</a>")[0]
    return int(re.search(r'<span class="hint">(\d+)</span>', teil).group(1))


def test_filter_als_navigationspunkte(admin, v):
    post(admin, "/admin/veranstaltungen", name="Frühjahr", datum="2027-04-17")
    with admin.app.state.sessionmaker() as s:
        alt = Veranstaltung(name="Alt", datum=date(2020, 5, 1), zustand="abgeschlossen")
        neu = s.query(Veranstaltung).filter_by(name="Frühjahr").one()
        neu.einrichtung_schritt = 2
        s.add(alt)
        s.commit()
    html = admin.get("/admin").text
    assert 'id="neue-veranstaltung"' in html and 'class="btn btn-primary btn-sm sl-knopf"' in html
    assert '<div class="nav-group">Filter</div>' in html
    erwartet = {"alle": 3, "laufend": 1, "einrichtung": 1, "abgeschlossen": 1, "faellig": 1}
    assert {f: zahl(html, f) for f in erwartet} == erwartet
    assert sorted(namen(html)) == ["Alt", "Frühjahr", "Kottenforst"]
    assert namen(admin.get("/admin", params={"filter": "laufend"}).text) == ["Kottenforst"]
    assert namen(admin.get("/admin", params={"filter": "einrichtung"}).text) == ["Frühjahr"]
    seite = admin.get("/admin", params={"filter": "abgeschlossen"}).text
    assert (
        namen(seite) == ["Alt"]
        and 'data-seite="abgeschlossen"\n         aria-current="page"' in seite
    )
    assert namen(admin.get("/admin", params={"filter": "faellig"}).text) == ["Alt"]
    assert sorted(namen(admin.get("/admin", params={"filter": "quatsch"}).text)) == [
        "Alt",
        "Frühjahr",
        "Kottenforst",
    ]
    assert (
        "/admin/v/" not in html.split('id="sl-nav"')[1].split("</nav>")[0]
    )  # kein Wechsel über die Leiste
