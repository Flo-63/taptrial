"""Eindeutige Kurznamen für Geräte und Rennleitung.

Nachname wie bisher; haben mehrere Fahrer einer Veranstaltung denselben Nachnamen, kommen so viele
Anfangsbuchstaben des Vornamens dazu, bis er sich unterscheidet („Müller A.“, „Müller An.“), notfalls
der ganze Vorname. Öffentliche Ergebnisse und Zielmonitor zeigen ohnehin den vollen Namen.
"""

from collections import defaultdict
from collections.abc import Iterable


def anzeigenamen(personen: Iterable[tuple[int, str | None, str | None]]) -> dict[int, str]:
    """(Startnummer, Vorname, Nachname) je Fahrer → {Startnummer: Anzeigename}."""
    gruppen: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    for nr, vorname, nachname in personen:
        nach = (nachname or "").strip()
        gruppen[nach.casefold()].append((nr, (vorname or "").strip(), nach))
    namen: dict[int, str] = {}
    for gruppe in gruppen.values():
        for nr, vor, nach in gruppe:
            andere = [v.casefold() for n, v, _ in gruppe if n != nr]
            if not andere or not vor:
                namen[nr] = nach
                continue
            k = next((k for k in range(1, len(vor))
                      if all(a[:k] != vor[:k].casefold() for a in andere)), None)  # fmt: skip
            namen[nr] = f"{nach} {vor[:k]}." if k else f"{nach} {vor}"
    return namen
