"""Streckendefinition als Datei speichern und laden.

TOML wie die Klassen-/Wertungssätze, mit Versionsfeld. Inhalt: Name, gemessene Länge, Meta-Infos
(Beschreibung, Komoot-Link), Messpunkte (über ihre Namen), Streckenverlauf (Teile mit Segmenten,
Rundenblöcke) und Auswertungsabschnitte. Nicht in die Datei gehören die Klassenzuordnung und
 die Darstellung – „anzeigen“ und „Vergleich“; ältere Dateien mit diesen Angaben laden
mit dem Hinweis „Darstellungseinstellungen werden ignoriert“.

    version = 1
    name = "20 km"
    laenge_km = 20.2
    beschreibung = "Rundkurs Kottenforst"
    komoot = "https://www.komoot.com/de-de/tour/123"
    messpunkte = ["Kreisel", "Berg"]

    [[teil]]                  # Einzelsegment (name leer) oder Rundenblock
    name = "Runde"
    wdh = 2
    [[teil.segment]]
    name = "Kreisel→Berg"
    von = "Kreisel"           # „Start“, „Ziel“ oder Name einer Zwischenzeit
    nach = "Berg"
    km = 3.5

    [[abschnitt]]             # eigene Auswertungsabschnitte
    name = "Runde"
    von = "Kreisel"
    nach = "Kreisel"
    art = "wiederkehrend"     # oder „einmalig“ (mit von_nr/nach_nr)
"""

import json
import tomllib
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app import segmente as sg
from app import strecken as st
from app.models import Messpunkt, Strecke, Veranstaltung
from app.saetze import SatzFehler, _dekodieren, _klein, _toml_text

VERSION = 1
KOMOOT_MAX = 500


def komoot_pruefen(url: str) -> str | None:
    """Komoot-Link (oder anderer Tourlink): leer erlaubt, sonst https-Adresse."""
    url = (url or "").strip()
    if not url:
        return None
    if (
        not url.lower().startswith("https://")
        or " " in url
        or len(url) > KOMOOT_MAX
        or len(url) < 12
    ):
        raise SatzFehler("Komoot-Link: bitte eine vollständige https-Adresse eintragen.")
    return url


def _km(km) -> str:
    return f"{km:g}"


def strecke_toml(session: Session, v: Veranstaltung, s: Strecke) -> str:
    namen = {
        sg.START: "Start",
        sg.ZIEL: "Ziel",
        **{str(m.id): m.name for m in st.messpunkte_von(session, v)},
    }
    teile = st.teile_von(s)
    benutzt = []
    for p in sg.innere_punkte(teile):
        if namen.get(p, p) not in benutzt:
            benutzt.append(namen.get(p, p))
    zeilen = [f"# Strecke „{s.name}“ – TapTrial (Klassenzuordnung gehört nicht dazu)",
              f"version = {VERSION}", f"name = {_toml_text(s.name)}"]  # fmt: skip
    if s.laenge_km:
        zeilen.append(f"laenge_km = {_km(s.laenge_km)}")
    if s.beschreibung:
        zeilen.append(f"beschreibung = {_toml_text(s.beschreibung)}")
    if s.komoot_url:
        zeilen.append(f"komoot = {_toml_text(s.komoot_url)}")
    orte = st.orte_von(session, v)  # Start- und Zielort über ihre Namen
    zeilen.append(f"startort = {_toml_text(st.startort_von(s, orte).name)}")
    zeilen.append(f"zielort = {_toml_text(st.zielort_von(s, orte).name)}")
    zeilen.append(f"messpunkte = [{', '.join(_toml_text(n) for n in benutzt)}]")
    for t in teile:
        zeilen += ["", "[[teil]]", f"name = {_toml_text(t.name)}", f"wdh = {t.wdh}"]
        for x in t.segmente:
            zeilen += ["[[teil.segment]]", f"name = {_toml_text(x.name)}", f"von = {_toml_text(namen.get(x.von, x.von))}",
                       f"nach = {_toml_text(namen.get(x.nach, x.nach))}"]  # fmt: skip
            if x.km is not None:
                zeilen.append(f"km = {_km(x.km)}")
    for a in st.abschnitte_von(s):
        if a.automatisch:  # Segment- und Rundenblock-Abschnitte ergeben sich aus dem Verlauf
            continue
        zeilen += ["", "[[abschnitt]]", f"name = {_toml_text(a.name)}", f"von = {_toml_text(namen.get(a.von, a.von))}",
                   f"nach = {_toml_text(namen.get(a.nach, a.nach))}", f'art = "{a.art}"']  # fmt: skip
        if a.art == sg.EINMALIG:
            zeilen += [f"von_nr = {a.von_nr}", f"nach_nr = {a.nach_nr}"]
    return "\n".join(zeilen) + "\n"


# --- Einlesen ---------------------------------------------------------------------------------


@dataclass
class Entwurf:
    """Gelesene Streckendatei (Punkte über Namen) mit Prüfergebnis."""

    name: str = ""
    laenge_km: float | None = None
    beschreibung: str = ""
    komoot: str | None = None
    messpunkte: list[str] = field(default_factory=list)
    startort: str = ""  # Name des Startorts (leer = Standard „Start“)
    zielort: str = ""
    teile: tuple = ()  # TeilDef mit Messpunkt-NAMEN statt IDs
    abschnitte: list[dict] = field(default_factory=list)
    fehler: list[str] = field(default_factory=list)
    hinweise: list[str] = field(default_factory=list)  # Z. B. ignorierte Darstellung


def _zahl(wert, wo: str, fehler: list[str]) -> float | None:
    if wert is None or wert == "":
        return None
    try:
        km = float(str(wert).replace(",", "."))
    except ValueError:
        km = -1
    if km < 0:
        fehler.append(f"{wo}: Länge ungültig.")
        return None
    return round(km, 2)


DARSTELLUNG_IGNORIERT = "Darstellungseinstellungen werden ignoriert („anzeigen“ und „Vergleich“ stellst du unter Einstellungen → „Zielmonitor und Ergebnisse“ ein)."


def lesen(inhalt: bytes) -> Entwurf:
    e = Entwurf()
    try:
        daten = _klein(tomllib.loads(_dekodieren(inhalt)))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as fehler:
        e.fehler.append(f"Datei nicht lesbar (TOML erwartet): {fehler}")
        return e
    version = daten.get("version")
    if version != VERSION:
        e.fehler.append(f"Unbekannte Version {version!r} (erwartet {VERSION}).")
    e.name = str(daten.get("name") or "").strip()[:100]
    if not e.name:
        e.fehler.append("Name der Strecke fehlt.")
    e.laenge_km = _zahl(daten.get("laenge_km"), "Strecke", e.fehler)
    e.beschreibung = str(daten.get("beschreibung") or "").strip()[:2000]
    try:
        e.komoot = komoot_pruefen(str(daten.get("komoot") or ""))
    except SatzFehler as fehler:
        e.fehler.append(str(fehler))
    e.messpunkte = [str(n).strip()[:100] for n in daten.get("messpunkte") or [] if str(n).strip()]
    e.startort = str(daten.get("startort") or "").strip()[:100]
    e.zielort = str(daten.get("zielort") or "").strip()[:100]
    bekannt = {n.lower(): n for n in e.messpunkte}
    for wort, n in (("start", e.startort), ("ziel", e.zielort)):
        if n.lower() in bekannt:
            e.fehler.append(
                f"„{n}“ ist Zwischenzeit und {'Start' if wort == 'start' else 'Ziel'} zugleich."
            )

    def punkt(text, wo: str) -> str:
        t = str(text or "").strip()
        if t.lower() == "start":
            return sg.START
        if t.lower() == "ziel":
            return sg.ZIEL
        if t.lower() not in bekannt:
            e.fehler.append(f"{wo}: „{t}“ steht nicht in der Liste der Zwischenzeiten.")
        return bekannt.get(t.lower(), t)

    teile = []
    for i, t in enumerate(daten.get("teil") or [], start=1):
        segs = []
        for j, x in enumerate(t.get("segment") or [], start=1):
            wo = f"Teil {i}, Segment {j}"
            von, nach = punkt(x.get("von"), wo), punkt(x.get("nach"), wo)
            name = str(x.get("name") or "").strip()[:100] or f"{x.get('von')}→{x.get('nach')}"
            segs.append(sg.SegmentDef(name, von, nach, _zahl(x.get("km"), wo, e.fehler)))
        try:
            wdh = int(t.get("wdh") or 1)
        except (TypeError, ValueError):
            wdh = 0
        teile.append(sg.TeilDef(tuple(segs), wdh, str(t.get("name") or "").strip()[:100]))
    e.teile = tuple(teile)
    namen = {n: n for n in e.messpunkte}
    e.fehler += sg.pruefen(e.teile, namen, alle_genutzt=False) if e.teile else []
    darstellung = (
        bool(daten.get("segment_abschnitt") or daten.get("block_abschnitt"))
        or any("vergleich" in x or "aus" in x for x in daten.get("abschnitt") or [])
        or any("vergleich" in y for t in daten.get("teil") or [] for y in t.get("segment") or [])
    )
    if darstellung:  # „anzeigen“/„Vergleich“ gehören nicht mehr zur Strecke
        e.hinweise.append(DARSTELLUNG_IGNORIERT)
    for n, x in enumerate(daten.get("abschnitt") or [], start=1):
        a = sg.AbschnittDef(f"a{n}", str(x.get("name") or "").strip()[:100], punkt(x.get("von"), f"Abschnitt {n}"),
                            punkt(x.get("nach"), f"Abschnitt {n}"), str(x.get("art") or sg.WIEDERKEHREND),
                            int(x.get("von_nr") or 1), int(x.get("nach_nr") or 1))  # fmt: skip
        e.fehler += sg.abschnitt_pruefen(a, e.teile, namen)
        e.abschnitte.append({"id": a.id, "name": a.name, "von": a.von, "nach": a.nach, "art": a.art,
                             "von_nr": a.von_nr, "nach_nr": a.nach_nr})  # fmt: skip
    return e


@dataclass
class Vorschau:
    entwurf: Entwurf
    abfolge: str
    anzahl: list[tuple[str, int]]
    wiederverwendet: list[str]
    neu: list[str]
    namenskonflikt: bool
    sperre: str | None  # Grund, warum nicht übernommen werden kann (eingefroren)
    ortkonflikte: list[str] = field(default_factory=list)  # gleicher Name, andere Art
    ersetzbar: Strecke | None = None  # Gleichnamige bzw. gewählte Strecke zum Ersetzen

    @property
    def verlust(self) -> str:
        """Was „Ersetzen“ überschreibt, für die Bestätigung."""
        return ersetzen_verlust(self.ersetzbar) if self.ersetzbar is not None else ""


def ersetzen_verlust(s: Strecke) -> str:
    """„Verlauf, Start/Ziel und 2 eigene Abschnitte von ‚20 km‘ werden ersetzt.“"""
    teile = []
    if st.teile_von(s):
        teile.append("Verlauf")
    teile.append("Start/Ziel")
    try:
        eigene = len((json.loads(s.abschnitte_json or "{}") or {}).get("eigene") or [])
    except (ValueError, AttributeError):
        eigene = 0
    if eigene:
        teile.append(
            f"{eigene} eigene{'r' if eigene == 1 else ''} Abschnitt{'e' if eigene > 1 else ''}"
        )
    for wert, text in (
        (s.laenge_km, "Länge"),
        (s.beschreibung, "Beschreibung"),
        (s.komoot_url, "Komoot-Link"),
    ):
        if wert:
            teile.append(text)
    liste = ", ".join(teile[:-1]) + " und " + teile[-1] if len(teile) > 1 else teile[0]
    return f"{liste} von ‚{s.name}‘ werden ersetzt."


def _benoetigte_orte(e: Entwurf) -> list[tuple[str, str]]:
    """(Name, Art) aller Orte der Datei: Startort, Messpunkte, Zielort."""
    return [(e.startort or "Start", "start"), *((n, "punkt") for n in e.messpunkte),
            (e.zielort or "Ziel", "ziel")]  # fmt: skip


def vorschau(
    session: Session, v: Veranstaltung, e: Entwurf, ersetzen: int | None = None
) -> Vorschau:
    vorhanden = {m.name.lower(): m.art for m in st.orte_von(session, v)}
    anzahl = sg.erwartete_anzahl(e.teile)
    sperre = (
        "Die Streckenzuordnung ist eingefroren (es gibt bereits Zeiten)." if v.regeln_json else None
    )
    orte = _benoetigte_orte(e)
    konflikte = [f"„{n}“ ist hier ein Messpunkt der Art „{st.ORTSARTEN[vorhanden[n.lower()]]}“, in der Datei „{st.ORTSARTEN[a]}“."
                 for n, a in orte if vorhanden.get(n.lower(), a) != a]  # fmt: skip
    namen = {sg.START: e.startort or "Start", sg.ZIEL: e.zielort or "Ziel"}
    return Vorschau(
        e,
        sg.abfolge_text(e.teile, namen) if e.teile else f"{namen[sg.START]} · {namen[sg.ZIEL]}",
        [(n, anzahl.get(n, 0)) for n in e.messpunkte],
        [n for n, a in orte if vorhanden.get(n.lower()) == a],
        [n for n, a in orte if n.lower() not in vorhanden],
        any(s.name.lower() == e.name.lower() for s in st.strecken_von(session, v)),
        sperre,
        konflikte,
        next((x for x in st.strecken_von(session, v) if x.id == ersetzen), None)
        or next((x for x in st.strecken_von(session, v) if x.name.lower() == e.name.lower()), None),
    )


def _orte_anlegen(
    session: Session, v: Veranstaltung, e: Entwurf
) -> tuple[dict[str, str], int, int]:
    """Fehlende Orte der Datei anlegen, gleichnamige wiederverwenden. Gibt (Messpunkt-Name klein →
    ID, Startort-ID, Zielort-ID) zurück."""
    st.sicherstellen(session, v)
    vorhanden = {m.name.lower(): m for m in st.orte_von(session, v, entfernte=True)}
    pos = len(vorhanden)
    for n, art in _benoetigte_orte(e):
        m = vorhanden.get(n.lower())
        if m is not None and m.art != art:
            raise SatzFehler(
                f"„{n}“ ist hier ein Messpunkt der Art „{st.ORTSARTEN[m.art]}“, in der Datei „{st.ORTSARTEN[art]}“."
            )
        if m is not None and m.entfernt:  # Entfernter Standard kommt zurück
            m.entfernt = False
        if m is None:
            m = Messpunkt(veranstaltung_id=v.id, position=pos, name=n, art=art)
            session.add(m)
            vorhanden[n.lower()] = m
            pos += 1
    session.flush()
    ids = {n.lower(): str(vorhanden[n.lower()].id) for n in e.messpunkte}
    return (ids, vorhanden[(e.startort or "Start").lower()].id,
            vorhanden[(e.zielort or "Ziel").lower()].id)  # fmt: skip


def uebernehmen(
    session: Session,
    v: Veranstaltung,
    e: Entwurf,
    name: str,
    klassen_mit_teilnehmern: set[str],
    ersetzen: Strecke | None = None,
) -> Strecke:
    """Legt die Strecke an (ohne Klassen), Orte mit gleichem Namen werden wiederverwendet.

    ersetzen: bestehende Strecke bekommt Verlauf, Abschnitte, Start-/Zielort, Länge,
    Beschreibung und Komoot-Link aus der Datei; Name und Klassenzuordnung bleiben.
    """
    if e.fehler:
        raise SatzFehler("Die Datei hat Fehler: " + " ".join(e.fehler))
    if v.regeln_json:
        raise SatzFehler("Die Streckenzuordnung ist eingefroren (es gibt bereits Zeiten).")
    if ersetzen is not None:
        return _ersetzen(session, v, e, ersetzen, klassen_mit_teilnehmern)
    name = (name or "").strip()[:100]
    if not name:
        raise SatzFehler("Name der Strecke fehlt.")
    if any(s.name.lower() == name.lower() for s in st.strecken_von(session, v)):
        raise SatzFehler(f"Es gibt schon eine Strecke „{name}“ – bitte einen anderen Namen wählen.")
    ids, start_id, ziel_id = _orte_anlegen(session, v, e)

    def pid(p: str) -> str:
        return p if p in (sg.START, sg.ZIEL) else ids[p.lower()]

    teile = _teile(e, pid)
    liste = st.strecken_von(session, v)
    zeilen = [{"id": str(s.id), "name": s.name, "laenge": st.km_text(s.laenge_km)} for s in liste]
    zuordnung = {kl: str(i) for kl, i in st.zuordnung(liste).items()}
    zeilen.append({"id": "", "name": name, "laenge": st.km_text(e.laenge_km)})
    st.strecken_speichern(session, v, zeilen, zuordnung, klassen_mit_teilnehmern)
    neu = st.strecken_von(session, v)[-1]
    _inhalt_setzen(session, v, e, neu, teile, pid, start_id, ziel_id)
    session.commit()
    return neu


def _teile(e: Entwurf, pid) -> tuple:
    return tuple(sg.TeilDef(tuple(sg.SegmentDef(x.name, pid(x.von), pid(x.nach), x.km) for x in t.segmente),
                            t.wdh, t.name) for t in e.teile)  # fmt: skip


def _inhalt_setzen(
    session, v, e: Entwurf, s: Strecke, teile, pid, start_id: int, ziel_id: int
) -> None:
    """Darstellung (anzeigen, Vergleich) passender Abschnitte bleibt beim Ersetzen;
    neue Abschnitte werden angezeigt, ohne Vergleich."""
    bisher = st.abschnitte_von(s) if s.id is not None else []
    alt_teile = st.teile_von(s) if s.id is not None else ()

    def punkte_von(t, key):
        i, j = (int(n) for n in key.split("."))
        return (
            (t[i].segmente[j].von, t[i].segmente[j].nach)
            if i < len(t) and j < len(t[i].segmente)
            else None
        )

    s.teile_json = sg.nach_json(teile) if teile else None
    segmente, bloecke = {}, {}
    for i, t in enumerate(teile or ()):
        for j, _x in enumerate(t.segmente):
            key = f"{i}.{j}"
            alt = next(
                (
                    a
                    for a in bisher
                    if a.segment == key and punkte_von(alt_teile, key) == punkte_von(teile, key)
                ),
                None,
            )
            segmente[key] = {"aus": bool(alt and alt.aus), "vergleich": bool(alt and alt.vergleich)}
        if t.block and len(t.segmente) > 1:
            alt = next(
                (a for a in bisher if a.block == str(i) and a.name == (t.name or "Rundenblock")),
                None,
            )
            bloecke[str(i)] = {
                "aus": bool(alt and alt.aus),
                "vergleich": bool(alt and alt.vergleich),
            }
    alt_eigene = {a.name.lower(): a for a in bisher if not a.automatisch}
    eigene = []
    for a in e.abschnitte:
        alt = alt_eigene.get(a["name"].lower())
        eigene.append({**a, "von": pid(a["von"]), "nach": pid(a["nach"]), "aus": bool(alt and alt.aus),
                       "vergleich": bool(alt and alt.vergleich) and a["art"] == sg.WIEDERKEHREND})  # fmt: skip
    s.abschnitte_json = None
    if eigene or segmente or bloecke:
        s.abschnitte_json = json.dumps({"segmente": segmente, "bloecke": bloecke, "eigene": eigene},
                                       ensure_ascii=False)  # fmt: skip
    s.beschreibung, s.komoot_url = e.beschreibung or None, e.komoot
    st.strecke_orte_setzen(session, v, s, str(start_id), str(ziel_id))
    st.unbenutzte_standards_ausblenden(session, v)  # Auch direkt nach dem Laden


def _ersetzen(
    session: Session, v: Veranstaltung, e: Entwurf, s: Strecke, mit_tn: set[str]
) -> Strecke:
    ids, start_id, ziel_id = _orte_anlegen(session, v, e)

    def pid(p: str) -> str:
        return p if p in (sg.START, sg.ZIEL) else ids[p.lower()]

    liste = st.strecken_von(session, v)
    zeilen = [{"id": str(x.id), "name": x.name,
               "laenge": st.km_text(e.laenge_km if x.id == s.id else x.laenge_km)} for x in liste]  # fmt: skip
    zuordnung = {kl: str(i) for kl, i in st.zuordnung(liste).items()}
    st.strecken_speichern(session, v, zeilen, zuordnung, mit_tn)  # prüft die Länge (Wertung)
    s = session.get(Strecke, s.id)
    _inhalt_setzen(session, v, e, s, _teile(e, pid), pid, start_id, ziel_id)
    session.commit()
    return s
