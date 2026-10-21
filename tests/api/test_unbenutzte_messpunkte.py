"""Messpunkte, die keine Strecke nutzt, haben keinen Gerätelink (Geräte, QR-Blatt,
„Als Nächstes“); ein ungenutzter Standard-Start/-Ziel ohne Zeiten wird automatisch ausgeblendet –
auch direkt nach dem Laden der Strecken im Assistenten. Wird ein Messpunkt wieder genutzt, ist sein
Link wieder da (gleicher Token)."""

import re

from app import strecken as st
from app.models import Klassensatz, Messpunkt, Veranstaltung, Wertungssatz
from tests.api.test_admin_saetze import post, satz
from tests.api.test_einrichtung import admin, veranstaltung  # noqa: F401 (Fixture)
from tests.api.test_orte import orte_speichern

FUENF = """version = 1
name = "5 km"
laenge_km = 5
startort = "Start 5k"
zielort = "Ziel"
messpunkte = []
"""


def zwanzig(punkt="P1"):
    return f"""version = 1
name = "20 km"
laenge_km = 20
startort = "Start 20k"
zielort = "Ziel"
messpunkte = ["{punkt}"]

[[teil]]
name = ""
wdh = 1
[[teil.segment]]
name = "Start→{punkt}"
von = "Start"
nach = "{punkt}"
km = 10
[[teil.segment]]
name = "{punkt}→Ziel"
von = "{punkt}"
nach = "Ziel"
km = 10
"""


def zur_zusammenfassung(admin, v, ks, ws):  # noqa: F811
    """Schritte 2–4 des Assistenten bestätigen (die Zusammenfassung gibt es erst danach)."""
    post(admin, f"/admin/einrichtung/{v.id}/2", klassensatz_id=str(ks), wertungssatz_id=str(ws))
    post(admin, f"/admin/einrichtung/{v.id}/3")
    r = post(admin, f"/admin/v/{v.id}/zielmonitor", weiter="1", seite_an_0="1", seite_dauer_0="10",
             ziel=f"/admin/einrichtung/{v.id}/4")  # fmt: skip
    assert 'id="zusammenfassung"' in r.text


def zeile(html, name):
    return re.search(rf'<tr [^>]*data-ort="{re.escape(name)}"[^>]*>.*?</tr>', html, re.DOTALL)


def test_assistent_strecken_laden_blendet_standard_aus(admin):  # noqa: F811
    post(admin, "/admin/einrichtung/neu", name="Kottenforst", datum="2026-10-10")
    v = veranstaltung(admin)
    ks, ws = satz(admin, Klassensatz, "DRA 2026"), satz(admin, Wertungssatz, "DRA A.1 flach")
    post(admin, f"/admin/einrichtung/{v.id}/2", klassensatz_id=str(ks), wertungssatz_id=str(ws))
    post(admin, f"/admin/v/{v.id}/strecken/uebernehmen", inhalt=FUENF, name="5 km", platz="5 km")
    # „Start“ fährt noch die vorläufige Strecke (20-km-Klassen) → bleibt
    assert zeile(admin.get(f"/admin/v/{v.id}/messpunkte").text, "Start")
    r = post(
        admin,
        f"/admin/v/{v.id}/strecken/uebernehmen",
        inhalt=zwanzig(),
        name="20 km",
        platz="20 km",
    )
    assert "aus der Datei geladen" in r.text
    html = admin.get(f"/admin/v/{v.id}/messpunkte").text
    assert not zeile(html, "Start")  # direkt nach dem Laden ausgeblendet
    assert zeile(html, "Start 5k") and zeile(html, "Start 20k") and zeile(html, "Ziel")
    geraete = admin.get(f"/admin/v/{v.id}/geraete").text
    assert f"/start?token={v.token_start}" not in geraete
    assert "Start Start 5k" in geraete and "Posten P1" in geraete
    zur_zusammenfassung(admin, v, ks, ws)
    assert f"/start?token={v.token_start}" not in admin.get(f"/admin/einrichtung/{v.id}/5").text


def test_ungenutzte_zwischenzeit_ohne_geraetelink(admin):  # noqa: F811
    post(admin, "/admin/einrichtung/neu", name="Kottenforst", datum="2026-10-10")
    v = veranstaltung(admin)
    ks, ws = satz(admin, Klassensatz, "DRA 2026"), satz(admin, Wertungssatz, "DRA A.1 flach")
    post(admin, f"/admin/einrichtung/{v.id}/2", klassensatz_id=str(ks), wertungssatz_id=str(ws))
    post(admin, f"/admin/v/{v.id}/strecken/uebernehmen", inhalt=FUENF, name="5 km", platz="5 km")
    post(
        admin,
        f"/admin/v/{v.id}/strecken/uebernehmen",
        inhalt=zwanzig(),
        name="20 km",
        platz="20 km",
    )
    orte_speichern(admin, v, ("P9", "punkt"))
    with admin.app.state.sessionmaker() as s:
        p9 = next(m for m in st.orte_von(s, s.get(Veranstaltung, v.id)) if m.name == "P9")
        p9_id, p9_token = p9.id, p9.token
    html = admin.get(f"/admin/v/{v.id}/messpunkte").text
    p9_zeile = zeile(html, "P9").group(0)
    assert "von keiner Strecke genutzt" in p9_zeile and "kein Gerätelink" in p9_zeile
    assert "disabled" not in p9_zeile.split("mp_entfernen")[1]  # „Entfernen“ möglich
    assert "von keiner Strecke genutzt" not in zeile(html, "P1").group(0)
    zur_zusammenfassung(admin, v, ks, ws)
    for url in (
        f"/admin/v/{v.id}/geraete",
        f"/admin/v/{v.id}/geraetelinks-druck",
        f"/admin/einrichtung/{v.id}/5",
    ):
        seite = admin.get(url).text
        assert "Posten P9" not in seite and p9_token not in seite, url
        assert "Posten P1" in seite, url
    post(admin, f"/admin/einrichtung/{v.id}/5")  # Einrichtung abschließen → „Als Nächstes“
    uebersicht = admin.get(f"/admin/v/{v.id}").text
    assert "Posten P1 noch nicht verbunden" in uebersicht and "Posten P9" not in uebersicht
    # die 20-km-Strecke führt jetzt an P9 vorbei: Link wieder da, gleicher Token; P1 ohne Link
    with admin.app.state.sessionmaker() as s:
        sid = next(
            x.id for x in st.strecken_von(s, s.get(Veranstaltung, v.id)) if x.name == "20 km"
        )
    post(admin, f"/admin/v/{v.id}/strecken/uebernehmen", inhalt=zwanzig("P9"), modus="ersetzen",
         ersetzen=str(sid), bestaetigt="1")  # fmt: skip
    geraete = admin.get(f"/admin/v/{v.id}/geraete").text
    assert f"/posten?token={p9_token}&amp;ort={p9_id}" in geraete and "Posten P1" not in geraete


def test_standard_mit_zeiten_bleibt(admin):  # noqa: F811
    """Gibt es Zeiten (Regeln eingefroren), wird nichts mehr automatisch ausgeblendet."""
    post(admin, "/admin/einrichtung/neu", name="Kottenforst", datum="2026-10-10")
    v = veranstaltung(admin)
    with admin.app.state.sessionmaker() as s:
        w = s.get(Veranstaltung, v.id)
        orte = st.orte_von(s, w)
        start = next(m for m in orte if m.art == "start")
        w.regeln_json = "{}"
        st.strecken_von(s, w)[0].startort_id = None
        neu_start = Messpunkt(veranstaltung_id=w.id, position=1, name="Nord", art="start")
        s.add(neu_start)
        s.flush()
        st.strecken_von(s, w)[0].startort_id = neu_start.id
        st.unbenutzte_standards_ausblenden(s, w)
        assert not start.entfernt
        w.regeln_json = None
        st.unbenutzte_standards_ausblenden(s, w)
        assert start.entfernt
