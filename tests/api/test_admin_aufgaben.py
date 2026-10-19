"""Aufgaben statt „Rennen“: Fälle direkt lösbar, Vorschläge für offene Zeiten (Schritt 6)."""

import re

from tests.api import test_admin
from tests.api.test_admin import MIN, T0, geraet, post, stand

admin, v, keine_sperre = test_admin.admin, test_admin.v, test_admin.keine_sperre  # Fixtures


def seite(admin, v):
    return admin.get(f"/admin/v/{v.id}/aufgaben").text


def test_ohne_aufgaben(admin, v):
    html = seite(admin, v)
    assert 'id="keine-aufgaben"' in html and "Keine Konflikte." in html
    assert (
        'id="sonderfaelle"' in html and 'id="tauschen"' in html and 'id="geraetedatei-link"' in html
    )


def test_offene_zeit_mit_vorschlaegen_zuordnen(admin, v):
    for nr in (1, 2, 3):
        geraet(admin, v, "START", startnummer=nr, zeit_ms=T0 + (nr - 1) * MIN)
    geraet(admin, v, "ZIEL", startnummer=1, zeit_ms=T0 + 30 * MIN)  # Median 30 min
    offen = geraet(admin, v, "ZIEL", zeit_ms=T0 + MIN + 30 * MIN)  # passt zu Nr. 2
    html = seite(admin, v)
    karte = html.split(f'data-uuid="{offen["uuid"]}"')[1].split("</article>")[0]
    vorschlaege = re.findall(r'name="vorschlag" value="(\d+)"', karte)
    assert vorschlaege[0] == "2" and set(vorschlaege) == {"2", "3"}
    assert "Nr. 2 · Ben B · 30:00,0 unterwegs" in karte
    r = post(admin, f"/admin/v/{v.id}/korrektur", aktion="umbuchen", stempel=offen["uuid"],
             zuordnen="1", startnummer="", notiz="Nummer verdeckt", zurueck=f"/admin/v/{v.id}/aufgaben")  # fmt: skip
    assert "Bitte einen Fahrer wählen" in r.text
    r = post(admin, f"/admin/v/{v.id}/korrektur", aktion="umbuchen", stempel=offen["uuid"],
             zuordnen="1", startnummer="", vorschlag="2", notiz="Nummer verdeckt",
             zurueck=f"/admin/v/{v.id}/aufgaben")  # fmt: skip
    assert r.url.path == f"/admin/v/{v.id}/aufgaben" and "umgebucht" in r.text
    assert sorted(stand(admin, v)["im_ziel"]) == [1, 2]
    assert 'id="keine-aufgaben"' in r.text


def test_doppelstempel_und_unbekannte_nummer(admin, v):
    geraet(admin, v, "START", startnummer=3, zeit_ms=T0)
    a = geraet(admin, v, "ZIEL", startnummer=3, zeit_ms=T0 + 20 * MIN)
    b = geraet(admin, v, "ZIEL", startnummer=3, zeit_ms=T0 + 20 * MIN + 400)
    fremd = geraet(admin, v, "ZIEL", startnummer=77, zeit_ms=T0 + 25 * MIN)
    html = seite(admin, v)
    assert 'data-fall="mehrere_ziele" data-nr="3"' in html and "Nr. 3: 2 Zielstempel" in html
    assert f'name="stempel" value="{b["uuid"]}"' in html and "Fahrer öffnen" in html
    assert 'data-fall="unbekannte_startnummer" data-nr="77"' in html
    post(admin, f"/admin/v/{v.id}/korrektur", aktion="stornieren", stempel=b["uuid"],
         notiz="Doppelstempel")  # fmt: skip
    post(admin, f"/admin/v/{v.id}/korrektur", aktion="umbuchen", stempel=fremd["uuid"],
         startnummer="2", notiz="7 statt 2")  # fmt: skip
    html = seite(admin, v)
    assert "mehrere_ziele" not in html and "unbekannte_startnummer" not in html
    assert a["uuid"] not in html  # gelöst


def test_vorlaeufige_dsq_und_hinweis(admin, v):
    geraet(admin, v, "START", startnummer=1, zeit_ms=T0)
    geraet(admin, v, "STATUS", startnummer=1, wert="DSQ_VORLAEUFIG", notiz="Windschatten")
    html = seite(admin, v)
    assert "Vorläufige DSQ (1)" in html and 'class="filterleiste dsq-form" data-nr="1"' in html
    assert "Windschatten" in html
