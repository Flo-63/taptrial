"""Nacharbeit mit Segmenten messen: Fälle und Abweichungen bei vergessenen Tipps.

Simuliert 50 Fahrer auf der Beispielstrecke (Start→P1, Rundenblock P1→P2→P1 ×2, P1→Ziel) mit
15 % vergessenen und 2 % doppelten Tipps über viele Seeds und zählt Fälle und Abweichungen; dazu
dieselbe Messung mit zwei Strecken (gemeinsamer Kreisel) und dem Abschnitt „Runde“:

    uv run python scripts/nacharbeit.py [--laeufe 200] [--vergessen 0.15] [--doppelt 0.02]
"""

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.simulation import nacharbeit_rundkurs


def bericht(titel: str, laeufe: list) -> None:
    def mittel(feld: str) -> float:
        return statistics.mean(getattr(m, feld) for m in laeufe)

    print(f"{titel} ({len(laeufe)} Läufe, Mittelwerte je Rennen mit {laeufe[0].fahrer} Fahrern):")
    print(
        f"  Tipps {mittel('tipps'):.0f}, vergessen {mittel('vergessen'):.1f}, doppelt {mittel('doppelt'):.1f}"
    )
    print(f"  Aufgaben {mittel('aufgaben'):.2f} (max {max(m.aufgaben for m in laeufe)}), "
          f"je Fahrer {mittel('je_fahrer_aufgaben'):.3f}")  # fmt: skip
    print(f"  Rennleitung {mittel('rennleitung'):.2f} (max {max(m.rennleitung for m in laeufe)}), "
          f"je Fahrer {mittel('je_fahrer_rennleitung'):.3f}")  # fmt: skip
    print(
        f"  Gesamtzeit falsch {mittel('gesamtzeit_falsch'):.2f}, Rang falsch {mittel('rang_falsch'):.2f}"
    )
    print(f"  Segment-/Abschnittszeiten {mittel('segmentzeiten'):.1f}, davon falsch "
          f"{mittel('segmentzeiten_falsch'):.2f} (max {max(m.segmentzeiten_falsch for m in laeufe)})")  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--laeufe", type=int, default=200)
    p.add_argument("--vergessen", type=float, default=0.15)
    p.add_argument("--doppelt", type=float, default=0.02)
    a = p.parse_args(argv)
    for titel, zwei in (
        ("Beispielstrecke", False),
        ("Zwei Strecken, gemeinsamer Kreisel, Abschnitt „Runde“", True),
    ):
        laeufe = [nacharbeit_rundkurs(seed=s, vergessen=a.vergessen, doppelt=a.doppelt, zwei_strecken=zwei)
                  for s in range(1, a.laeufe + 1)]  # fmt: skip
        bericht(titel, laeufe)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
