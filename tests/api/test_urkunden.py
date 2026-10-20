"""Urkunden als Druckansicht."""

import re

from tests.api import test_oeffentlich
from tests.api.test_oeffentlich import fahrt, senden, szenario, wertung_an

v = test_oeffentlich.v  # Fixture: 8 Fahrer, Nr. 1–7 mit Abzeichen


def anmelden(client):
    client.post("/admin/anmelden", data={"token": "tok-admin"})
    return re.search(r'name="csrf" value="([0-9a-f]+)"', client.get("/admin").text).group(1)


def urkunden(html):
    return re.findall(r'<section class="urkunde" data-nr="(\d+)"', html)


def test_urkunden_alle_und_einzeln(client, v):
    wertung_an(client, v)
    szenario(client)  # 1–4 im Ziel, 5 DNF, 6 DNS, 7 unterwegs, 8 offen
    fahrt(client, 8, 31)
    senden(client, {"art": "STATUS", "startnummer": 4, "wert": "DSQ", "notiz": "x"}, rolle="admin")
    anmelden(client)
    seite = client.get(f"/admin/v/{v.id}/ergebnis").text
    assert f'href="/admin/v/{v.id}/urkunden?nr=1&amp;zurueck=' in seite
    assert f'href="/admin/v/{v.id}/urkunden?zurueck=' in client.get(f"/admin/v/{v.id}/drucken").text
    html = client.get(f"/admin/v/{v.id}/urkunden").text
    assert urkunden(html) == ["1", "2", "3", "8"]  # nicht DNF, DNS, DSQ, unterwegs, offen
    assert "size: A4 landscape" in html and "break-after: page" in html
    erste = html.split('data-nr="1"')[1].split("</section>")[0]
    for teil in (
        "Kottenforst",
        "10.10.2026",
        "Vor1 Nach1",
        "Herren 31-40",
        "20 km",
        "20,0 km",
        "1. Platz",
        "30:00,0",
        "Gold",
        "Veranstalter",
        'class="logo"',
    ):
        assert teil in erste, teil
    assert 'class="wasserzeichen"' in html and "vorläufig" in html  # Ergebnis noch vorläufig
    # Fahrer ohne Abzeichen: keine Stufe
    assert "Gold" not in html.split('data-nr="8"')[1].split("</section>")[0]
    einzeln = client.get(f"/admin/v/{v.id}/urkunden?nr=2").text
    assert urkunden(einzeln) == ["2"]
    assert client.get(f"/admin/v/{v.id}/urkunden?nr=5").status_code == 404  # DNF: keine Urkunde


def test_urkunde_ausser_konkurrenz_und_offiziell(client, v):
    fahrt(client, 1, 30)
    fahrt(client, 2, 31)
    csrf = anmelden(client)
    with client.app.state.sessionmaker() as s:
        from app.models import Teilnehmer

        tid = s.query(Teilnehmer).filter_by(startnummer=1).one().id
    client.post(f"/admin/v/{v.id}/teilnehmer/{tid}/merkmal",
                data={"csrf": csrf, "merkmal": "ausser_konkurrenz", "wert": "ja"})  # fmt: skip
    client.post(f"/admin/v/{v.id}/ergebnisstatus",
                data={"csrf": csrf, "wert": "OFFIZIELL", "bestaetigung": "1", "notiz": "ok"})  # fmt: skip
    html = client.get(f"/admin/v/{v.id}/urkunden").text
    erste = html.split('data-nr="1"')[1].split("</section>")[0]
    assert "außer Konkurrenz" in erste and "Platz" not in erste
    assert "1. Platz" in html.split('data-nr="2"')[1]  # Nr. 2 rückt auf Platz 1
    assert 'class="wasserzeichen"' not in html  # offiziell: kein Wasserzeichen
    assert "Gold" not in html  # Wertung aus
