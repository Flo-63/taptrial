"""Übersicht mit Kennzahlen und „Als Nächstes“."""

import re
from datetime import date

from sqlalchemy import select

from app.models import Strecke, Veranstaltung
from tests.api import test_admin
from tests.api.test_admin import MIN, T0, geraet, post

admin, v, keine_sperre = test_admin.admin, test_admin.v, test_admin.keine_sperre  # Fixtures


def aufgaben(html: str) -> dict[str, str]:
    """data-aufgabe → Ziel des Buttons."""
    return dict(re.findall(r'data-aufgabe="([a-z-]+)".*?<a class="btn[^"]*" href="([^"]+)"', html,
                           re.DOTALL))  # fmt: skip


def uebersicht(admin, v) -> str:
    return admin.get(f"/admin/v/{v.id}").text


def test_kennzahlen_mit_buttons(admin, v):
    html = uebersicht(admin, v)
    for kpi, ziel in (("teilnehmer", "/teilnehmer"), ("gestartet", "/teilnehmer?stand=gestartet"),
                      ("strecke", "/teilnehmer?stand=strecke"), ("ziel", "/teilnehmer?stand=ziel"),
                      ("aufgaben", "/aufgaben")):  # fmt: skip
        assert f'id="kpi-{kpi}" href="/admin/v/{v.id}{ziel}"' in html, kpi
    assert re.search(r'id="kpi-teilnehmer"[^>]*><strong>3</strong>', html)
    assert 'id="naechstes"' in html and 'id="stand"' in html


def test_aufgaben_aus_dem_zustand(admin, v):
    html = uebersicht(admin, v)
    a = aufgaben(html)
    # „Strecke 1: Länge fehlt“ führt direkt auf die Seite der Strecke
    assert re.fullmatch(rf"/admin/v/{v.id}/strecken/\d+", a["laenge"])
    assert (
        'id="zielmonitor-einstellen"' in html
        and f"/admin/v/{v.id}/einstellungen/zielmonitor#zielmonitor" in html
    )
    assert 'id="zielmonitor-oeffnen"' in html
    assert a["geraet-start"] == a["geraet-ziel"] == f"/admin/v/{v.id}/geraete"
    assert "aktiv" not in a and "teilnehmer" not in a and "offiziell" not in a
    # Geräte verbinden, eins mit falscher Uhr; eine Zeit ohne Nummer (offene Aufgabe)
    for token, kennung, ms in (
        (v.token_start, "start-aa", "100"),
        (v.token_ziel, "ziel-bb", "900"),
    ):
        admin.get("/api/stand", headers={"Authorization": f"Bearer {token}", "X-Geraet": kennung,
                                         "X-Uhr-Abweichung": ms})  # fmt: skip
    geraet(admin, v, "START", startnummer=1, zeit_ms=T0)
    geraet(admin, v, "ZIEL", zeit_ms=T0 + 30 * MIN)
    html = uebersicht(admin, v)
    a = aufgaben(html)
    assert "geraet-start" not in a and "geraet-ziel" not in a
    assert a["uhr"] == f"/admin/v/{v.id}/geraete" and "ziel-bb" in html and "0,9 s" in html
    assert a["aufgaben"] == f"/admin/v/{v.id}/aufgaben" and "1 offene Aufgabe" in html


def test_ohne_teilnehmer_inaktiv_und_einrichtung(admin, v):
    r = post(admin, "/admin/veranstaltungen", name="Leer", datum="2026-11-01")
    vid = int(re.search(r"/admin/v/(\d+)/", r.url.path + "/").group(1))
    with admin.app.state.sessionmaker() as s:
        x = s.get(Veranstaltung, vid)
        x.einrichtung_schritt = 3
        s.commit()
    a = aufgaben(admin.get(f"/admin/v/{vid}").text)
    assert a["teilnehmer"] == f"/admin/v/{vid}/teilnehmer"
    assert a["aktiv"] == f"/admin/v/{vid}/status"
    assert a["einrichtung"] == f"/admin/einrichtung/{vid}/3"


def test_offiziell_abschliessen_anonymisieren(admin, v):
    with admin.app.state.sessionmaker() as s:
        for strecke in s.scalars(select(Strecke).where(Strecke.veranstaltung_id == v.id)):
            strecke.laenge_km = 20.0
        s.commit()
    geraet(admin, v, "START", startnummer=1, zeit_ms=T0)
    geraet(admin, v, "ZIEL", startnummer=1, zeit_ms=T0 + 30 * MIN)
    a = aufgaben(uebersicht(admin, v))
    assert a["offiziell"] == f"/admin/v/{v.id}/ergebnis" and "laenge" not in a
    post(admin, f"/admin/v/{v.id}/ergebnisstatus", wert="OFFIZIELL", bestaetigung="1")
    a = aufgaben(uebersicht(admin, v))
    assert "offiziell" not in a and a["abschliessen"] == f"/admin/v/{v.id}/status"
    post(admin, f"/admin/v/{v.id}/abschliessen", bestaetigung="1", name="Kottenforst")
    with admin.app.state.sessionmaker() as s:
        s.get(Veranstaltung, v.id).anonymisieren_ab = date(2000, 1, 1)
        s.commit()
    a = aufgaben(uebersicht(admin, v))
    assert a == {"anonymisieren": f"/admin/v/{v.id}/status"}
