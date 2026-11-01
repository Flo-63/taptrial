"""Mehrere Geräte an einem Messpunkt: Ziel mit zwei Zielgeräten, Posten-Ziel mit zwei Posten."""

import re

from tests.api import test_admin, test_ziel_zwischenzeit
from tests.api.test_admin import MIN, T0, geraet, post, stand
from tests.api.test_ziel_zwischenzeit import P2, START, durch, ergebnis, ev, senden

admin, v = test_admin.admin, test_admin.v  # Fixtures
zm = test_ziel_zwischenzeit.zm  # Fixture


def test_zwei_zielgeraete_konflikt_kachel_bleibt_weg_diese_zeit_gilt(admin, v):
    geraet(admin, v, "START", startnummer=3, zeit_ms=T0)
    a = geraet(admin, v, "ZIEL", startnummer=3, zeit_ms=T0 + 20 * MIN, geraet="ziel-a")
    b = geraet(admin, v, "ZIEL", startnummer=3, zeit_ms=T0 + 20 * MIN + 300, geraet="ziel-b")
    daten = stand(admin, v)
    nr3 = next(t for t in daten["teilnehmer"] if t["startnummer"] == 3)
    assert nr3["status"] == "KONFLIKT" and nr3["ziel_ms"] is None
    assert 3 in daten["im_ziel"]  # Geräte: Kachel taucht bei offenem Konflikt nicht wieder auf
    html = admin.get(f"/admin/v/{v.id}/aufgaben").text
    assert 'data-fall="mehrere_ziele" data-nr="3"' in html
    knoepfe = re.findall(r'name="stempel" value="([0-9a-f-]+)"[^>]*>[^<]*diese Zeit gilt', html)
    assert knoepfe == [a["uuid"], b["uuid"]]
    assert "Abstand 0,3 s" in html
    r = post(admin, f"/admin/v/{v.id}/korrektur", aktion="gilt", stempel=a["uuid"],
             notiz="zwei Zielgeräte")  # fmt: skip
    assert "gilt" in r.text
    nr3 = next(t for t in stand(admin, v)["teilnehmer"] if t["startnummer"] == 3)
    assert nr3["status"] == "OK" and nr3["ziel_ms"] == T0 + 20 * MIN
    assert "mehrere_ziele" not in admin.get(f"/admin/v/{v.id}/aufgaben").text


def test_zwei_posten_am_ziel_messpunkt(client, zm):
    """5 km enden an P2: zwei Posten-Geräte tippen Nr. 2 – der frühere Tipp ist die Zielzeit, der
    zweite ein Doppeltipp (anderes Gerät). Ein später dritter Tipp ergibt den Konflikt, die Kachel
    bleibt trotzdem erledigt."""
    senden(client, [ev("START", startnummer=2, zeit_ms=T0 + MIN)], START)
    erster = durch(zm, 2, T0 + 8 * MIN, "P2", geraet="posten-a")
    zweiter = durch(zm, 2, T0 + 8 * MIN + 400, "P2", geraet="posten-b")
    senden(client, [erster, zweiter], P2)
    erg = ergebnis(client, zm)
    assert erg.zeile(2).ziel_ms == T0 + 8 * MIN and erg.konflikte == []
    assert erg.nicht_gezaehlt == {zweiter["uuid"]: "Doppeltipp (anderes Gerät)"}
    senden(client, [durch(zm, 2, T0 + 20 * MIN, "P2", geraet="posten-b")], P2)
    erg = ergebnis(client, zm)
    assert [k.art for k in erg.konflikte] == ["MEHRERE_ZIELE"]
    daten = client.get("/api/stand", headers=P2).json()
    nr2 = next(t for t in daten["teilnehmer"] if t["startnummer"] == 2)
    assert nr2["naechste_runde"][zm.mp["P2"]] == 2 and 2 in daten["im_ziel"]
