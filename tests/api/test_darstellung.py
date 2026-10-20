"""„Anzeigen“ und „Vergleich“ raus aus der Strecke – Spalten unter „Öffentlichkeit &
Zielmonitor“, Vergleich je Zielmonitor-Feld; Ergebnisse, Druck, CSV und Monitor wirken wie vorher."""

import json
import re

from app import segmente
from app import strecken as st
from app.models import Messpunkt, Strecke, StreckeKlasse, Veranstaltung
from app.simulation import beispiel_teile
from tests.api import test_posten
from tests.api.test_posten import MIN, T0, anmelden, csrf_aus, durch, ev, posten, senden

v = test_posten.v  # Fixture

START = {"Authorization": "Bearer tok-start"}


def unterwegs_mit_runden(client, v):
    """Nr. 1 auf der Strecke: Runde 1 in 10 min, Runde 2 in 9 min (▼ 60 s), noch nicht im Ziel."""
    senden(client, [ev("START", startnummer=1, zeit_ms=T0)], START)
    folge = [("Kreisel", 3), ("Brücke", 8), ("Kreisel", 13), ("Brücke", 17), ("Kreisel", 22)]
    tipps = [durch(v, 1, T0 + m * MIN, name) for name, m in folge]
    senden(client, [t for t in tipps if t["wert"] == str(v.mp["Kreisel"])], posten("kreisel"))
    senden(client, [t for t in tipps if t["wert"] == str(v.mp["Brücke"])], posten("bruecke"))


def live(client):
    d = client.get("/api/oeffentlich").json()
    return next(z for z in d["unterwegs"] if z["startnummer"] == 1)["abschnitte"]


def ausgaben(client):
    return (client.get("/ergebnisse").text, client.get("/ergebnisse/druck").text,
            client.get("/ergebnisse.csv").text)  # fmt: skip


def test_vorher_gleich_nachher_und_vergleich_je_feld(client, v):
    with client.app.state.sessionmaker() as s:
        x = s.get(Veranstaltung, v.id)
        strecke = st.strecken_von(s, x)[0]
        sid = strecke.id
        # Älterer Stand: „Kreisel→Ziel“ ausgeblendet; Vergleich an der Strecke (Rundenblock
        # „Runde“ standardmäßig an); Live-Felder ohne „live_vergleich“
        strecke.abschnitte_json = json.dumps({"segmente": {"2.0": {"aus": True}}})
        x.zwischenzeiten_oeffentlich = True
        x.monitor_json = json.dumps({"seiten": [{"typ": "ergebnis", "an": True, "dauer": 10, "live": True,
                                                 "abzeichen": True, "strecken": []}],
                                     "live_abschnitte": [f"{sid}:b1", f"{sid}:s0.0"]})  # fmt: skip
        s.commit()
    unterwegs_mit_runden(client, v)
    vorher = live(client)
    assert vorher["0:b1"]["pfeil"] == "▼" and vorher["0:b1"]["diff"] == "60 s"
    assert vorher["0:s0.0"]["pfeil"] == ""  # Start→Kreisel: einmalig, ohne Vergleich
    alt = ausgaben(client)
    kopf = next(zeile for zeile in alt[2].splitlines() if "Start→Kreisel" in zeile)  # Spaltenköpfe
    assert "Kreisel→Ziel" not in kopf and "Runde" in kopf

    # Admin: Zielmonitor-Einstellungen zeigen „mit Pfeil und Differenz“ nur bei „jede Durchfahrt“
    anmelden(client)
    html = client.get(f"/admin/v/{v.id}/einstellungen/zielmonitor").text
    assert re.search(rf'name="live_vergleich" value="{sid}:b1" checked', html)
    assert f'name="live_vergleich" value="{sid}:s0.0"' not in html
    # Strecken-Editor ohne „anzeigen“/„Vergleich“; Speichern ändert die Darstellung nicht
    seite = client.get(f"/admin/v/{v.id}/strecken/{sid}").text
    assert (
        "sg_anzeigen" not in seite
        and "ab_vergleich" not in seite
        and "<th>Vergleich</th>" not in seite
    )
    r = client.post(f"/admin/v/{v.id}/strecken/{sid}", data={
        "csrf": csrf_aus(seite), "name": "Strecke 1", "laenge": "",
        "verlauf": json.dumps({"punkte": ["Kreisel", {"block": "Runde", "wdh": 2, "punkte": ["Brücke", "Kreisel"]}]}),
        "sg_name_0": "", "ab_anzahl": "0"})  # fmt: skip
    assert "Strecke gespeichert" in r.text
    # Zielmonitor speichern (gleiche Auswahl) → gleiche Anzeige wie vorher
    r = client.post(f"/admin/v/{v.id}/zielmonitor", data={
        "csrf": csrf_aus(html), "aktion": "speichern", "seite_an_0": "1", "seite_dauer_0": "10",
        "seite_live_0": "1", "seite_abzeichen_0": "1",
        "live_abschnitt": [f"{sid}:b1", f"{sid}:s0.0"], "live_vergleich": [f"{sid}:b1"]})  # fmt: skip
    with client.app.state.sessionmaker() as s:
        konf = json.loads(s.get(Veranstaltung, v.id).monitor_json)
    assert konf["live_vergleich"] == [f"{sid}:b1"]
    assert live(client) == vorher and ausgaben(client)[2] == alt[2]
    assert ausgaben(client)[0].count("Kreisel→Ziel") == alt[0].count("Kreisel→Ziel")
    # Feld ohne „mit Pfeil und Differenz“: nur die Zeit
    client.post(f"/admin/v/{v.id}/zielmonitor", data={
        "csrf": csrf_aus(html), "aktion": "speichern", "seite_an_0": "1", "seite_dauer_0": "10",
        "seite_live_0": "1", "live_abschnitt": [f"{sid}:b1"]})  # fmt: skip
    assert (
        live(client)["0:b1"]["pfeil"] == ""
        and live(client)["0:b1"]["zeit"] == vorher["0:b1"]["zeit"]
    )


def test_spalten_je_strecke_ein_speichern(client, v):
    with client.app.state.sessionmaker() as s:
        x = s.get(Veranstaltung, v.id)
        kreisel = s.get(Messpunkt, v.mp["Kreisel"])
        zweite = Strecke(veranstaltung_id=x.id, position=1, name="Kurz",
                         teile_json=segmente.nach_json(beispiel_teile({"P1": str(kreisel.id), "P2": str(v.mp["Brücke"])})),
                         klassen=[StreckeKlasse(klasse="Offen")])  # fmt: skip
        s.add(zweite)
        s.commit()
        ids = [y.id for y in st.strecken_von(s, x)]
    anmelden(client)
    html = client.get(f"/admin/v/{v.id}/einstellungen/zielmonitor").text
    assert (
        'id="spalten-strecke"' in html
        and html.count('class="tbl-box tbl-box-kurz spalten-strecke"') == 2
    )
    # Strecke 1: alles außer „Runde“; Kurz: nur „Runde“ – ein Speichern für beide
    with client.app.state.sessionmaker() as s:
        alle = {i: st.abschnitte_von(s.get(Strecke, i)) for i in ids}
    anzeigen = [f"{ids[0]}:{a.id}" for a in alle[ids[0]] if a.name != "Runde"] + [
        f"{ids[1]}:{a.id}" for a in alle[ids[1]] if a.name == "Runde"]  # fmt: skip
    r = client.post(f"/admin/v/{v.id}/spalten", data={"csrf": csrf_aus(html), "spalten_strecke": [str(i) for i in ids],
                                                      "anzeigen": anzeigen})  # fmt: skip
    assert "Ergebnistabelle gespeichert" in r.text
    with client.app.state.sessionmaker() as s:
        sichtbar = {
            i: [a.name for a in st.abschnitte_von(s.get(Strecke, i)) if not a.aus] for i in ids
        }
    assert "Runde" not in sichtbar[ids[0]] and len(sichtbar[ids[0]]) == len(alle[ids[0]]) - 1
    assert sichtbar[ids[1]] == ["Runde"]
