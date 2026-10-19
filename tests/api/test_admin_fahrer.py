"""Teilnehmerliste mit Suche/Filtern und Fahrer-Seite (ADMIN-REVIEW Schritt 5)."""

import re

from tests.api import test_admin
from tests.api.test_admin import MIN, T0, _tid, geraet, post

admin, v, keine_sperre = test_admin.admin, test_admin.v, test_admin.keine_sperre  # Fixtures


def nummern(html: str) -> list[int]:
    tabelle = html.split('id="teilnehmer"')[1].split("</table>")[0]
    return [int(n) for n in re.findall(r'<tr class="row" data-nr="(\d+)"', tabelle)]


def test_liste_suche_und_filter(admin, v):
    geraet(admin, v, "START", startnummer=1, zeit_ms=T0)
    geraet(admin, v, "START", startnummer=2, zeit_ms=T0 + MIN)
    geraet(admin, v, "ZIEL", startnummer=1, zeit_ms=T0 + 30 * MIN)
    basis = f"/admin/v/{v.id}/teilnehmer"
    html = admin.get(basis).text
    assert nummern(html) == [1, 2, 3]
    assert f'data-href="{basis}/{_tid(admin, 1)}"' in html and ">Öffnen</a>" in html
    for kennung in ("csv-importieren", "teilnehmer-hinzufuegen", "t-suche", "t-stand"):
        assert f'id="{kennung}"' in html, kennung
    assert nummern(admin.get(basis, params={"q": "ben"}).text) == [2]
    assert nummern(admin.get(basis, params={"q": "RC"}).text) == [1]  # Verein
    assert nummern(admin.get(basis, params={"stand": "gestartet"}).text) == [1, 2]
    assert nummern(admin.get(basis, params={"stand": "strecke"}).text) == [2]
    assert nummern(admin.get(basis, params={"stand": "ziel"}).text) == [1]
    assert nummern(admin.get(basis, params={"stand": "nicht"}).text) == [3]
    html = admin.get(basis, params={"stand": "status"}).text
    assert nummern(html) == [] and "Kein Teilnehmer passt" in html and 'id="filter-aus"' in html
    assert ">im Ziel</span>" in admin.get(basis).text


def test_leere_liste_und_eigene_seiten(admin, v):
    r = post(admin, "/admin/veranstaltungen", name="Leer", datum="2026-11-01")
    vid = int(re.search(r"/admin/v/(\d+)/", r.url.path + "/").group(1))
    html = admin.get(f"/admin/v/{vid}/teilnehmer").text
    assert 'id="keine-teilnehmer"' in html
    assert 'id="neuer-teilnehmer"' in admin.get(f"/admin/v/{vid}/teilnehmer/neu").text
    imp = admin.get(f"/admin/v/{vid}/import").text
    assert 'id="csv-hinweis"' in imp and f'action="/admin/v/{vid}/import"' in imp
    r = admin.post(f"/admin/v/{vid}/import", data={"csrf": admin.csrf},
                   files={"datei": ("x.csv", b"", "text/csv")})  # fmt: skip
    assert r.url.path == f"/admin/v/{vid}/import"  # Fehler bleiben auf der Importseite


def test_fahrer_seite_zeiten_status_verlauf_urkunde(admin, v):
    geraet(admin, v, "START", startnummer=1, zeit_ms=T0)
    z = geraet(admin, v, "ZIEL", startnummer=1, zeit_ms=T0 + 30 * MIN)
    tid = _tid(admin, 1)
    seite = f"/admin/v/{v.id}/teilnehmer/{tid}"
    html = admin.get(seite).text
    assert "<h1>Nr. 1 · Anna A</h1>" in html and ">im Ziel</span>" in html
    assert 'id="zeiten"' in html and 'id="fahrzeit"' in html and "30:00,0" in html
    assert f'data-uuid="{z["uuid"]}"' in html and ">test</td>" in html  # Gerät
    assert f'href="/admin/v/{v.id}/urkunden?nr=1&amp;zurueck={seite}"' in html
    assert f'href="{seite}/bearbeiten"' in html
    for aktion in ("manuell", "umbuchen", "stornieren", "status"):
        assert f'id="aktion-{aktion}"' in html, aktion
    # Korrektur auf der Fahrer-Seite: Rücksprung zur Fahrer-Seite, Verlauf zeigt die Notiz
    r = post(admin, f"/admin/v/{v.id}/korrektur", aktion="stornieren", stempel=z["uuid"],
             notiz="Fehltipp", zurueck=seite)  # fmt: skip
    assert r.url.path == seite and "storniert" in r.text
    html = admin.get(seite).text
    assert 'class="storniert" data-art="ZIEL"' in html and "Fehltipp" in html
    assert ">auf der Strecke</span>" in html and "fahrer-urkunde" not in html
    r = post(admin, f"/admin/v/{v.id}/korrektur", aktion="status", startnummer="1", wert="DNF",
             notiz="Sturz", zurueck=seite)  # fmt: skip
    assert r.url.path == seite and 'id="fahrer-status">DNF<' in r.text
    # Merkmal auf der Fahrer-Seite umschalten
    r = post(admin, f"/admin/v/{v.id}/teilnehmer/{tid}/merkmal", merkmal="ausser_konkurrenz",
             wert="ja", zurueck=seite)  # fmt: skip
    assert r.url.path == seite and "Außer Konkurrenz: <strong>ja</strong>" in r.text


def test_vorlaeufige_dsq_auf_der_fahrer_seite(admin, v):
    geraet(admin, v, "START", startnummer=2, zeit_ms=T0)
    geraet(admin, v, "STATUS", startnummer=2, wert="DSQ_VORLAEUFIG", notiz="Windschatten")
    seite = f"/admin/v/{v.id}/teilnehmer/{_tid(admin, 2)}"
    html = admin.get(seite).text
    assert 'id="fahrer-dsq"' in html and "Windschatten" in html
    r = post(admin, f"/admin/v/{v.id}/dsq", startnummer="2", entscheidung="verwerfen",
             notiz="kein Verstoß", zurueck=seite)  # fmt: skip
    assert r.url.path == seite and 'id="fahrer-dsq"' not in r.text


def test_bearbeiten_und_kein_offener_redirect(admin, v):
    tid = _tid(admin, 3)
    html = admin.get(f"/admin/v/{v.id}/teilnehmer/{tid}/bearbeiten").text
    assert 'id="bearbeiten"' in html and 'id="teilnehmer-loeschen"' in html
    r = post(admin, f"/admin/v/{v.id}/korrektur", aktion="status", startnummer="3", wert="DNS",
             notiz="x", zurueck="https://example.org/admin/")  # fmt: skip
    assert r.url.path == f"/admin/v/{v.id}/aufgaben"
    assert admin.get(f"/admin/v/{v.id}/teilnehmer/999999").status_code == 404
