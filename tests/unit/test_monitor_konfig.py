"""Zielmonitor-Konfiguration (Standard, Auflösung für den Monitor)."""

import json
from types import SimpleNamespace

from app import monitor_konfig as mk
from app import segmente as sg
from app.simulation import beispiel_teile


def strecke(sid=7, name="Rund"):
    return SimpleNamespace(
        id=sid, name=name, teile_json=sg.nach_json(beispiel_teile()), abschnitte_json=None
    )


def test_standard_wie_bisher_und_alter_schalter():
    liste = [strecke()]
    v = SimpleNamespace(segmente_monitor=False, monitor_json=None)
    assert mk.laden(v, liste) == {
        "seiten": [mk.neue_seite("ergebnis")],
        "live_abschnitte": [],
        "live_vergleich": [],
    }
    v.segmente_monitor = True  # früher „Segmentzeiten auf dem Zielmonitor“: alle Segment-Abschnitte
    assert mk.laden(v, liste)["live_abschnitte"] == ["7:s0.0", "7:s1.0", "7:s1.1", "7:s2.0"]


def test_fuer_monitor_nur_eingeschaltete_seiten_mit_strecken_index():
    liste = [strecke(3, "Kurz"), strecke(7, "Lang")]
    konf = {"seiten": [{"typ": "ergebnis", "an": False, "dauer": 5},
                       {"typ": "ergebnis", "an": True, "dauer": 8, "live": False, "abzeichen": True, "strecken": [7]},
                       {"typ": "rangliste", "an": True, "dauer": 15, "abschnitt": "7:s1.0", "top": 5}],
            "live_abschnitte": ["7:s1.0", "9:x"]}  # fmt: skip
    erg = SimpleNamespace(wertung=None)
    aus = mk.fuer_monitor(konf, liste, erg)
    assert aus["seiten"][0] == {
        "typ": "ergebnis",
        "dauer": 8,
        "live": False,
        "abzeichen": False,
        "strecken": [1],
    }
    assert (
        aus["seiten"][1]["strecke"] == 1
        and aus["seiten"][1]["titel"] == "Lang: Kreisel→Berg"
        and aus["seiten"][1]["top"] == 5
    )
    # Name und „mehrere Durchfahrten“ fürs Ergebnis („beste … 9:18“)
    assert aus["live"] == [{"id": "1:s1.0", "strecke": 1, "abschnitt": "s1.0", "vergleich": False,
                            "name": "Kreisel→Berg", "mehrfach": True}]  # fmt: skip
    # nichts eingeschaltet → eine Ergebnisseite
    leer = mk.fuer_monitor({"seiten": [], "live_abschnitte": []}, liste, erg)
    assert [s["typ"] for s in leer["seiten"]] == ["ergebnis"]
    assert json.dumps(aus)  # für die API serialisierbar


class Formular(dict):
    def get(self, name, default=None):
        wert = super().get(name, default)
        return wert[0] if isinstance(wert, list) else wert

    def getlist(self, name):
        wert = super().get(name, [])
        return wert if isinstance(wert, list) else [wert]


def test_kein_pfeil_fuer_nicht_gewaehltes_feld():
    """Der Server speichert „mit Pfeil und Differenz“ nur für gewählte Felder; die
    Vorgabe für das Formular kommt aus dem Vorschlag."""
    liste = [strecke()]
    form = Formular(live_abschnitt=["7:s1.0"], live_vergleich=["7:s1.0", "7:s1.1", "7:b1"])
    konf = mk.aus_formular(form, {"seiten": []}, liste)
    assert konf["live_abschnitte"] == ["7:s1.0"] and konf["live_vergleich"] == ["7:s1.0"]
    v = SimpleNamespace(segmente_monitor=False, monitor_json=None)
    assert mk.kontext(v, liste, False, "/x")["live_vorgabe"] == {"7:s1.0", "7:s1.1", "7:b1"}


def test_beste_abschnittszeit_fuers_ergebnis():
    """Erstes gewähltes Feld der eigenen Strecke mit mehreren Durchfahrten."""
    from app.oeffentlich import beste_abschnittszeit

    zeit = lambda s, ms: SimpleNamespace(schluessel=s, zeit_ms=ms)
    z = SimpleNamespace(strecke=0, abschnittszeiten=[zeit("s1", 300_000), zeit("s1", 241_000),
                                                     zeit("s1", None), zeit("b1", 500_000)])  # fmt: skip
    live = [{"strecke": 1, "abschnitt": "s1", "name": "Andere", "mehrfach": True},
            {"strecke": 0, "abschnitt": "s0", "name": "Start→Kreisel", "mehrfach": False},
            {"strecke": 0, "abschnitt": "s1", "name": "Runde", "mehrfach": True},
            {"strecke": 0, "abschnitt": "b1", "name": "Block", "mehrfach": True}]  # fmt: skip
    assert beste_abschnittszeit(z, live) == {"label": "beste Runde", "zeit": "4:01"}
    assert beste_abschnittszeit(z, live[:2]) is None  # kein Feld mit mehreren Durchfahrten
    assert beste_abschnittszeit(SimpleNamespace(strecke=0, abschnittszeiten=[]), live) is None
