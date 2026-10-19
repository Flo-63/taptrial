"""Zielmonitor konfigurierbar (Segmente 14): Seiten, Reihenfolge, Anzeigedauer, Live-Bereich.

Konfiguration je Veranstaltung in `veranstaltung.monitor_json`:

    {"seiten": [{"typ": "ergebnis", "an": true, "dauer": 10, "live": true,
                 "abzeichen": true, "strecken": [<Strecken-ID>, …]}, …],
     "live_abschnitte": ["<Strecken-ID>:<Abschnitt-ID>", …],
     "live_vergleich": [<Teilmenge von live_abschnitte: „mit Pfeil und Differenz“, Segmente 52>]}

Seitentypen: „ergebnis“ (Ergebnisbereich mit internem Blättern; „dauer“ je Unterseite) und
„rangliste“ (Rangliste eines Abschnitts, Segmente 15). „strecken“ leer = alle Strecken. Die
Standardkonfiguration entspricht dem bisherigen Monitor; der frühere Schalter „Segmentzeiten auf
dem Zielmonitor“ wird zur Auswahl aller sichtbaren Segment-Abschnitte im Live-Bereich.
"""

import json

from app import strecken as st

SEITENTYPEN = {"ergebnis": "Ergebnis", "rangliste": "Rangliste Abschnitt"}
STANDARD_DAUER = {"ergebnis": 10, "rangliste": 15}
DAUER_GRENZEN = (3, 600)
TOP_STANDARD = 10


class KonfigFehler(ValueError):
    pass


def neue_seite(typ: str) -> dict:
    if typ not in SEITENTYPEN:
        raise KonfigFehler("Unbekannter Seitentyp.")
    seite = {"typ": typ, "an": True, "dauer": STANDARD_DAUER[typ], "live": True}
    if typ == "ergebnis":
        seite |= {"abzeichen": True, "strecken": []}
    else:
        seite |= {"abschnitt": "", "top": TOP_STANDARD}
    return seite


def standard(v, liste) -> dict:
    """Heutiges Verhalten: eine Ergebnisseite mit Live-Bereich; Abschnittsfelder nur, wenn der alte
    Schalter „Segmentzeiten auf dem Zielmonitor“ an war (dann alle sichtbaren Segment-Abschnitte)."""
    live = []
    if getattr(v, "segmente_monitor", False):
        live = [
            f"{s.id}:{a.id}" for s in liste for a in st.abschnitte_von(s) if a.segment and not a.aus
        ]
    return {"seiten": [neue_seite("ergebnis")], "live_abschnitte": live}


def vorschlag(liste) -> dict:
    """Segmente 66: Vorgabe im Assistenten – Ergebnisseite an, Live-Bereich an, je sichtbarem
    Abschnitt (Segmente, Rundenblöcke, eigene) ein Feld; „mit Pfeil und Differenz“ bei Abschnitten
    mit mehreren Durchfahrten (A172)."""
    from app import segmente as sg

    live, mit = [], []
    for s in liste:
        teile = st.teile_von(s)
        for a in st.abschnitte_von(s):
            if a.aus:
                continue
            key = f"{s.id}:{a.id}"
            live.append(key)
            if a.art == "wiederkehrend" and len(sg.paare(a, teile)) > 1:
                mit.append(key)
    return {"seiten": [neue_seite("ergebnis")], "live_abschnitte": live, "live_vergleich": mit}


def kontext(v, liste, mit_wertung: bool, ziel: str, konf: dict | None = None) -> dict:
    """Bausteine des Formulars „_zielmonitor_form.html“ – eine Quelle für Reiter und Assistent."""
    return {"konf": konf or laden(v, liste), "strecken": liste, "abschnitte": abschnitt_auswahl(liste),
            "live_wiederkehrend": wiederkehrend(liste), "SEITENTYPEN": SEITENTYPEN,
            "mit_wertung": mit_wertung, "monitor_ziel": ziel,
            "live_vorgabe": set(vorschlag(liste)["live_vergleich"])}  # fmt: skip


def _bisheriger_vergleich(liste) -> set[str]:
    """Abschnitte mit dem früheren Streckenschalter „Vergleich“ (vor Segmente 52) – daraus wird für
    ältere Konfigurationen „mit Pfeil und Differenz“ der Live-Felder (Migration vorher = nachher)."""
    return {f"{s.id}:{a.id}" for s in liste for a in st.abschnitte_von(s) if a.vergleich}


def laden(v, liste) -> dict:
    konf = json.loads(v.monitor_json) if v.monitor_json else standard(v, liste)
    konf.setdefault("seiten", [])
    konf.setdefault("live_abschnitte", [])
    if "live_vergleich" not in konf:
        alt = _bisheriger_vergleich(liste)
        konf["live_vergleich"] = [k for k in konf["live_abschnitte"] if k in alt]
    return konf


def wiederkehrend(liste) -> set[str]:
    """Abschnitte mit einer Zeit je Durchfahrt – nur dort gibt es „mit Pfeil und Differenz“."""
    return {
        f"{s.id}:{a.id}" for s in liste for a in st.abschnitte_von(s) if a.art == "wiederkehrend"
    }


def abschnitt_auswahl(liste) -> list[tuple[str, str]]:
    """Alle sichtbaren Abschnitte als (Schlüssel „Strecken-ID:Abschnitt-ID“, Anzeigename)."""
    mehrere = len([s for s in liste if st.teile_von(s)]) > 1
    return [(f"{s.id}:{a.id}", f"{s.name}: {a.name}" if mehrere else a.name)
            for s in liste for a in st.abschnitte_von(s) if not a.aus]  # fmt: skip


def aus_formular(form, konf: dict, liste) -> dict:
    """Formular der Admin-Seite „Zielmonitor“ → Konfiguration (Felder seite_<feld>_<i>)."""
    feld = lambda name: str(form.get(name) or "")
    ids = {str(s.id) for s in liste}
    erlaubt = {k for k, _ in abschnitt_auswahl(liste)}
    seiten = []
    for i, alt in enumerate(konf["seiten"]):
        typ = alt.get("typ", "ergebnis")
        dauer = feld(f"seite_dauer_{i}") or str(alt.get("dauer", STANDARD_DAUER.get(typ, 10)))
        if not dauer.isdigit() or not DAUER_GRENZEN[0] <= int(dauer) <= DAUER_GRENZEN[1]:
            raise KonfigFehler(
                f"Seite {i + 1}: Anzeigedauer {DAUER_GRENZEN[0]} bis {DAUER_GRENZEN[1]} Sekunden."
            )
        seite = {"typ": typ, "an": bool(feld(f"seite_an_{i}")), "dauer": int(dauer),
                 "live": bool(feld(f"seite_live_{i}"))}  # fmt: skip
        if typ == "ergebnis":
            seite["abzeichen"] = bool(feld(f"seite_abzeichen_{i}"))
            seite["strecken"] = [int(x) for x in form.getlist(f"seite_strecken_{i}") if x in ids]
        else:
            abschnitt = feld(f"seite_abschnitt_{i}")
            top = feld(f"seite_top_{i}") or str(TOP_STANDARD)
            if not top.isdigit() or not 1 <= int(top) <= 100:
                raise KonfigFehler(f"Seite {i + 1}: Top N zwischen 1 und 100.")
            seite["abschnitt"] = abschnitt if abschnitt in erlaubt else ""
            seite["top"] = int(top)
        seiten.append(seite)
    live = [x for x in form.getlist("live_abschnitt") if x in erlaubt]
    mit = set(form.getlist("live_vergleich")) & wiederkehrend(liste)
    return {
        "seiten": seiten,
        "live_abschnitte": live,
        "live_vergleich": [x for x in live if x in mit],
    }


def fuer_monitor(konf: dict, liste, erg) -> dict:
    """Aufgelöste Konfiguration für /api/oeffentlich (Strecken als Index, nur eingeschaltete Seiten)."""
    index = {s.id: i for i, s in enumerate(liste)}
    namen = dict(abschnitt_auswahl(liste))
    seiten = []
    for s in konf["seiten"]:
        if not s.get("an"):
            continue
        aus = {
            "typ": s["typ"],
            "dauer": int(s.get("dauer") or 10),
            "live": bool(s.get("live", True)),
        }
        if s["typ"] == "ergebnis":
            aus["abzeichen"] = bool(s.get("abzeichen", True)) and erg.wertung is not None
            aus["strecken"] = [index[x] for x in s.get("strecken") or [] if x in index]
        else:
            sid, _, aid = str(s.get("abschnitt") or "").partition(":")
            if not sid.isdigit() or int(sid) not in index:
                continue
            aus |= {"strecke": index[int(sid)], "abschnitt": aid, "titel": namen.get(s["abschnitt"], aid),
                    "top": int(s.get("top") or TOP_STANDARD)}  # fmt: skip
        seiten.append(aus)
    if not seiten:  # nichts eingeschaltet: wie bisher eine Ergebnisseite
        seiten = [
            {
                "typ": "ergebnis",
                "dauer": 10,
                "live": True,
                "abzeichen": erg.wertung is not None,
                "strecken": [],
            }
        ]
    from app import segmente as sg

    # Segmente 72: Name und „mehrere Durchfahrten“ je Abschnitt – fürs Ergebnis („beste Runde 9:18“)
    info = {}
    for s in liste:
        teile = st.teile_von(s)
        for a in st.abschnitte_von(s):
            mehrfach = a.art == "wiederkehrend" and len(sg.paare(a, teile)) > 1
            info[f"{s.id}:{a.id}"] = (a.name, mehrfach)
    live = []
    for key in konf.get("live_abschnitte") or []:
        sid, _, aid = key.partition(":")
        if sid.isdigit() and int(sid) in index and key in namen:
            name, mehrfach = info.get(key, (aid, False))
            live.append({"id": f"{index[int(sid)]}:{aid}", "strecke": index[int(sid)], "abschnitt": aid,
                         "vergleich": key in (konf.get("live_vergleich") or []),
                         "name": name, "mehrfach": mehrfach})  # fmt: skip
    return {"seiten": seiten, "live": live}
