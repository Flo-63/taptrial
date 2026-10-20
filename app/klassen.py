"""Altersklassen laden, validieren und Teilnehmer klassifizieren."""

import tomllib
from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from typing import Protocol

GESCHLECHTER = ("m", "w", "d")
OFFENE_KLASSE = "Offen"


class KlassenConfigFehler(ValueError):
    pass


class KeineKlasseFehler(ValueError):
    pass


@dataclass(frozen=True)
class Klasse:
    name: str
    geschlecht: str
    alter_von: int
    alter_bis: int


class HatJahrgang(Protocol):
    geschlecht: str
    geburtsjahr: int


def pruefe_klassen(roh: list[dict]) -> list[Klasse]:
    fehler: list[str] = []
    klassen: list[Klasse] = []
    if not roh:
        raise KlassenConfigFehler("Konfiguration enthält keine Klassen")
    for i, eintrag in enumerate(roh, start=1):
        wo = f"Klasse {i} ({eintrag.get('name', '?')})"
        fehlend = [f for f in ("name", "geschlecht", "alter_von", "alter_bis") if f not in eintrag]
        if fehlend:
            fehler.append(f"{wo}: Feld {', '.join(fehlend)} fehlt")
            continue
        if eintrag["geschlecht"] not in GESCHLECHTER:
            fehler.append(f"{wo}: Geschlecht muss m, w oder d sein")
            continue
        von, bis = eintrag["alter_von"], eintrag["alter_bis"]
        if not all(isinstance(x, int) and not isinstance(x, bool) for x in (von, bis)):
            fehler.append(f"{wo}: alter_von und alter_bis müssen Ganzzahl sein")
            continue
        if von > bis:
            fehler.append(f"{wo}: alter_von ist größer als alter_bis")
            continue
        klassen.append(Klasse(str(eintrag["name"]), eintrag["geschlecht"], von, bis))

    namen: set[str] = set()
    for i, a in enumerate(klassen):
        if a.name in namen:
            fehler.append(f"Klassenname „{a.name}“ doppelt")
        namen.add(a.name)
        for b in klassen[:i]:
            if (
                a.geschlecht == b.geschlecht
                and a.alter_von <= b.alter_bis
                and b.alter_von <= a.alter_bis
            ):
                fehler.append(f"Altersspannen von „{b.name}“ und „{a.name}“ überschneiden sich")
    if fehler:
        raise KlassenConfigFehler("; ".join(fehler))
    return klassen


def lade_klassen(pfad: str) -> list[Klasse]:
    try:
        with open(pfad, "rb") as f:
            daten = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise KlassenConfigFehler(f"{pfad} nicht lesbar: {e}") from e
    return pruefe_klassen(daten.get("klasse", []))


def alter_am_stichtag(geburtsjahr: int, stichtag: date) -> int:
    """Jahrgangsprinzip: Wettkampfjahr minus Geburtsjahr."""
    return stichtag.year - geburtsjahr


def klassifizieren(teilnehmer: HatJahrgang, stichtag: date, config: list[Klasse]) -> str:
    alter = alter_am_stichtag(teilnehmer.geburtsjahr, stichtag)
    g = teilnehmer.geschlecht
    if g not in GESCHLECHTER:
        raise KeineKlasseFehler(f"ungültiges Geschlecht „{g}“")
    for k in config:
        if k.geschlecht == g and k.alter_von <= alter <= k.alter_bis:
            return k.name
    if g == "d" and not any(k.geschlecht == "d" for k in config):
        return OFFENE_KLASSE
    raise KeineKlasseFehler(f"keine Altersklasse für Geschlecht {g}, Alter {alter}")


def luecken(klassen: list[Klasse]) -> list[str]:
    """Warnungen für Alterslücken je Geschlecht (zwischen kleinster und größter Altersgrenze)."""
    warnungen = []
    for g in GESCHLECHTER:
        spannen = sorted((k.alter_von, k.alter_bis) for k in klassen if k.geschlecht == g)
        for (_, bis), (von, _) in pairwise(spannen):
            if von > bis + 1:
                warnungen.append(
                    f"Lücke bei Geschlecht {g}: Alter {bis + 1}–{von - 1} hat keine Klasse"
                )
    return warnungen
