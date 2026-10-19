"""Rennplan für Simulationstest und Testdaten-Skript (KONZEPT 9, Phase 8).

Erzeugt reproduzierbar (Seed) ein Rennen mit 50 Fahrern: zufällige Startabstände,
3 Überholungen, 2 fast gleichzeitige Zieleinläufe, 1 DNS, 1 DNF und zwei Zeitfenster für
Funklöcher. Fünf Jugendliche fahren die kurze Strecke (5 km, Phase 13), alle anderen 20 km. `sollergebnis` berechnet die erwarteten Werte unabhängig von `app.auswertung`.
"""

import random
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from itertools import pairwise
from zoneinfo import ZoneInfo

from app.klassen import Klasse, klassifizieren

ZEITZONE = ZoneInfo("Europe/Berlin")
ANZAHL = 50
DNS_NR = 13
DNF_NR = 27
AUSSER_KONKURRENZ = 7  # startet außer Konkurrenz: Zeit ja, Rang nein
UEBERHOLER = (6, 21, 36)  # überholen jeweils ihren Vordermann
GLEICHZEITIG = (41, 42)  # kommen 0,4 s nacheinander ins Ziel, erfasst per „Zeit ohne Nummer“
JUGEND = (4, 15, 25, 33, 47)  # Jahrgang 2010–2015, fahren 5 km (9–16 min)
VORNAMEN = ["Anna", "Ben", "Clara", "David", "Eva", "Felix", "Greta", "Henrik", "Ida", "Jonas"]
NACHNAMEN = ["Becker", "Fischer", "Hoffmann", "Klein", "Lange", "Meyer", "Neumann", "Richter"]


@dataclass(frozen=True)
class Fahrer:
    startnummer: int
    vorname: str
    nachname: str
    geschlecht: str
    geburtsjahr: int
    verein: str | None = None
    abzeichen: bool = False
    ausser_konkurrenz: bool = False


@dataclass
class Rennplan:
    datum: date
    fahrer: list[Fahrer]
    start_ms: dict[int, int] = field(default_factory=dict)
    ziel_ms: dict[int, int] = field(default_factory=dict)
    dns: int = DNS_NR
    dnf: int = DNF_NR
    dnf_gemeldet_ms: int = 0
    ueberholer: tuple[int, ...] = UEBERHOLER
    gleichzeitig: tuple[int, int] = GLEICHZEITIG
    offline_start: tuple[int, int] = (0, 0)  # Zeitfenster (ms) Funkloch am Start
    offline_ziel: tuple[int, int] = (0, 0)  # Zeitfenster (ms) Funkloch am Ziel

    def ereignisse(self) -> list[tuple[int, str, int | None]]:
        """Chronologische Abfolge (zeit_ms, art, startnummer) für die Geräte."""
        liste: list[tuple[int, str, int | None]] = []
        for nr, t in self.start_ms.items():
            liste.append((t, "START", nr))
        liste.append((self._dns_zeit(), "DNS", self.dns))
        for nr, t in self.ziel_ms.items():
            art = "ZIEL_OHNE_NUMMER" if nr in self.gleichzeitig else "ZIEL"
            liste.append((t, art, nr))
        liste.append((self.dnf_gemeldet_ms, "DNF", self.dnf))
        for art, (von, bis) in (("START", self.offline_start), ("ZIEL", self.offline_ziel)):
            liste += [(von, f"OFFLINE_{art}", None), (bis, f"ONLINE_{art}", None)]
        return sorted(liste, key=lambda e: (e[0], e[1]))

    def _dns_zeit(self) -> int:
        # DNS wird gemeldet, wenn der Fahrer an der Reihe wäre (nach dem Vordermann)
        return self.start_ms[self.dns - 1] + 1_000


def rennplan(seed: int = 2026, tag: date = date(2026, 10, 10)) -> Rennplan:
    zufall = random.Random(seed)
    fahrer = []
    for nr in range(1, ANZAHL + 1):
        geschlecht = "d" if nr in (17, 44) else ("w" if nr % 3 == 0 else "m")
        fahrer.append(
            Fahrer(nr, zufall.choice(VORNAMEN), zufall.choice(NACHNAMEN), geschlecht,
                   zufall.randint(1950, 2008), zufall.choice(["RC Bonn", "RSV Kottenforst", None]),
                   abzeichen=zufall.random() < 0.5, ausser_konkurrenz=nr == AUSSER_KONKURRENZ)
        )  # fmt: skip
    fahrer = [replace(f, geburtsjahr=tag.year - 11 - f.startnummer % 6) if f.startnummer in JUGEND else f
              for f in fahrer]  # fmt: skip
    plan = Rennplan(tag, fahrer)
    t = int(datetime(tag.year, tag.month, tag.day, 10, 0, tzinfo=ZEITZONE).timestamp() * 1000)
    for nr in range(1, ANZAHL + 1):
        if nr == plan.dns:
            continue
        t += zufall.randint(30_000, 90_000) + zufall.randint(0, 999)  # zufälliger Startabstand
        plan.start_ms[nr] = t
    fahrzeit = {nr: zufall.randint(25 * 60_000, 40 * 60_000) for nr in plan.start_ms}
    for nr in JUGEND:  # kurze Strecke
        fahrzeit[nr] = zufall.randint(9 * 60_000, 16 * 60_000)
    for nr in plan.ueberholer:  # mindestens 30 s vor dem Vordermann im Ziel
        vorne = nr - 1
        fahrzeit[nr] = plan.start_ms[vorne] + fahrzeit[vorne] - plan.start_ms[nr] - 30_000
    for nr, fz in fahrzeit.items():
        if nr != plan.dnf:
            plan.ziel_ms[nr] = plan.start_ms[nr] + fz
    a, b = plan.gleichzeitig
    plan.ziel_ms[b] = plan.ziel_ms[a] + 400
    _abstaende_sichern(plan)
    plan.dnf_gemeldet_ms = plan.start_ms[plan.dnf] + 45 * 60_000
    # Funkloch am Start während der Starts 10–18, am Ziel während der 15.–24. Zieleinläufe
    plan.offline_start = (plan.start_ms[10] - 500, plan.start_ms[18] + 500)
    ziele = sorted(plan.ziel_ms.values())
    plan.offline_ziel = (ziele[14] - 500, ziele[23] + 500)
    return plan


def _abstaende_sichern(plan: Rennplan) -> None:
    """Zielzeiten (außer dem gleichzeitigen Paar) mindestens 2 s auseinander, sonst verschieben."""
    paar = set(plan.gleichzeitig)
    belegt: list[int] = [plan.ziel_ms[plan.gleichzeitig[0]], plan.ziel_ms[plan.gleichzeitig[1]]]
    for nr in sorted(plan.ziel_ms, key=plan.ziel_ms.get):
        if nr in paar:
            continue
        while any(abs(plan.ziel_ms[nr] - b) < 2_000 for b in belegt):
            plan.ziel_ms[nr] += 2_000
        belegt.append(plan.ziel_ms[nr])


def ueberholungen(plan: Rennplan) -> int:
    """Anzahl Fahrer, die vor ihrem direkten Vordermann (Startreihenfolge) im Ziel sind."""
    gestartet = sorted(plan.start_ms, key=plan.start_ms.get)
    return sum(
        1
        for vorne, hinten in pairwise(gestartet)
        if vorne in plan.ziel_ms
        and hinten in plan.ziel_ms
        and plan.ziel_ms[hinten] < plan.ziel_ms[vorne]
    )


@dataclass
class Soll:
    startnummer: int
    status: str
    fahrzeit_ms: int | None
    klasse: str
    rang_gesamt: int | None = None  # Rang auf der eigenen Strecke
    rang_klasse: int | None = None
    strecke: str = ""


def _raenge(zeilen: list[Soll], attr: str) -> None:
    zeilen = sorted(zeilen, key=lambda z: z.fahrzeit_ms // 100)
    for i, z in enumerate(zeilen):
        gleich = i and zeilen[i - 1].fahrzeit_ms // 100 == z.fahrzeit_ms // 100
        setattr(z, attr, getattr(zeilen[i - 1], attr) if gleich else i + 1)


def sollergebnis(
    plan: Rennplan, klassen: list[Klasse], strecke_je_klasse: dict[str, str] | None = None
) -> dict[int, Soll]:
    """Erwartetes Ergebnis, unabhängig von app.auswertung berechnet (Ränge je Strecke)."""
    stichtag = date(plan.datum.year, 12, 31)
    soll: dict[int, Soll] = {}
    for f in plan.fahrer:
        nr = f.startnummer
        if f.geschlecht == "d" and not any(k.geschlecht == "d" for k in klassen):
            klasse = "Offen"
        else:
            klasse = klassifizieren(f, stichtag, klassen)
        if nr == plan.dns:
            soll[nr] = Soll(nr, "DNS", None, klasse)
        elif nr == plan.dnf:
            soll[nr] = Soll(nr, "DNF", None, klasse)
        else:
            soll[nr] = Soll(nr, "OK", plan.ziel_ms[nr] - plan.start_ms[nr], klasse)
    for s in soll.values():
        s.strecke = (strecke_je_klasse or {}).get(s.klasse, "")
    ak = {f.startnummer for f in plan.fahrer if f.ausser_konkurrenz}
    gewertet = [s for s in soll.values() if s.status == "OK" and s.startnummer not in ak]
    for strecke in {s.strecke for s in gewertet}:
        auf = [s for s in gewertet if s.strecke == strecke]
        _raenge(auf, "rang_gesamt")
        for klasse in {s.klasse for s in auf}:
            _raenge([s for s in auf if s.klasse == klasse], "rang_klasse")
    return soll


def strecken_dra_a1(klassen: list[Klasse]) -> dict[str, str]:
    """Zuordnung wie der Vorschlag aus DRA A.1: Jugendklassen „5 km“, alle anderen „20 km“."""
    zuordnung = {k.name: "5 km" if k.alter_bis <= 17 else "20 km" for k in klassen}
    zuordnung["Offen"] = "20 km"
    return zuordnung


# --- Segmente: Beispielstrecke (AUSBAU C1) ---------------------------------------------------

# Messpunkte der Beispielstrecke: (Schlüssel, Name)
BEISPIEL_MESSPUNKTE = (("P1", "Kreisel"), ("P2", "Berg"))


def beispiel_teile(ids: dict[str, str] | None = None, namen: dict[str, str] | None = None) -> tuple:
    """Beispiel aus dem Feedback (Längen sind Platzhalter): Startsegment Start→P1 1,5 km;
    Rundenblock „Runde“ = P1→P2 3,5 km (mit Vergleich), P2→P1 3,5 km, ×2; Zielsegment P1→Ziel 0,5 km.
    ids/namen: Schlüssel → Messpunkt-ID bzw. -Name (Standard: Schlüssel bzw. BEISPIEL_MESSPUNKTE)."""
    from app.segmente import START, ZIEL, SegmentDef, TeilDef

    i = {"P1": "P1", "P2": "P2", **(ids or {})}
    n = {**dict(BEISPIEL_MESSPUNKTE), **(namen or {})}
    return (
        TeilDef((SegmentDef(f"Start→{n['P1']}", START, i["P1"], 1.5),)),
        TeilDef((SegmentDef(f"{n['P1']}→{n['P2']}", i["P1"], i["P2"], 3.5, True),
                 SegmentDef(f"{n['P2']}→{n['P1']}", i["P2"], i["P1"], 3.5)), 2, "Runde"),
        TeilDef((SegmentDef(f"{n['P1']}→Ziel", i["P1"], ZIEL, 0.5),)),
    )  # fmt: skip


@dataclass
class RundkursDurchfahrt:
    zeit_ms: int
    startnummer: int
    ort: str  # Messpunkt-Schlüssel bzw. -ID
    position: int | None  # innere Position in der Abfolge; None = Doppeltipp


def rundkurs(plan: Rennplan, fahrer: set[int], vergessen: float = 0.15, doppelt: float = 0.0,
             seed: int = 1, teile: tuple | None = None) -> list[RundkursDurchfahrt]:  # fmt: skip
    """Durchfahrten der Fahrer `fahrer` an den Messpunkten der Strecke `teile` (Standard: Beispiel).

    Segmentzeiten nach Länge (±5 % je Segment), die Zielzeit des Rennplans bleibt unverändert. Jede
    Durchfahrt wird mit `vergessen` nicht getippt und mit `doppelt` 1–5 s später ein zweites Mal.
    Ein DNF-Fahrer kommt nur bis zur zweiten Durchfahrt.
    """
    from app.segmente import abfolge

    zufall = random.Random(seed)
    folge = abfolge(teile or beispiel_teile())
    laengen = [d.segment.km or 1.0 for d in folge]
    liste: list[RundkursDurchfahrt] = []
    for nr in sorted(fahrer):
        if nr not in plan.start_ms:
            continue
        start = plan.start_ms[nr]
        fahrzeit = plan.ziel_ms[nr] - start if nr in plan.ziel_ms else 30 * 60_000
        anteile = [km * zufall.uniform(0.95, 1.05) for km in laengen]
        faktor = fahrzeit / sum(anteile)
        t = float(start)
        for j, d in enumerate(folge[:-1]):
            t += anteile[j] * faktor
            # immer gleich viele Zufallszahlen: Läufe mit und ohne Fehler bleiben vergleichbar
            weg, zweimal, versatz = (
                zufall.random() < vergessen,
                zufall.random() < doppelt,
                zufall.randint(1_000, 5_000),
            )
            if nr == plan.dnf and j >= 2:
                break
            if weg:
                continue
            liste.append(RundkursDurchfahrt(int(t), nr, d.segment.nach, j))
            if zweimal:
                liste.append(RundkursDurchfahrt(int(t) + versatz, nr, d.segment.nach, None))
    return sorted(liste, key=lambda d: d.zeit_ms)


# --- Nacharbeit mit Segmenten messen -----------------------------------------------------------


@dataclass
class SimEreignis:
    """Minimales Ereignis für `app.auswertung.auswerten` (ohne Datenbank)."""

    uuid: str
    art: str
    erstellt_ms: int
    zeit_ms: int | None = None
    startnummer: int | None = None
    bezug_uuid: str | None = None
    wert: str | None = None
    geraet: str = "sim"
    empfangen_ms: int = 0
    notiz: str | None = None
    delta_ms: int | None = None


@dataclass(frozen=True)
class Nacharbeit:
    """Ergebnis eines Simulationslaufs: Fälle in „Aufgaben“ und Rennleitung, Abweichungen."""

    fahrer: int
    tipps: int  # erwartete Tipps an Messpunkten
    vergessen: int
    doppelt: int
    aufgaben: int  # zusätzliche Fälle in „Aufgaben“ (wie die Admin-Seite zählt)
    rennleitung: int  # zusätzliche offene Fälle in der Rennleitung
    gesamtzeit_falsch: int  # Fahrer mit anderer Fahrzeit als im Lauf ohne Fehler
    rang_falsch: int
    segmentzeiten: int  # vorhandene Segmentzeiten
    segmentzeiten_falsch: int  # vorhandene, aber andere als im Lauf ohne Fehler

    @property
    def je_fahrer_aufgaben(self) -> float:
        return self.aufgaben / self.fahrer

    @property
    def je_fahrer_rennleitung(self) -> float:
        return self.rennleitung / self.fahrer


def nacharbeit_rundkurs(
    seed: int = 1, vergessen: float = 0.15, doppelt: float = 0.02, zwei_strecken: bool = False
) -> Nacharbeit:
    """50 Fahrer (Rennplan) auf der Beispielstrecke, mit und ohne Fehler ausgewertet.

    zwei_strecken (Segmente 12–15): die Jugend fährt eine kurze Strecke Start → Kreisel → Ziel (der
    Kreisel ist gemeinsamer Messpunkt), alle anderen die Beispielstrecke mit dem Abschnitt „Runde“
    (Kreisel bis nächster Kreisel, wiederkehrend, Vergleich an). Geprüft werden Segment- und
    Abschnittszeiten.
    """
    from app import segmente as sg
    from app.auswertung import AuswertungsConfig, MesspunktDef, StreckenDef, auswerten
    from app.einstellungen import STANDARD_KLASSEN_DATEI
    from app.klassen import lade_klassen

    plan = rennplan(seed=seed)
    klassen = lade_klassen(STANDARD_KLASSEN_DATEI)
    mps = tuple(MesspunktDef(k, n) for k, n in BEISPIEL_MESSPUNKTE)
    lang = beispiel_teile()
    runde = sg.AbschnittDef("a1", "Runde", "P1", "P1", sg.WIEDERKEHREND, vergleich=True)
    abschnitte_lang = (*sg.abschnitte(lang), runde)
    if zwei_strecken:
        zuordnung = strecken_dra_a1(klassen)
        kurz = (sg.TeilDef((sg.SegmentDef("Start→Kreisel", sg.START, "P1", 1.0),)),
                sg.TeilDef((sg.SegmentDef("Kreisel→Ziel", "P1", sg.ZIEL, 4.0),)))  # fmt: skip
        strecken = (
            StreckenDef("5 km", 50, frozenset(k for k, x in zuordnung.items() if x == "5 km"), mps[:1], kurz,
                        tuple(sg.abschnitte(kurz))),
            StreckenDef("20 km", 160, frozenset(k for k, x in zuordnung.items() if x == "20 km"), mps, lang,
                        abschnitte_lang),
        )  # fmt: skip
        stichtag = date(plan.datum.year, 12, 31)
        auf_kurz = {f.startnummer for f in plan.fahrer
                    if zuordnung.get(klassifizieren(f, stichtag, klassen)) == "5 km"}  # fmt: skip
    else:
        alle = frozenset(k.name for k in klassen) | {"Offen"}
        strecken = (StreckenDef("Rundkurs", 160, alle, mps, lang, abschnitte_lang),)
        auf_kurz = set()
    cfg = AuswertungsConfig(
        klassen=klassen, stichtag=date(plan.datum.year, 12, 31), strecken=strecken
    )
    zaehler = iter(range(1, 10**9))

    def ev(art: str, t: int, **kw) -> SimEreignis:
        return SimEreignis(f"s{next(zaehler)}", art, t, zeit_ms=t, **kw)

    basis = [ev("START", t, startnummer=nr) for nr, t in plan.start_ms.items()]
    basis += [ev("ZIEL", t, startnummer=nr) for nr, t in plan.ziel_ms.items()]
    fahrer = {f.startnummer for f in plan.fahrer}

    def tipps(verg: float, dopp: float) -> list[RundkursDurchfahrt]:
        liste = rundkurs(
            plan, fahrer - auf_kurz, vergessen=verg, doppelt=dopp, seed=seed, teile=lang
        )
        if auf_kurz:
            liste += rundkurs(
                plan, auf_kurz, vergessen=verg, doppelt=dopp, seed=seed + 1, teile=strecken[0].teile
            )
        return liste

    ohne, mit = tipps(0.0, 0.0), tipps(vergessen, doppelt)
    erg_soll = auswerten(plan.fahrer, basis + [ev("DURCHFAHRT", d.zeit_ms, startnummer=d.startnummer, wert=d.ort)
                                              for d in ohne], cfg)  # fmt: skip
    erg = auswerten(plan.fahrer, basis + [ev("DURCHFAHRT", d.zeit_ms, startnummer=d.startnummer, wert=d.ort)
                                         for d in mit], cfg)  # fmt: skip
    soll = {z.startnummer: z for z in erg_soll.zeilen}
    vorhanden = [(z.startnummer, art, i, x.zeit_ms) for z in erg.zeilen
                 for art in ("segmentzeiten", "abschnittszeiten") for i, x in enumerate(getattr(z, art))
                 if x.zeit_ms is not None]  # fmt: skip
    return Nacharbeit(
        fahrer=len(plan.fahrer),
        tipps=len(ohne),
        vergessen=len(ohne) - sum(1 for d in mit if d.position is not None),
        doppelt=sum(1 for d in mit if d.position is None),
        # gegenüber dem Lauf ohne Fehler zusätzliche Fälle (die Segmente dürfen keine erzeugen)
        aufgaben=len(erg.blockierende_konflikte)
        + len(erg.vorlaeufige_dsq)
        - len(erg_soll.blockierende_konflikte)
        - len(erg_soll.vorlaeufige_dsq),
        rennleitung=len(erg.offene_ziele) - len(erg_soll.offene_ziele),
        gesamtzeit_falsch=sum(
            1 for z in erg.zeilen if z.fahrzeit_ms != soll[z.startnummer].fahrzeit_ms
        ),
        rang_falsch=sum(1 for z in erg.zeilen if z.rang_gesamt != soll[z.startnummer].rang_gesamt),
        segmentzeiten=len(vorhanden),
        segmentzeiten_falsch=sum(
            1 for nr, art, i, ms in vorhanden if getattr(soll[nr], art)[i].zeit_ms != ms
        ),
    )
