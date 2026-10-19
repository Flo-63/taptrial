"""Segmente 47: „Ergebnis offiziell erklären“ erst, wenn alle gestarteten Fahrer eine Zielzeit
oder einen Status (DNF/DSQ) haben."""

import re

from tests.api import test_admin
from tests.api.test_admin import MIN, T0, geraet, korr, post

admin, v, keine_sperre = test_admin.admin, test_admin.v, test_admin.keine_sperre  # Fixtures


def test_offiziell_erst_wenn_alle_durch(admin, v):
    for nr in (1, 2, 3):
        geraet(admin, v, "START", startnummer=nr, zeit_ms=T0 + nr * MIN)
    geraet(admin, v, "ZIEL", startnummer=1, zeit_ms=T0 + 21 * MIN)
    korr(admin, v, "status", startnummer="2", wert="DNF", notiz="Defekt")
    # Nr. 3 noch unterwegs: kein „Als Nächstes“, Button gesperrt mit Grund, Server lehnt ab
    assert 'data-aufgabe="offiziell"' not in admin.get(f"/admin/v/{v.id}").text
    seite = admin.get(f"/admin/v/{v.id}/ergebnis").text
    assert "Noch 1 Fahrer auf der Strecke" in seite
    assert re.search(r'id="offiziell-erklaeren" disabled', seite)
    assert f'href="/admin/v/{v.id}/teilnehmer?stand=strecke"' in seite
    liste = admin.get(f"/admin/v/{v.id}/teilnehmer?stand=strecke").text
    assert "Carl" in liste and "Anna" not in liste
    r = post(admin, f"/admin/v/{v.id}/ergebnisstatus", wert="OFFIZIELL", bestaetigung="1", notiz="")
    assert "Noch 1 Fahrer auf der Strecke" in r.text and "Vorläufiges Ergebnis" in r.text
    # DSQ für Nr. 3 – jetzt sind alle durch
    korr(admin, v, "status", startnummer="3", wert="DSQ", notiz="Abkürzung")
    assert 'data-aufgabe="offiziell"' in admin.get(f"/admin/v/{v.id}").text
    assert 'id="offiziell-erklaeren" disabled' not in admin.get(f"/admin/v/{v.id}/ergebnis").text
    r = post(admin, f"/admin/v/{v.id}/ergebnisstatus", wert="OFFIZIELL", bestaetigung="1", notiz="")
    assert "Offizielles Ergebnis" in r.text
