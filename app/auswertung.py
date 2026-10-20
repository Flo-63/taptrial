"""Auswertung als reine Funktion über Teilnehmer und Ereignisse.

Kein Datenbankzugriff. Eingaben sind beliebige Objekte mit den passenden Attributen
(ORM-Modelle oder Dataclasses in Tests).
"""

import statistics
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any

from app.klassen import KeineKlasseFehler, Klasse, klassifizieren
from app.wertung import Wertung, stufe_berechnen


class Art(StrEnum):
    START = "START"
    ZIEL = "ZIEL"
    ZUORDNUNG = "ZUORDNUNG"
    STORNO = "STORNO"
    STATUS = "STATUS"
    MANUELLE_ZEIT = "MANUELLE_ZEIT"
    ERGEBNIS = "ERGEBNIS"  # Ergebnis offiziell/vorläufig erklärt (nur Admin, nur Protokoll)
    MERKMAL = "MERKMAL"  # Abzeichen/außer Konkurrenz im Admin umgeschaltet (nur Protokoll)
    ZEITKORREKTUR = "ZEITKORREKTUR"  # ±1 s auf einen ZIEL- oder DURCHFAHRT-Stempel
    DURCHFAHRT = "DURCHFAHRT"  # Posten: Fahrer passiert einen Messpunkt (wert = Messpunkt-ID)
    RUNDE = "RUNDE"  # Rennleitung: Durchfahrt einer Runde zuordnen (wert „R3“) oder „VERWORFEN“


class Status(StrEnum):
    OK = "OK"
    DNS = "DNS"
    DNF = "DNF"
    DSQ = "DSQ"
    UNVOLLSTAENDIG = "UNVOLLSTAENDIG"
    KONFLIKT = "KONFLIKT"
    OFFEN = "OFFEN"


# DSQ_VORLAEUFIG: von der Rennleitung gesetzt, Zeit und Rang bleiben (unter Vorbehalt),
# bis der Admin mit DSQ bestätigt oder mit OK verwirft.
STATUS_WERTE = ("DNS", "DNF", "DSQ", "DSQ_VORLAEUFIG", "OK")
ERGEBNIS_WERTE = ("OFFIZIELL", "VORLAEUFIG")
# Schnellkorrektur am Ziel: je Tipp ±1000 ms (bis 10/2026: ±200 ms, bleibt gültig),
# Summe je Stempel höchstens ±5000 ms
KORREKTUR_SCHRITT_MS = 1000
KORREKTUR_SCHRITTE_MS = (200, 1000)
MAX_KORREKTUR_MS = 5000


def korrektur_text(ms: int) -> str:
    """+400 → „+0,4 s“, −1400 → „−1,4 s“."""
    zeichen = "+" if ms >= 0 else "−"
    return f"{zeichen}{abs(ms) / 1000:.1f} s".replace(".", ",")


MERKMAL_WERTE = ("ABZEICHEN_JA", "ABZEICHEN_NEIN", "AUSSER_KONKURRENZ_JA", "AUSSER_KONKURRENZ_NEIN")


class KonfliktArt(StrEnum):
    MEHRERE_STARTS = "MEHRERE_STARTS"
    MEHRERE_ZIELE = "MEHRERE_ZIELE"
    ZIEL_VOR_START = "ZIEL_VOR_START"
    UNPLAUSIBEL = "UNPLAUSIBEL"
    OHNE_ZUORDNUNG = "OHNE_ZUORDNUNG"
    UNBEKANNTE_STARTNUMMER = "UNBEKANNTE_STARTNUMMER"
    KEINE_KLASSE = "KEINE_KLASSE"


# Reihenfolge in der Ergebnisliste nach den gewerteten Fahrern
_SORTIERUNG = [
    Status.OK,
    Status.KONFLIKT,
    Status.UNVOLLSTAENDIG,
    Status.OFFEN,
    Status.DNF,
    Status.DSQ,
    Status.DNS,
]


@dataclass(frozen=True)
class MesspunktDef:
    """Messpunkt einer Strecke (Segmente): Linie mit eigenem Posten-Gerät."""

    id: str
    name: str


@dataclass(frozen=True)
class StreckenDef:
    """Eine Strecke der Veranstaltung: Name, gemessene Länge, zugeordnete Klassen;
    mit Segmenten Messpunkte und Teile (Segmente, Rundenblöcke, app/segmente.py)."""

    name: str
    zehntel_km: int | None
    klassen: frozenset[str] = frozenset()
    messpunkte: tuple[MesspunktDef, ...] = ()
    teile: tuple = ()  # tuple[segmente.TeilDef, ...]
    abschnitte: tuple = ()  # tuple[segmente.AbschnittDef, ...] (inkl. ausgeblendeter)
    startort: MesspunktDef | None = None  # Erster Punkt (None = Standard „Start“)
    zielort: MesspunktDef | None = None  # letzter Punkt (None = Standard „Ziel“)

    @property
    def sichtbare_abschnitte(self) -> tuple:
        return tuple(a for a in self.abschnitte if not a.aus)

    @property
    def mit_durchfahrten(self) -> bool:
        from app.segmente import mit_messpunkten

        return mit_messpunkten(self.teile)

    @property
    def messpunkt_namen(self) -> dict[str, str]:
        return {m.id: m.name for m in self.messpunkte}


@dataclass(frozen=True)
class AuswertungsConfig:
    klassen: list[Klasse]
    stichtag: date
    plausibel_min_prozent: int = 50
    plausibel_max_prozent: int = 200
    plausibel_mindestanzahl: int = 3
    wertung: Wertung | None = None  # Leistungswertung aus (None) oder an
    strecke_zehntel_km: int | None = None  # nur ohne `strecken`: Länge der einzigen Strecke
    # Strecken mit ihren Klassen; leer = eine Strecke für alle (bisheriges Verhalten)
    strecken: tuple[StreckenDef, ...] = ()

    def strecken_liste(self) -> tuple[StreckenDef, ...]:
        return self.strecken or (StreckenDef("", self.strecke_zehntel_km),)


@dataclass
class Konflikt:
    art: KonfliktArt
    text: str
    startnummer: int | None = None
    uuids: list[str] = field(default_factory=list)


@dataclass
class OffenesZiel:
    uuid: str
    zeit_ms: int  # korrigierte Zeit
    geraet: str
    korrektur_ms: int = 0
    ort: str = ""  # Zielort-ID des Geräts (ereignis.ort_id); leer = Standard-Zielort


def _ort(e) -> str:
    """Messpunkt eines Start-/Zielstempels bzw. einer manuellen Zeit, leer = Standard."""
    oid = getattr(e, "ort_id", None)
    return str(oid) if oid else ""


@dataclass
class Zielstempel:
    uuid: str
    zeit_ms: int  # korrigierte Zeit (Stempel + Summe ZEITKORREKTUR)
    geraet: str
    startnummer: int | None
    korrektur_ms: int = 0
    ort: str = ""  # Durchfahrten: Messpunkt-ID; Start/Ziel: ort_id


@dataclass
class DurchfahrtZeile:
    """Eine erwartete Durchfahrt an einem Messpunkt (Position in der Abfolge); zeit_ms seit Start."""

    label: str  # z. B. „P1 (2)“
    messpunkt: str  # Messpunkt-ID
    position: int  # innere Position in der Abfolge (0 …)
    zeit_ms: int | None = None
    uuid: str | None = None
    korrektur_ms: int = 0


@dataclass
class SegmentZeit:
    """Segmentzeit einer Segment-Durchfahrt; None = nicht vorhanden."""

    label: str  # z. B. „P1→P2 (2)“
    schluessel: str  # gleich für alle Wiederholungen eines Segments
    k: int
    zeit_ms: int | None = None
    vergleich_ms: int | None = (
        None  # Differenz zur vorherigen Durchfahrt (Anzeige: Zielmonitor-Feld)
    )


@dataclass
class ErgebnisZeile:
    startnummer: int
    vorname: str
    nachname: str
    verein: str | None
    geschlecht: str
    klasse: str | None
    status: Status
    start_ms: int | None = None
    ziel_ms: int | None = None
    fahrzeit_ms: int | None = None
    manuell: bool = False
    dsq_vorlaeufig: str | None = None  # Grund, solange ein vorläufiger DSQ offen ist
    stufe: str | None = None  # GOLD/SILBER/BRONZE (nur bei eingeschalteter Wertung)
    abzeichen: bool = False  # Teilnehmer möchte das Abzeichen erwerben
    ausser_konkurrenz: bool = False  # Zeit wird gewertet, aber ohne Rang
    rang_gesamt: int | None = None  # Rang auf der eigenen Strecke
    rang_klasse: int | None = None
    strecke: int = 0  # Index in Ergebnis.strecken
    korrektur_ms: int = 0  # Schnellkorrektur der verwendeten Zielzeit
    durchfahrten: list[DurchfahrtZeile] = field(
        default_factory=list
    )  # erwartete Durchfahrten (Segmente)
    segmentzeiten: list[SegmentZeit] = field(default_factory=list)
    # Zeiten der sichtbaren Auswertungsabschnitte je Durchfahrt (schluessel = Abschnitt-ID)
    abschnittszeiten: list[SegmentZeit] = field(default_factory=list)
    naechste_runde: dict[str, int] = field(
        default_factory=dict
    )  # Messpunkt → nächste Durchfahrt (1 …)

    @property
    def korrigiert(self) -> bool:
        return self.korrektur_ms != 0

    @property
    def fahrzeit_zehntel(self) -> int | None:
        return None if self.fahrzeit_ms is None else zehntel(self.fahrzeit_ms)

    @property
    def fahrzeit_text(self) -> str:
        return "" if self.fahrzeit_ms is None else format_zehntel(zehntel(self.fahrzeit_ms))


@dataclass
class Ergebnis:
    zeilen: list[ErgebnisZeile]
    konflikte: list[Konflikt]
    offene_ziele: list[OffenesZiel]
    zielstempel: list[Zielstempel] = field(default_factory=list)
    startstempel: list[Zielstempel] = field(default_factory=list)
    offiziell_seit_ms: int | None = None  # gesetzt, wenn das Ergebnis offiziell erklärt ist
    wertung: Wertung | None = None
    strecken: tuple[StreckenDef, ...] = (StreckenDef("", None),)
    durchfahrtstempel: list[Zielstempel] = field(default_factory=list)
    # Segmente: Tipps, die die Auswertung nicht zählt (Grund je UUID, im Protokoll sichtbar)
    nicht_gezaehlt: dict[str, str] = field(default_factory=dict)

    @property
    def mit_durchfahrten(self) -> bool:
        return any(s.mit_durchfahrten for s in self.strecken)

    @property
    def mehrere_strecken(self) -> bool:
        return len(self.strecken) > 1

    def strecke_von(self, z: "ErgebnisZeile") -> StreckenDef:
        return self.strecken[z.strecke]

    @property
    def offiziell(self) -> bool:
        return self.offiziell_seit_ms is not None

    @property
    def vorlaeufige_dsq(self) -> list["ErgebnisZeile"]:
        return [z for z in self.zeilen if z.dsq_vorlaeufig is not None]

    @property
    def blockierende_konflikte(self) -> list["Konflikt"]:
        """Konflikte, die „Ergebnis offiziell“ verhindern (UNPLAUSIBEL ist nur ein Hinweis)."""
        return [k for k in self.konflikte if k.art != KonfliktArt.UNPLAUSIBEL]

    def zeile(self, startnummer: int) -> ErgebnisZeile | None:
        return next((z for z in self.zeilen if z.startnummer == startnummer), None)


def zehntel(ms: int) -> int:
    """Auf Zehntel abschneiden (12:34,567 -> 12:34,5)."""
    return ms // 100


def format_zehntel(z: int) -> str:
    sekunden, zehn = divmod(z, 10)
    minuten, sek = divmod(sekunden, 60)
    stunden, minu = divmod(minuten, 60)
    if stunden:
        return f"{stunden}:{minu:02d}:{sek:02d},{zehn}"
    return f"{minu}:{sek:02d},{zehn}"


def _sortierschluessel(e: Any) -> tuple:
    return (e.erstellt_ms, getattr(e, "empfangen_ms", 0) or 0, e.uuid)


def gueltige_ereignisse(ereignisse: Iterable[Any]) -> list[Any]:
    """Alle nicht stornierten Ereignisse, chronologisch."""
    return _gueltige(sorted(ereignisse, key=_sortierschluessel))


def _gueltige(ereignisse: list[Any]) -> list[Any]:
    """Verwirft stornierte Ereignisse. Ein STORNO kann selbst storniert werden."""
    stornos: dict[str, list[Any]] = defaultdict(list)
    for e in ereignisse:
        if e.art == Art.STORNO and e.bezug_uuid:
            stornos[e.bezug_uuid].append(e)
    memo: dict[str, bool] = {}

    def storniert(uuid: str, tiefe: int = 0) -> bool:
        if uuid not in memo:
            memo[uuid] = tiefe < 100 and any(
                not storniert(s.uuid, tiefe + 1) for s in stornos.get(uuid, [])
            )
        return memo[uuid]

    return [e for e in ereignisse if not storniert(e.uuid)]


def auswerten(
    teilnehmer: Iterable[Any], ereignisse: Iterable[Any], config: AuswertungsConfig
) -> Ergebnis:
    ereignisse = sorted(ereignisse, key=_sortierschluessel)
    gueltig = _gueltige(ereignisse)
    konflikte: list[Konflikt] = []

    # 2. effektive Startnummer: letzte gültige ZUORDNUNG gewinnt
    nummer: dict[str, int | None] = {}
    stempel = [e for e in gueltig if e.art in (Art.START, Art.ZIEL)]
    for e in stempel:
        nummer[e.uuid] = e.startnummer
    for e in gueltig:
        if e.art == Art.ZUORDNUNG and e.bezug_uuid in nummer:
            nummer[e.bezug_uuid] = e.startnummer

    # Durchfahrten an Messpunkten
    durchfahrten = [e for e in gueltig if e.art == Art.DURCHFAHRT and e.startnummer is not None
                    and e.zeit_ms is not None and e.wert]  # fmt: skip
    # Schnellkorrektur: Summe der Deltas je ZIEL-/DURCHFAHRT-Stempel, Summe höchstens ±5000 ms
    ziel_uuids = {e.uuid for e in stempel if e.art == Art.ZIEL} | {e.uuid for e in durchfahrten}
    korrektur: dict[str, int] = defaultdict(int)
    for e in gueltig:
        if e.art == Art.ZEITKORREKTUR and e.bezug_uuid in ziel_uuids:
            neu = korrektur[e.bezug_uuid] + (getattr(e, "delta_ms", None) or 0)
            if abs(neu) <= MAX_KORREKTUR_MS:
                korrektur[e.bezug_uuid] = neu
    zeit = {e.uuid: e.zeit_ms + korrektur.get(e.uuid, 0) for e in [*stempel, *durchfahrten]}
    runde_wert: dict[str, str] = {}  # Rennleitung: Durchfahrt → „R3“ bzw. „VERWORFEN“
    for e in gueltig:
        if e.art == Art.RUNDE and e.bezug_uuid and e.wert:
            runde_wert[e.bezug_uuid] = e.wert

    starts: dict[int, list[Any]] = defaultdict(list)
    ziele: dict[int, list[Any]] = defaultdict(list)
    offene: list[OffenesZiel] = []
    zielstempel = [
        Zielstempel(
            e.uuid, zeit[e.uuid], e.geraet, nummer[e.uuid], korrektur.get(e.uuid, 0), _ort(e)
        )
        for e in stempel
        if e.art == Art.ZIEL
    ]
    startstempel = [
        Zielstempel(e.uuid, e.zeit_ms, e.geraet, nummer[e.uuid], ort=_ort(e))
        for e in stempel
        if e.art == Art.START
    ]
    for e in stempel:
        nr = nummer[e.uuid]
        if nr is None:
            if e.art == Art.ZIEL:
                offene.append(OffenesZiel(e.uuid, zeit[e.uuid], e.geraet, korrektur.get(e.uuid, 0),
                                          _ort(e)))  # fmt: skip
            continue
        (starts if e.art == Art.START else ziele)[nr].append(e)

    manuell_start: dict[int, Any] = {}
    manuell_ziel: dict[int, Any] = {}
    status_wert: dict[int, str] = {}
    dsq_grund: dict[int, str | None] = {}
    for e in gueltig:
        if e.art == Art.MANUELLE_ZEIT and e.startnummer is not None:
            ziel_dict = manuell_start if (e.wert or "ZIEL") == "START" else manuell_ziel
            ziel_dict[e.startnummer] = e
        elif e.art == Art.STATUS and e.startnummer is not None:
            status_wert[e.startnummer] = e.wert
            dsq_grund[e.startnummer] = e.notiz if e.wert == "DSQ_VORLAEUFIG" else None

    for o in offene:
        konflikte.append(
            Konflikt(KonfliktArt.OHNE_ZUORDNUNG, "Zielstempel ohne Zuordnung", None, [o.uuid])
        )

    teilnehmer = sorted(teilnehmer, key=lambda t: t.startnummer)
    bekannte = {t.startnummer for t in teilnehmer}
    for nr in sorted(set(starts) | set(ziele) | set(manuell_ziel) | set(manuell_start)):
        if nr not in bekannte:
            uuids = [e.uuid for e in starts.get(nr, []) + ziele.get(nr, [])]
            konflikte.append(
                Konflikt(
                    KonfliktArt.UNBEKANNTE_STARTNUMMER,
                    f"Stempel für unbekannte Startnummer {nr}",
                    nr,
                    uuids,
                )
            )

    strecken = config.strecken_liste()
    strecke_je_klasse = {kl: i for i, s in enumerate(strecken) for kl in s.klassen}
    klasse_von: dict[int, str | None] = {}
    for t in teilnehmer:
        try:
            klasse_von[t.startnummer] = getattr(t, "klasse_fest", None) or klassifizieren(
                t, config.stichtag, config.klassen
            )
        except KeineKlasseFehler as err:
            klasse_von[t.startnummer] = None
            konflikte.append(
                Konflikt(KonfliktArt.KEINE_KLASSE, f"Nr. {t.startnummer}: {err}", t.startnummer)
            )
    strecke_von = {nr: strecke_je_klasse.get(kl, 0) for nr, kl in klasse_von.items()}

    def start_von(nr: int) -> int | None:
        if nr in manuell_start:
            return manuell_start[nr].zeit_ms
        return starts[nr][0].zeit_ms if len(starts[nr]) == 1 else None

    def ziel_von(nr: int) -> int | None:
        if nr in manuell_ziel:
            return manuell_ziel[nr].zeit_ms
        return zeit[ziele[nr][0].uuid] if len(ziele[nr]) == 1 else None

    runden = _segmente_auswerten(strecken, strecke_von, start_von, ziel_von, durchfahrten, zeit,
                                 korrektur, runde_wert, bekannte)  # fmt: skip

    zeilen: list[ErgebnisZeile] = []
    for t in teilnehmer:
        nr = t.startnummer
        klasse = klasse_von[nr]
        z = ErgebnisZeile(
            startnummer=nr,
            vorname=t.vorname,
            nachname=t.nachname,
            verein=t.verein,
            geschlecht=t.geschlecht,
            klasse=klasse,
            status=Status.OFFEN,
            abzeichen=bool(getattr(t, "abzeichen", False)),
            ausser_konkurrenz=bool(getattr(t, "ausser_konkurrenz", False)),
            strecke=strecke_je_klasse.get(klasse, 0),  # ohne Zuordnung: erste Strecke
        )
        zeilen.append(z)

        konflikt = False
        if nr in manuell_start:
            z.start_ms, z.manuell = manuell_start[nr].zeit_ms, True
        elif len(starts[nr]) > 1:
            konflikt = True
            konflikte.append(
                Konflikt(
                    KonfliktArt.MEHRERE_STARTS,
                    f"Nr. {nr}: {len(starts[nr])} Startstempel",
                    nr,
                    [e.uuid for e in starts[nr]],
                )
            )
        elif starts[nr]:
            z.start_ms = starts[nr][0].zeit_ms

        z.durchfahrten = runden.zeilen.get(nr, [])
        z.segmentzeiten = runden.segmentzeiten.get(nr, [])
        z.abschnittszeiten = runden.abschnittszeiten.get(nr, [])
        z.naechste_runde = runden.naechste.get(nr, {})
        if nr in manuell_ziel:
            z.ziel_ms, z.manuell = manuell_ziel[nr].zeit_ms, True
        elif len(ziele[nr]) > 1:
            konflikt = True
            konflikte.append(
                Konflikt(
                    KonfliktArt.MEHRERE_ZIELE,
                    f"Nr. {nr}: {len(ziele[nr])} Zielstempel",
                    nr,
                    [e.uuid for e in ziele[nr]],
                )
            )
        elif ziele[nr]:
            z.ziel_ms = zeit[ziele[nr][0].uuid]
            z.korrektur_ms = korrektur.get(ziele[nr][0].uuid, 0)

        if not konflikt and z.start_ms is not None and z.ziel_ms is not None:
            if z.ziel_ms <= z.start_ms:
                konflikt = True
                uuids = [e.uuid for e in starts[nr] + ziele[nr]]
                konflikte.append(
                    Konflikt(
                        KonfliktArt.ZIEL_VOR_START, f"Nr. {nr}: Zielzeit vor Startzeit", nr, uuids
                    )
                )
            else:
                z.fahrzeit_ms = z.ziel_ms - z.start_ms

        if status_wert.get(nr) == "DSQ_VORLAEUFIG":
            z.dsq_vorlaeufig = dsq_grund[nr] or "ohne Grund"
        if nr in status_wert and status_wert[nr] not in ("OK", "DSQ_VORLAEUFIG"):
            z.status = Status(status_wert[nr])
        elif konflikt:
            z.status = Status.KONFLIKT
        elif z.fahrzeit_ms is not None:
            z.status = Status.OK
        elif z.start_ms is not None or z.ziel_ms is not None:
            z.status = Status.UNVOLLSTAENDIG

    gewertet = [z for z in zeilen if z.status == Status.OK]
    if config.wertung is not None:
        # Stufe nur für Abzeichen-Teilnehmer; keine bei DNF/DSQ/DNS/Konflikt/unvollständig.
        # Maßgeblich ist die gemessene Länge der eigenen Strecke.
        for z in (z for z in gewertet if z.abzeichen):
            z.stufe = stufe_berechnen(
                config.wertung, z.klasse, z.fahrzeit_ms, strecken[z.strecke].zehntel_km
            )
    for i in range(len(strecken)):  # Plausibilität und Ränge je Strecke
        auf_strecke = [z for z in gewertet if z.strecke == i]
        _plausibilitaet(auf_strecke, config, konflikte, starts, ziele)
        _raenge([z for z in auf_strecke if not z.ausser_konkurrenz])  # a. K.: kein Rang
    zeilen.sort(
        key=lambda z: (
            _SORTIERUNG.index(z.status),
            z.fahrzeit_zehntel if z.status == Status.OK else 0,
            z.startnummer,
        )
    )
    offiziell_seit_ms = None
    for e in gueltig:  # chronologisch, die letzte Erklärung gilt
        if e.art == Art.ERGEBNIS:
            seit = getattr(e, "empfangen_ms", None) or e.erstellt_ms  # Serverzeit
            offiziell_seit_ms = seit if e.wert == "OFFIZIELL" else None
    return Ergebnis(
        zeilen=zeilen,
        konflikte=konflikte,
        offene_ziele=offene,
        zielstempel=zielstempel,
        startstempel=startstempel,
        offiziell_seit_ms=offiziell_seit_ms,
        wertung=config.wertung,
        strecken=strecken,
        nicht_gezaehlt=runden.nicht_gezaehlt,
        durchfahrtstempel=[
            Zielstempel(
                e.uuid, zeit[e.uuid], e.geraet, e.startnummer, korrektur.get(e.uuid, 0), str(e.wert)
            )
            for e in durchfahrten
        ],
    )


DOPPELTIPP_MS = 10_000  # gleiches Gerät, gleicher Fahrer, gleicher Messpunkt: Doppeltipp
ZIEL_TOLERANZ_MS = 10_000  # Messpunkt auf der Ziellinie: Tipp um die Zielzeit zählt nicht
DOPPELTIPP = "Doppeltipp"
AM_ZIEL = "am Ziel"
OHNE_PLATZ = "nicht in der Abfolge"


@dataclass
class _Segmente:
    zeilen: dict[int, list[DurchfahrtZeile]] = field(default_factory=dict)
    segmentzeiten: dict[int, list[SegmentZeit]] = field(default_factory=dict)
    abschnittszeiten: dict[int, list[SegmentZeit]] = field(default_factory=dict)
    naechste: dict[int, dict[str, int]] = field(default_factory=dict)
    nicht_gezaehlt: dict[str, str] = field(default_factory=dict)  # UUID → Grund


def _segmente_auswerten(strecken, strecke_von, start_von, ziel_von, durchfahrten, zeit, korrektur,
                        runde_wert, bekannte) -> _Segmente:  # fmt: skip
    """Durchfahrten der erwarteten Abfolge zuordnen; Segmentzeiten (Segmente, app/segmente.py).

    Doppeltipps (gleiches Gerät, gleicher Fahrer, gleicher Messpunkt, unter 10 s) und Tipps innerhalb
    10 s um die Zielzeit zählen nicht (RUNDE „GILT“ nimmt einen Doppeltipp zurück). Die Segment-
    Zeitnahme berührt Gesamtzeit und Rang nie und erzeugt keine Fälle in Aufgaben oder Rennleitung.
    """
    from app import segmente as sg

    erg = _Segmente()
    je_fahrer: dict[int, list[Any]] = defaultdict(list)
    for e in durchfahrten:
        if e.startnummer in bekannte and runde_wert.get(e.uuid) != "VERWORFEN":
            je_fahrer[e.startnummer].append(e)
    for nr, liste in je_fahrer.items():  # Doppeltipps je Gerät und Messpunkt
        letzte: dict[tuple[str, str], int] = {}
        for e in sorted(liste, key=lambda e: zeit[e.uuid]):
            schluessel = (e.geraet, str(e.wert))
            if (schluessel in letzte and zeit[e.uuid] - letzte[schluessel] < DOPPELTIPP_MS
                    and runde_wert.get(e.uuid) != "GILT"):  # fmt: skip
                erg.nicht_gezaehlt[e.uuid] = DOPPELTIPP
                continue
            letzte[schluessel] = zeit[e.uuid]
        ziel = ziel_von(nr)
        for e in liste:
            if (
                e.uuid not in erg.nicht_gezaehlt
                and ziel is not None
                and abs(zeit[e.uuid] - ziel) <= ZIEL_TOLERANZ_MS
            ):
                erg.nicht_gezaehlt[e.uuid] = AM_ZIEL
    for i, s in enumerate(strecken):
        if not s.mit_durchfahrten:
            continue
        folge = sg.abfolge(s.teile)
        innen = sg.innere_punkte(s.teile)
        namen = s.messpunkt_namen
        zaehler: dict[str, int] = defaultdict(int)
        labels = []
        for mp in innen:
            zaehler[mp] += 1
            labels.append(
                f"{namen.get(mp, mp)} ({zaehler[mp]})" if innen.count(mp) > 1 else namen.get(mp, mp)
            )
        fahrten = {}
        for nr in sorted(n for n, si in strecke_von.items() if si == i):
            tipps = [sg.Tipp(e.uuid, str(e.wert), zeit[e.uuid]) for e in je_fahrer.get(nr, [])
                     if str(e.wert) in namen and e.uuid not in erg.nicht_gezaehlt]  # fmt: skip
            for e in je_fahrer.get(nr, []):
                if str(e.wert) not in namen:
                    erg.nicht_gezaehlt[e.uuid] = OHNE_PLATZ
            fahrten[nr] = sg.Fahrt(start_von(nr), ziel_von(nr), tipps)
        median = sg.mediane(s.teile, list(fahrten.values()))
        median = sg.mediane(s.teile, list(fahrten.values()), median)
        for nr, fahrt in fahrten.items():
            start = fahrt.start_ms
            zu = sg.zuordnen(s.teile, fahrt, median)
            for u in zu.ohne_platz:
                erg.nicht_gezaehlt[u] = OHNE_PLATZ
            erg.zeilen[nr] = [
                DurchfahrtZeile(labels[j], innen[j], j,
                                zeit[u] - start if u and start is not None else None, u,
                                korrektur.get(u, 0) if u else 0)
                for j, u in enumerate(zu.positionen)
            ]  # fmt: skip
            werte = sg.segmentzeiten(s.teile, fahrt, zu, zeit)
            liste, vorher = [], {}
            for d, ms in zip(folge, werte, strict=True):
                diff = None  # Differenz immer; ob sie gezeigt wird, entscheidet die Anzeige
                if ms is not None and vorher.get(d.schluessel) is not None:
                    diff = ms - vorher[d.schluessel]
                vorher[d.schluessel] = ms
                liste.append(SegmentZeit(d.label, d.schluessel, d.k, ms, diff))
            erg.segmentzeiten[nr] = liste
            erg.abschnittszeiten[nr] = _abschnittszeiten(s, fahrt, zu, zeit)
            # Nächste Durchfahrt je Messpunkt aus der Zuordnung – eine Position gilt als
            # vorbei (gezählt oder verpasst), sobald der Fahrer an einer späteren gesehen wurde
            gesehen = [j for j, u in enumerate(zu.positionen) if u]
            letzte = (
                len(innen) - 1 if fahrt.ziel_ms is not None else (max(gesehen) if gesehen else -1)
            )
            erg.naechste[nr] = {
                mp: sum(1 for j in range(letzte + 1) if innen[j] == mp) + 1 for mp in namen
            }
    return erg


def _abschnittszeiten(s: StreckenDef, fahrt, zu, zeit) -> list[SegmentZeit]:
    """Zeiten der sichtbaren Abschnitte je Durchfahrt, „(k)“ bei mehreren; Vergleich zur vorherigen."""
    from app import segmente as sg

    liste = []
    for a in s.sichtbare_abschnitte:
        werte = sg.abschnittszeiten(a, s.teile, fahrt, zu, zeit)
        vorher = None
        for k, ms in enumerate(werte, start=1):
            diff = ms - vorher if ms is not None and vorher is not None else None
            liste.append(
                SegmentZeit(f"{a.name} ({k})" if len(werte) > 1 else a.name, a.id, k, ms, diff)
            )
            vorher = ms
    return liste


def _plausibilitaet(gewertet, config, konflikte, starts, ziele) -> None:
    if len(gewertet) < config.plausibel_mindestanzahl:
        return
    median = statistics.median(z.fahrzeit_ms for z in gewertet)
    unten = median * config.plausibel_min_prozent / 100
    oben = median * config.plausibel_max_prozent / 100
    for z in gewertet:
        if not unten <= z.fahrzeit_ms <= oben:
            konflikte.append(
                Konflikt(
                    KonfliktArt.UNPLAUSIBEL,
                    f"Nr. {z.startnummer}: Fahrzeit {z.fahrzeit_text} außerhalb "
                    f"{config.plausibel_min_prozent}–{config.plausibel_max_prozent} % "
                    f"des Medians {format_zehntel(zehntel(int(median)))}",
                    z.startnummer,
                    [e.uuid for e in starts[z.startnummer] + ziele[z.startnummer]],
                )
            )


def _rang_vergeben(zeilen: list[ErgebnisZeile], attr: str) -> None:
    """Gleiche Zeit in Zehnteln ergibt gleichen Rang (1, 1, 3)."""
    zeilen = sorted(zeilen, key=lambda z: z.fahrzeit_zehntel)
    for i, z in enumerate(zeilen):
        if i and z.fahrzeit_zehntel == zeilen[i - 1].fahrzeit_zehntel:
            setattr(z, attr, getattr(zeilen[i - 1], attr))
        else:
            setattr(z, attr, i + 1)


def _raenge(gewertet: list[ErgebnisZeile]) -> None:
    _rang_vergeben(gewertet, "rang_gesamt")
    je_klasse: dict[str | None, list[ErgebnisZeile]] = defaultdict(list)
    for z in gewertet:
        je_klasse[z.klasse].append(z)
    for klasse, liste in je_klasse.items():
        if klasse is not None:
            _rang_vergeben(liste, "rang_klasse")
