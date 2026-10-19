"""Segmente 62: Live-Bereich des Zielmonitors – je im Admin gewähltem Abschnittsfeld (Segment,
Rundenblock, eigener Abschnitt) die letzte Zeit mit Namen, bei „mit Pfeil und Differenz“ ▼/▲ und
Differenz; /api/oeffentlich nennt die App-Version, /monitor lädt seine Dateien versioniert."""

import json

from app import strecken as st
from app.models import Veranstaltung
from app.seiten import APP_VERSION
from tests.api import test_posten
from tests.api.test_posten import MIN, T0, anmelden, csrf_aus, durch, ev, posten, senden

v = test_posten.v  # Fixture

START = {"Authorization": "Bearer tok-start"}


def test_live_felder_segment_block_eigener_abschnitt(client, v):
    with client.app.state.sessionmaker() as s:
        strecke = st.strecken_von(s, s.get(Veranstaltung, v.id))[0]
        strecke.abschnitte_json = json.dumps({"eigene": [{
            "id": "a1", "name": "Abfahrt", "von": str(v.mp["Brücke"]), "nach": str(v.mp["Kreisel"]),
            "art": "wiederkehrend"}]})  # fmt: skip
        s.commit()
        sid = strecke.id
    # Admin wählt drei Felder, alle „mit Pfeil und Differenz“ (wie über die Oberfläche)
    anmelden(client)
    html = client.get(f"/admin/v/{v.id}/einstellungen/zielmonitor").text
    felder = [f"{sid}:s1.0", f"{sid}:b1", f"{sid}:a1"]
    for f in felder:
        assert f'name="live_abschnitt" value="{f}"' in html
        assert f'name="live_vergleich" value="{f}"' in html
    r = client.post(f"/admin/v/{v.id}/zielmonitor", data={
        "csrf": csrf_aus(html), "aktion": "speichern", "seite_an_0": "1", "seite_dauer_0": "10",
        "seite_live_0": "1", "live_abschnitt": felder, "live_vergleich": felder})  # fmt: skip
    assert "Zielmonitor gespeichert" in r.text
    # Nr. 1: Kreisel 3, Brücke 8, Kreisel 13 (Runde 10 min), Brücke 17, Kreisel 21 (Runde 8 min)
    senden(
        client,
        [ev("START", startnummer=1, zeit_ms=T0), ev("START", startnummer=2, zeit_ms=T0 + MIN)],
        START,
    )
    folge = [("Kreisel", 3), ("Brücke", 8), ("Kreisel", 13), ("Brücke", 17), ("Kreisel", 21)]
    tipps = [durch(v, 1, T0 + m * MIN, name) for name, m in folge]
    senden(client, [t for t in tipps if t["wert"] == str(v.mp["Kreisel"])], posten("kreisel"))
    senden(client, [t for t in tipps if t["wert"] == str(v.mp["Brücke"])], posten("bruecke"))

    r = client.get("/api/oeffentlich")
    assert r.headers["X-App-Version"] == APP_VERSION
    d = r.json()
    assert [x["id"] for x in d["monitor"]["live"]] == ["0:s1.0", "0:b1", "0:a1"]
    assert all(x["vergleich"] for x in d["monitor"]["live"])
    nr1 = next(u for u in d["unterwegs"] if u["startnummer"] == 1)["abschnitte"]
    assert nr1 == {
        "0:s1.0": {"label": "Kreisel→Brücke (2)", "zeit": "4:00", "pfeil": "▼", "diff": "60 s"},
        "0:b1": {"label": "Runde (2)", "zeit": "8:00", "pfeil": "▼", "diff": "120 s"},
        "0:a1": {"label": "Abfahrt (2)", "zeit": "4:00", "pfeil": "▼", "diff": "60 s"},
    }
    # Nr. 2 noch an keinem Messpunkt: Felder leer
    nr2 = next(u for u in d["unterwegs"] if u["startnummer"] == 2)["abschnitte"]
    assert nr2 == {f: None for f in nr1}
    # ohne „mit Pfeil und Differenz“: nur Name und Zeit
    client.post(f"/admin/v/{v.id}/zielmonitor", data={
        "csrf": csrf_aus(html), "aktion": "speichern", "seite_an_0": "1", "seite_dauer_0": "10",
        "seite_live_0": "1", "live_abschnitt": felder})  # fmt: skip
    nr1 = next(
        u for u in client.get("/api/oeffentlich").json()["unterwegs"] if u["startnummer"] == 1
    )
    assert all(
        f["pfeil"] == "" and f["diff"] == "" and f["zeit"] for f in nr1["abschnitte"].values()
    )


def test_monitor_dateien_versioniert(client, v):
    html = client.get("/monitor").text
    assert f'data-version="{APP_VERSION}"' in html
    for datei in ("css/basis.css", "css/oeffentlich.css", "js/monitor.js"):
        assert f"/static/{datei}?v={APP_VERSION}" in html
