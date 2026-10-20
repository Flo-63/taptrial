"""Segment-Modell: Messpunkte, Segmente, Rundenblöcke.

Reine Funktionen ohne Datenbank.

- **Messpunkt:** Linie mit eigenem Zeitnehmer (Posten-Gerät), mehrfach durchfahrbar. Start und Ziel
  bleiben eigene Geräte (Punkte „START“ und „ZIEL“).
- **Segment:** Abschnitt zwischen zwei Messungen (Name, Länge in km, von, nach). Segmentzeit = Zeit
  für eine Durchfahrt eines Segments.
- **Streckendefinition:** geordnete Folge von Teilen von Start bis Ziel. Ein Teil ist ein einzelnes
  Segment oder ein Rundenblock (Name, Folge von Segmenten, Anzahl Wiederholungen).
- **Abfolge:** die daraus erwartete Folge der Punkte, z. B. Start · P1 · P2 · P1 · P2 · P1 · Ziel.
"""

import json
from collections import Counter
from dataclasses import dataclass, field
from itertools import pairwise

START = "START"
ZIEL = "ZIEL"
MAX_WDH = 50


@dataclass(frozen=True)
class SegmentDef:
    name: str
    von: str  # START oder Messpunkt-ID
    nach: str  # ZIEL oder Messpunkt-ID
    km: float | None = None
    vergleich: bool = False  # Segmentzeit mit der vorherigen Durchfahrt vergleichen


@dataclass(frozen=True)
class TeilDef:
    """Einzelnes Segment (name leer, wdh 1, ein Segment) oder Rundenblock."""

    segmente: tuple[SegmentDef, ...]
    wdh: int = 1
    name: str = ""

    @property
    def block(self) -> bool:
        return bool(self.name) or self.wdh > 1 or len(self.segmente) > 1


@dataclass(frozen=True)
class SegmentDurchfahrt:
    """Eine erwartete Durchfahrt eines Segments in der Abfolge."""

    schluessel: str  # „Teil.Segment“, gleich für alle Wiederholungen
    segment: SegmentDef
    k: int  # Wiederholung 1..wdh
    wdh: int

    @property
    def label(self) -> str:
        return f"{self.segment.name} ({self.k})" if self.wdh > 1 else self.segment.name


def abfolge(teile: tuple[TeilDef, ...] | list[TeilDef]) -> list[SegmentDurchfahrt]:
    liste = []
    for i, t in enumerate(teile):
        for k in range(1, max(1, t.wdh) + 1):
            for j, s in enumerate(t.segmente):
                liste.append(SegmentDurchfahrt(f"{i}.{j}", s, k, max(1, t.wdh)))
    return liste


def punkte(teile) -> list[str]:
    """Erwartete Punktfolge: START, Messpunkte …, ZIEL."""
    folge = abfolge(teile)
    return [folge[0].segment.von, *(d.segment.nach for d in folge)] if folge else [START, ZIEL]


def innere_punkte(teile) -> list[str]:
    """Erwartete Durchfahrten an Messpunkten in Reihenfolge (ohne Start und Ziel)."""
    return punkte(teile)[1:-1]


def erwartete_anzahl(teile) -> dict[str, int]:
    """Messpunkt-ID → erwartete Zahl der Durchfahrten."""
    return dict(Counter(innere_punkte(teile)))


def mit_messpunkten(teile) -> bool:
    return bool(innere_punkte(teile))


def abfolge_text(teile, namen: dict[str, str]) -> str:
    """„Start · P1 · P2 · P1 · Ziel“ mit Messpunkt-Namen."""
    alle = {START: "Start", ZIEL: "Ziel", **namen}
    return " · ".join(alle.get(p, p) for p in punkte(teile))


def pruefen(teile, messpunkte: dict[str, str], alle_genutzt: bool = True) -> list[str]:
    """Fehler der Streckendefinition (leer = in Ordnung). messpunkte: ID → Name der bekannten
    Messpunkte; alle_genutzt: jeder davon muss in der Abfolge vorkommen."""
    alle = {START: "Start", ZIEL: "Ziel", **messpunkte}

    def n(p: str) -> str:
        return alle.get(p, p)

    fehler = []
    if not teile:
        return ["Keine Segmente definiert."] if messpunkte and alle_genutzt else []
    for t in teile:
        if not t.segmente:
            fehler.append(f"Rundenblock „{t.name}“ hat keine Segmente.")
        if not 1 <= t.wdh <= MAX_WDH:
            fehler.append(f"„{t.name or t.segmente[0].name}“: Wiederholungen 1 bis {MAX_WDH}.")
        for s in t.segmente:
            for p in (s.von, s.nach):
                if p not in alle:
                    fehler.append(f"Segment „{s.name}“: unbekannter Messpunkt „{p}“.")
            if s.von == s.nach and t.wdh == 1:
                fehler.append(f"Segment „{s.name}“: „von“ und „nach“ sind gleich.")
            if s.km is not None and s.km < 0:
                fehler.append(f"Segment „{s.name}“: Länge ungültig.")
            if s.vergleich and t.wdh < 2:
                fehler.append(
                    f"Segment „{s.name}“: „Vergleich mit vorheriger Durchfahrt“ geht nur bei "
                    "mehrfach durchfahrenen Segmenten."
                )
    if fehler:
        return fehler
    folge = abfolge(teile)
    if folge[0].segment.von != START:
        fehler.append(
            f"Die Strecke muss am Start beginnen (erstes Segment „{folge[0].segment.name}“)."
        )
    if folge[-1].segment.nach != ZIEL:
        fehler.append(
            f"Die Strecke muss am Ziel enden (letztes Segment „{folge[-1].segment.name}“)."
        )
    for a, b in pairwise(folge):
        if a.segment.nach != b.segment.von:
            fehler.append(
                f"Lücke: „{a.label}“ endet bei {n(a.segment.nach)}, „{b.label}“ beginnt bei "
                f"{n(b.segment.von)}."
            )
            break
    for d in folge[1:]:
        if d.segment.von == START:
            fehler.append(f"Segment „{d.segment.name}“: Start nur am Anfang.")
    for d in folge[:-1]:
        if d.segment.nach == ZIEL:
            fehler.append(f"Segment „{d.segment.name}“: Ziel nur am Ende.")
    benutzt = set(innere_punkte(teile))
    for mid, name in messpunkte.items() if alle_genutzt else ():
        if mid not in benutzt:
            fehler.append(f"Messpunkt „{name}“ kommt in keinem Segment vor.")
    return fehler


# --- Verlauf als Punktfolge -----------------------------------------------------


@dataclass(frozen=True)
class VerlaufBlock:
    """Rundenblock in der Punktfolge: Punkte in Fahrreihenfolge, „wdh“-mal gefahren."""

    name: str
    wdh: int
    punkte: tuple[str, ...]


def verlauf_aus_teilen(teile) -> list:
    """Teile → Punktfolge zwischen Start und Ziel (ohne beide): Punkt-IDs und VerlaufBlock.

    Ein einzelnes Segment wird zu seinem Endpunkt, ein Rundenblock zu den Endpunkten seiner
    Segmente. Das letzte Segment zum Ziel fällt weg (das Ziel steht immer am Ende).
    """
    folge: list = []
    for i, t in enumerate(teile):
        letztes = i == len(teile) - 1
        if not t.block:
            if not (letztes and t.segmente[0].nach == ZIEL):
                folge.append(t.segmente[0].nach)
            continue
        folge.append(VerlaufBlock(t.name, t.wdh, tuple(s.nach for s in t.segmente)))
    return folge


def teile_aus_verlauf(folge) -> tuple[TeilDef, ...]:
    """Punktfolge → Teile (Segmente ohne Namen und Längen, in Fahrreihenfolge).

    Jeder Punkt ergibt ein Segment vom Punkt davor; ein Rundenblock beginnt am Punkt davor und
    sollte dort enden, damit er wiederholt werden kann. Zum Schluss das Segment zum Ziel.
    """
    teile, vorher = [], START
    for x in folge:
        if isinstance(x, VerlaufBlock):
            segs = []
            for p in x.punkte:
                segs.append(SegmentDef("", vorher, p))
                vorher = p
            teile.append(TeilDef(tuple(segs), x.wdh, x.name))
        else:
            teile.append(TeilDef((SegmentDef("", vorher, x),)))
            vorher = x
    if vorher != ZIEL:
        teile.append(TeilDef((SegmentDef("", vorher, ZIEL),)))
    return tuple(teile)


def verlauf_pruefen(folge, namen: dict[str, str]) -> list[str]:
    """Fehler der Punktfolge, die sich vor dem Ableiten der Segmente erkennen lassen."""
    alle = {START: "Start", ZIEL: "Ziel", **namen}
    fehler, vorher = [], START
    for i, x in enumerate(folge, 1):
        if isinstance(x, VerlaufBlock):
            name = x.name or "Rundenblock"
            if not x.punkte or not all(x.punkte):
                fehler.append(f"Rundenblock „{name}“: jeden Punkt wählen (mindestens einer).")
                continue
            if x.wdh > 1 and x.punkte[-1] != vorher:
                fehler.append(
                    f"Rundenblock „{name}“ beginnt bei {alle.get(vorher, vorher)} und muss dort auch "
                    f"enden, damit er {x.wdh}-mal gefahren werden kann."
                )
            vorher = x.punkte[-1]
        elif not x:
            fehler.append(f"Punkt {i}: Messpunkt wählen.")
        else:
            vorher = x
    return fehler


# --- JSON (Spalte strecke.teile_json) ---------------------------------------------------------


def nach_json(teile) -> str:
    return json.dumps(
        [{"name": t.name, "wdh": t.wdh,
          "segmente": [{"name": s.name, "von": s.von, "nach": s.nach, "km": s.km, "vergleich": s.vergleich}
                       for s in t.segmente]} for t in teile],
        ensure_ascii=False,
    )  # fmt: skip


def aus_json(text: str | None) -> tuple[TeilDef, ...]:
    if not text:
        return ()
    return tuple(
        TeilDef(tuple(SegmentDef(str(s["name"]), str(s["von"]), str(s["nach"]), s.get("km"),
                                 bool(s.get("vergleich"))) for s in t["segmente"]),
                int(t.get("wdh") or 1), str(t.get("name") or ""))
        for t in json.loads(text)
    )  # fmt: skip


# --- Migration aus dem Modell von phase-14 (Anlauf/Runde/Auslauf, Rundenzahl) ----------------


def aus_altem_modell(
    messpunkte: list[tuple[str, str, str, float | None]], runden: int
) -> tuple[TeilDef, ...]:
    """(ID, Name, Abschnitt, km-Position) in Folge, Rundenzahl → Teile mit gleicher Punktfolge.

    Alte Folge: Start, Anlauf …, (Runde …) × N, Auslauf …, Ziel. Neu: Einzelsegmente bis zum ersten
    Runden-Messpunkt, Rundenblock „Runde“ (R1→…→Rk→R1) × (N−1), danach Einzelsegmente bis zum Ziel.
    Längen aus den km-Positionen, wo beide bekannt sind (die Rundenlänge ist unbekannt).
    """
    name = {START: "Start", ZIEL: "Ziel", **{m[0]: m[1] for m in messpunkte}}
    km = {START: 0.0, **{m[0]: m[3] for m in messpunkte}}
    anlauf = [m[0] for m in messpunkte if m[2] == "anlauf"]
    runde = [m[0] for m in messpunkte if m[2] == "runde"]
    auslauf = [m[0] for m in messpunkte if m[2] == "auslauf"]
    n = max(1, runden)

    def seg(a: str, b: str) -> SegmentDef:
        laenge = None
        if km.get(a) is not None and km.get(b) is not None and km[b] > km[a]:
            laenge = round(km[b] - km[a], 2)
        return SegmentDef(f"{name[a]}→{name[b]}", a, b, laenge)

    def einzeln(folge: list[str]) -> list[TeilDef]:
        return [TeilDef((seg(a, b),)) for a, b in pairwise(folge)]

    if not runde or n == 1:
        folge = [START, *anlauf, *(runde * n), *auslauf, ZIEL]
        return tuple(einzeln(folge))
    teile = einzeln([START, *anlauf, runde[0]])
    ring = [*runde, runde[0]]
    teile.append(TeilDef(tuple(SegmentDef(f"{name[a]}→{name[b]}", a, b)
                               for a, b in pairwise(ring)), n - 1, "Runde"))  # fmt: skip
    teile += einzeln([*runde, *auslauf, ZIEL])
    return tuple(teile)


# --- Zuordnung der Durchfahrten zur Abfolge --------------------------------------------------


@dataclass
class Tipp:
    uuid: str
    messpunkt: str
    zeit_ms: int  # korrigierte Uhrzeit


@dataclass
class Fahrt:
    start_ms: int | None
    ziel_ms: int | None
    tipps: list[Tipp] = field(default_factory=list)


@dataclass
class Zuordnung:
    positionen: list[str | None]  # je innerer Abfolge-Position die UUID (oder None)
    fehlend_ab: int | None  # erste fehlende innere Position (None: keine erkannt)
    ohne_platz: list[str] = field(default_factory=list)  # Tipps ohne Position in der Abfolge


LUECKE = 1.5  # Abstand über 1,5 × erwartete Segmentzeit: dazwischen fehlt eine Durchfahrt


def zuordnen(teile, fahrt: Fahrt, median: dict[str, float] | None = None) -> Zuordnung:
    """Tipps eines Fahrers der erwarteten Abfolge zuordnen.

    Der Reihe nach: jeder Tipp geht an die nächste offene Position seines Messpunkts. Mit `median`
    (Segment-Schlüssel → Median der Segmentzeit aller Fahrer) zusätzlich die Abstandsregel: dauert
    es seit der letzten Messung länger als 1,5 × die erwartete Zeit bis zu dieser Position, fehlt
    dazwischen eine Durchfahrt (wichtig bei Rundenblöcken mit nur einem Messpunkt, wo die Reihenfolge
    nichts verrät); der Tipp geht dann an die Position, deren erwartete Zeit am besten passt. Die
    erwartete Zeit wird mit dem Tempo des Fahrers skaliert.
    """
    folge = abfolge(teile)
    innen = innere_punkte(teile)
    pos: list[str | None] = [None] * len(innen)
    ohne: list[str] = []
    tempo = _tempo(teile, fahrt, median) if median else 1.0

    def erwartet(von_punkt: int, bis_punkt: int) -> float | None:
        werte = (
            [median.get(folge[i].schluessel) for i in range(von_punkt, bis_punkt)]
            if median
            else [None]
        )
        return None if any(w is None for w in werte) else sum(werte) * tempo

    i, letzter_punkt, letzte_zeit = 0, 0, fahrt.start_ms
    for t in sorted(fahrt.tipps, key=lambda t: t.zeit_ms):
        kandidaten = [j for j in range(i, len(innen)) if innen[j] == t.messpunkt]
        if not kandidaten:
            ohne.append(t.uuid)
            continue
        j = kandidaten[0]
        if letzte_zeit is not None and len(kandidaten) > 1:
            dauer = t.zeit_ms - letzte_zeit
            soll = erwartet(letzter_punkt, j + 1)
            if soll and dauer > LUECKE * soll:
                passend = [
                    (abs(dauer - e), c) for c in kandidaten if (e := erwartet(letzter_punkt, c + 1))
                ]
                j = min(passend)[1] if passend else j
        pos[j] = t.uuid
        i, letzter_punkt, letzte_zeit = j + 1, j + 1, t.zeit_ms
    return Zuordnung(pos, _fehlend_ab(pos, fahrt), ohne)


def _tempo(teile, fahrt: Fahrt, median: dict[str, float]) -> float:
    """Tempo des Fahrers relativ zum Median: Median seiner Segmentzeit-Verhältnisse (der Reihe nach
    zugeordnet), sonst Fahrzeit ÷ Summe der Mediane; begrenzt auf 0,25 … 4."""
    folge = abfolge(teile)
    z = zuordnen(teile, fahrt)
    zeit = {t.uuid: t.zeit_ms for t in fahrt.tipps}
    werte = segmentzeiten(teile, fahrt, z, zeit)
    verh = [w / median[d.schluessel] for d, w in zip(folge, werte, strict=True)
            if w is not None and median.get(d.schluessel)]  # fmt: skip
    if len(verh) >= 3:
        faktor = sorted(verh)[len(verh) // 2]
    elif (
        fahrt.ziel_ms is not None
        and fahrt.start_ms is not None
        and all(median.get(d.schluessel) for d in folge)
    ):
        faktor = (fahrt.ziel_ms - fahrt.start_ms) / sum(median[d.schluessel] for d in folge)
    else:
        faktor = 1.0
    return min(max(faktor, 0.25), 4.0)


def mediane(
    teile, fahrten: list[Fahrt], vorher: dict[str, float] | None = None
) -> dict[str, float]:
    """Median der Segmentzeit je Segment-Schlüssel über alle Fahrer (zugeordnet der Reihe nach bzw.
    mit der Abstandsregel nach den Medianen `vorher`)."""
    import statistics

    folge = abfolge(teile)
    je: dict[str, list[int]] = {}
    for f in fahrten:
        z = zuordnen(teile, f, vorher)
        zeit = {t.uuid: t.zeit_ms for t in f.tipps}
        for d, w in zip(folge, segmentzeiten(teile, f, z, zeit), strict=True):
            if w is not None:
                je.setdefault(d.schluessel, []).append(w)
    return {k: statistics.median(v) for k, v in je.items()}


def _fehlend_ab(pos: list[str | None], fahrt: Fahrt) -> int | None:
    """Erste Lücke vor der letzten belegten Position; im Ziel zählen auch Lücken am Ende."""
    letzte = max((j for j, u in enumerate(pos) if u), default=-1)
    grenze = len(pos) if fahrt.ziel_ms is not None else letzte
    return next((j for j in range(grenze) if pos[j] is None), None)


def segmentzeiten(teile, fahrt: Fahrt, z: Zuordnung, zeit: dict[str, int]) -> list[int | None]:
    """Segmentzeit je Abfolge-Segment; ab der ersten fehlenden Durchfahrt keine mehr."""
    folge = abfolge(teile)
    zeiten = [fahrt.start_ms, *(zeit[u] if u else None for u in z.positionen), fahrt.ziel_ms]
    liste: list[int | None] = []
    for i in range(len(folge)):
        if z.fehlend_ab is not None and i >= z.fehlend_ab:
            liste.append(None)
            continue
        a, b = zeiten[i], zeiten[i + 1]
        liste.append(b - a if a is not None and b is not None and b > a else None)
    return liste


# --- Auswertungsabschnitte ----------------------------------------------------

EINMALIG = "einmalig"
WIEDERKEHREND = "wiederkehrend"


@dataclass(frozen=True)
class AbschnittDef:
    """Auswertungsabschnitt einer Strecke: von Punkt X bis Punkt Y.

    „einmalig“: von der von_nr-ten Durchfahrt an X bis zur nach_nr-ten an Y; „wiederkehrend“: jede
    Durchfahrt an X bis zur nächsten Durchfahrt an Y (z. B. Runde = P1 bis nächstes P1). Jedes Segment
    ist automatisch ein Abschnitt (segment = Segment-Schlüssel; nicht löschbar, aber ausblendbar).
    """

    id: str
    name: str
    von: str
    nach: str
    art: str = WIEDERKEHREND
    von_nr: int = 1
    nach_nr: int = 1
    vergleich: bool = False
    aus: bool = False
    segment: str = ""  # Segment-Schlüssel bei automatischen Abschnitten
    block: str = ""  # Teil-Index bei automatischen Rundenblock-Abschnitten

    @property
    def automatisch(self) -> bool:
        """Automatisch aus dem Verlauf (Segment oder Rundenblock): nicht löschbar, aber ausblendbar."""
        return bool(self.segment or self.block)


def block_paare(teile, i: int) -> list[tuple[int, int]]:
    """Punkt-Indizes je Wiederholung des Rundenblocks teile[i] (Anfang und Ende jeder Runde)."""
    vorher = sum(len(t.segmente) * max(1, t.wdh) for t in teile[:i])
    n = len(teile[i].segmente)
    return [(vorher + k * n, vorher + (k + 1) * n) for k in range(max(1, teile[i].wdh))]


def _block_abschnitte(teile, e: dict, eigene: list[AbschnittDef]) -> tuple[list, list[str]]:
    """Je Rundenblock mit mehreren Segmenten ein automatischer Abschnitt: Name des
    Blocks, „jede Durchfahrt“, Vergleich an. Wertet ein eigener Abschnitt schon genau dasselbe aus,
    gibt es statt eines Duplikats einen Hinweis. Ein Block mit nur einem Segment ist schon als
    Segment ein Abschnitt."""
    bl = e.get("bloecke") or {}
    liste, hinweise = [], []
    for i, t in enumerate(teile):
        if not t.block or len(t.segmente) < 2:
            continue
        name = t.name or "Rundenblock"
        gleich = next((a for a in eigene if paare(a, teile) == block_paare(teile, i)), None)
        if gleich is not None:
            hinweise.append(f"Rundenblock „{name}“: Der eigene Abschnitt „{gleich.name}“ wertet "
                            "schon dasselbe aus – kein zusätzlicher automatischer Abschnitt.")  # fmt: skip
            continue
        x = bl.get(str(i), {})
        liste.append(AbschnittDef(f"b{i}", name, t.segmente[0].von, t.segmente[-1].nach, WIEDERKEHREND,
                                  vergleich=bool(x.get("vergleich", True)) and t.wdh > 1,
                                  aus=bool(x.get("aus")), block=str(i)))  # fmt: skip
    return liste, hinweise


def abschnitt_hinweise(teile, einstellungen: dict | None = None) -> list[str]:
    """Hinweise zu Rundenblöcken, die ein eigener Abschnitt schon abdeckt."""
    e = einstellungen or {}
    return _block_abschnitte(teile, e, _eigene(e))[1]


def _eigene(e: dict) -> list[AbschnittDef]:
    return [AbschnittDef(str(x["id"]), str(x["name"]), str(x["von"]), str(x["nach"]),
                         str(x.get("art") or WIEDERKEHREND), int(x.get("von_nr") or 1),
                         int(x.get("nach_nr") or 1), bool(x.get("vergleich")), bool(x.get("aus")))
            for x in e.get("eigene") or []]  # fmt: skip


def abschnitte(teile, einstellungen: dict | None = None) -> list[AbschnittDef]:
    """Automatische Abschnitte (je Segment und je Rundenblock einer) und eigene Abschnitte der Strecke.

    einstellungen (JSON-Spalte strecke.abschnitte_json): {"segmente": {Schlüssel: {"aus", "vergleich"}},
    "bloecke": {Teil-Index: {"aus", "vergleich"}},
    "eigene": [{"id", "name", "von", "nach", "art", "von_nr", "nach_nr", "vergleich", "aus"}]}.
    Ohne Einstellung gilt „Vergleich“ aus der Segment-Definition, bei Rundenblöcken an.
    """
    e = einstellungen or {}
    seg = e.get("segmente") or {}
    liste = []
    for i, t in enumerate(teile):
        for j, s in enumerate(t.segmente):
            key = f"{i}.{j}"
            x = seg.get(key, {})
            art = WIEDERKEHREND if t.wdh > 1 else EINMALIG
            liste.append(AbschnittDef(f"s{key}", s.name, s.von, s.nach, art,
                                      vergleich=bool(x.get("vergleich", s.vergleich)) and art == WIEDERKEHREND,
                                      aus=bool(x.get("aus")), segment=key))  # fmt: skip
    eigene = _eigene(e)
    return liste + _block_abschnitte(teile, e, eigene)[0] + eigene


def paare(a: AbschnittDef, teile) -> list[tuple[int, int]]:
    """Punkt-Indizes (in punkte(teile)) je Durchfahrt des Abschnitts."""
    p = punkte(teile)
    if a.segment:
        return [(i, i + 1) for i, d in enumerate(abfolge(teile)) if d.schluessel == a.segment]
    if a.block:
        i = int(a.block)
        return block_paare(teile, i) if i < len(teile) else []
    vor = [i for i, x in enumerate(p) if x == a.von]
    nach = [i for i, x in enumerate(p) if x == a.nach]
    if a.art == EINMALIG:
        if len(vor) < a.von_nr or len(nach) < a.nach_nr or nach[a.nach_nr - 1] <= vor[a.von_nr - 1]:
            return []
        return [(vor[a.von_nr - 1], nach[a.nach_nr - 1])]
    liste = []
    for i in vor:
        q = next((j for j in nach if j > i), None)
        if q is not None:
            liste.append((i, q))
    return liste


def abschnitt_labels(abschnitte_liste, teile) -> list[str]:
    """Spaltenüberschriften je Abschnitt und Durchfahrt („Runde (1)“, „Runde (2)“ …) – in der
    Reihenfolge von ErgebnisZeile.abschnittszeiten."""
    labels = []
    for a in abschnitte_liste:
        n = len(paare(a, teile))
        labels += [f"{a.name} ({k})" if n > 1 else a.name for k in range(1, n + 1)]
    return labels


def abschnitt_pruefen(a: AbschnittDef, teile, namen: dict[str, str]) -> list[str]:
    """X und Y liegen auf der Strecke, Y nach X in der Abfolge; Vergleich nur bei wiederkehrenden."""
    p = punkte(teile)
    alle = {START: "Start", ZIEL: "Ziel", **namen}
    fehler = []
    if not a.name.strip():
        fehler.append("Abschnitt ohne Namen.")
    for x in (a.von, a.nach):
        if x not in p:
            fehler.append(f"Abschnitt „{a.name}“: {alle.get(x, x)} liegt nicht auf der Strecke.")
    if a.art not in (EINMALIG, WIEDERKEHREND):
        fehler.append(
            f"Abschnitt „{a.name}“: „jede Durchfahrt“ oder „eine bestimmte Durchfahrt“ wählen."
        )
    if fehler:
        return fehler
    if not paare(a, teile):
        fehler.append(f"Abschnitt „{a.name}“: {alle.get(a.nach, a.nach)} liegt nicht nach "
                      f"{alle.get(a.von, a.von)} in der Abfolge.")  # fmt: skip
    if a.vergleich and a.art != WIEDERKEHREND:
        fehler.append(f"Abschnitt „{a.name}“: „Vergleich“ nur bei „jede Durchfahrt“.")
    return fehler


def durchfahrt_optionen(teile, namen: dict[str, str]) -> list[tuple[str, int, str]]:
    """Auswahl für Anfang und Ende „einer bestimmten Durchfahrt“: je Punkt der Abfolge
    (Punkt-ID, Nummer der Durchfahrt, Text), z. B. „P1 · 2. Durchfahrt (nach Runde 1)“. Endet dort
    ein Teil, steht dahinter, wonach: Name des Segments bzw. Rundenblock mit Wiederholung."""
    alle = {START: "Start", ZIEL: "Ziel", **namen}
    liste, zaehler = [(START, 1, "Start")], Counter()
    for t in teile:
        for k in range(1, max(1, t.wdh) + 1):
            for j, s in enumerate(t.segmente):
                if s.nach == ZIEL:
                    continue
                zaehler[s.nach] += 1
                nach = ""
                if j == len(t.segmente) - 1:
                    nach = (f"{t.name or 'Rundenblock'} {k}" if t.block
                            else s.name or f"{alle.get(s.von, s.von)} → {alle.get(s.nach, s.nach)}")  # fmt: skip
                text = f"{alle.get(s.nach, s.nach)} · {zaehler[s.nach]}. Durchfahrt"
                liste.append((s.nach, zaehler[s.nach], text + (f" (nach {nach})" if nach else "")))
    return [*liste, (ZIEL, 1, "Ziel")]


def punkt_optionen(teile, namen: dict[str, str]) -> list[tuple[str, str]]:
    """Auswahl für Anfang und Ende bei „jede Durchfahrt“: jeder Punkt einmal, in Fahrreihenfolge."""
    alle = {START: "Start", ZIEL: "Ziel", **namen}
    return [(p, alle.get(p, p)) for p in dict.fromkeys(punkte(teile))]


ART_TEXT = {WIEDERKEHREND: "jede Durchfahrt", EINMALIG: "eine bestimmte Durchfahrt"}


def abschnitt_text(a: AbschnittDef, teile, namen: dict[str, str]) -> str:
    """Vorschau in Worten, z. B. „Runde: Kreisel → Berg → Kreisel, 2-mal“ (ohne
    „mit Vergleich“ – der Vergleich gehört zum Zielmonitor-Feld)."""
    alle = {START: "Start", ZIEL: "Ziel", **namen}
    p, liste = punkte(teile), paare(a, teile)
    name = a.name.strip() or "Abschnitt"
    if not liste:
        return f"{name}: kommt in der Abfolge nicht vor"
    i, j = liste[0]
    weg = " → ".join(alle.get(x, x) for x in p[i : j + 1])
    if a.art == EINMALIG and not a.segment:
        wie = f"nur die {a.von_nr}. Durchfahrt an {alle.get(a.von, a.von)}"
    else:
        wie = "einmal" if len(liste) == 1 else f"{len(liste)}-mal"
    return f"{name}: {weg}, {wie}"


def abschnittszeiten(
    a: AbschnittDef, teile, fahrt: Fahrt, z: Zuordnung, zeit: dict[str, int]
) -> list[int | None]:
    """Zeit je Durchfahrt des Abschnitts aus vorhandenen Messungen. Eine Messung an einem Messpunkt
    zählt nur vor der ersten erkannten fehlenden Durchfahrt (Start und Ziel immer)."""
    werte: list[int | None] = [fahrt.start_ms]
    for j, u in enumerate(z.positionen):
        gilt = u is not None and (z.fehlend_ab is None or j < z.fehlend_ab)
        werte.append(zeit[u] if gilt else None)
    werte.append(fahrt.ziel_ms)
    liste = []
    for i, q in paare(a, teile):
        x, y = werte[i], werte[q]
        liste.append(y - x if x is not None and y is not None and y > x else None)
    return liste
