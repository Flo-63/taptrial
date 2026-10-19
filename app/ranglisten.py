"""Ranglisten je Auswertungsabschnitt (Segmente 15).

Je Strecke nach der schnellsten Durchfahrt des Abschnitts (in Zehnteln); gleiche Zeit = gleicher
Rang; außer Konkurrenz ohne Rang (nach Zeit einsortiert); DSQ ausgeschlossen; DNF gewertet (die
gemessenen Durchfahrten bis zur Aufgabe zählen). Schlüssel einer Rangliste: „<Strecken-Index>:<Abschnitt-ID>“.
"""

from dataclasses import dataclass

from app.auswertung import Ergebnis, Status, format_zehntel, zehntel


@dataclass
class RangZeile:
    rang: int | None
    startnummer: int
    name: str
    verein: str
    klasse: str
    zeit_ms: int
    durchfahrt: str  # Bezeichnung der schnellsten Durchfahrt, z. B. „Runde (2)“
    ausser_konkurrenz: bool
    status: str

    @property
    def zeit(self) -> str:
        return format_zehntel(zehntel(self.zeit_ms))


@dataclass
class Rangliste:
    schluessel: str
    titel: str  # „Runde“ bzw. bei mehreren Strecken „Lang: Runde“
    strecke: int
    zeilen: list[RangZeile]


def verfuegbar(erg: Ergebnis) -> list[tuple[str, str]]:
    """Alle Ranglisten der Veranstaltung: (Schlüssel, Titel) je sichtbarem Abschnitt."""
    mit = [i for i, s in enumerate(erg.strecken) if s.mit_durchfahrten]
    return [(f"{i}:{a.id}", f"{erg.strecken[i].name}: {a.name}" if len(mit) > 1 else a.name)
            for i in mit for a in erg.strecken[i].sichtbare_abschnitte]  # fmt: skip


def rangliste(erg: Ergebnis, schluessel: str) -> Rangliste | None:
    titel = dict(verfuegbar(erg)).get(schluessel)
    if titel is None:
        return None
    index, _, aid = schluessel.partition(":")
    i = int(index)
    zeilen = []
    for z in erg.zeilen:
        if z.strecke != i or z.status == Status.DSQ:
            continue
        zeiten = [x for x in z.abschnittszeiten if x.schluessel == aid and x.zeit_ms is not None]
        if not zeiten:
            continue
        beste = min(zeiten, key=lambda x: x.zeit_ms)
        zeilen.append(RangZeile(None, z.startnummer, f"{z.vorname} {z.nachname}".strip(), z.verein or "",
                                z.klasse or "", beste.zeit_ms, beste.label, z.ausser_konkurrenz, z.status.value))  # fmt: skip
    zeilen.sort(key=lambda r: (zehntel(r.zeit_ms), r.startnummer))
    gewertet = [r for r in zeilen if not r.ausser_konkurrenz]
    for n, r in enumerate(gewertet):
        vorher = gewertet[n - 1] if n else None
        r.rang = vorher.rang if vorher and zehntel(vorher.zeit_ms) == zehntel(r.zeit_ms) else n + 1
    return Rangliste(schluessel, titel, i, zeilen)
