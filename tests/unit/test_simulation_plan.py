"""Der Rennplan selbst erfüllt die Vorgaben der Simulation (Fahrer, Sonderfälle, Funklöcher)."""

from itertools import pairwise

import pytest

from app.einstellungen import STANDARD_KLASSEN_DATEI
from app.klassen import lade_klassen
from app.simulation import JUGEND, rennplan, sollergebnis, strecken_dra_a1, ueberholungen


@pytest.mark.parametrize("seed", [2026, 7, 99])
def test_rennplan_erfuellt_konzept(seed):
    plan = rennplan(seed=seed)
    assert len(plan.fahrer) == 50
    assert plan.dns not in plan.start_ms and len(plan.start_ms) == 49
    assert plan.dnf in plan.start_ms and plan.dnf not in plan.ziel_ms
    assert ueberholungen(plan) >= 3
    a, b = plan.gleichzeitig
    assert 0 < plan.ziel_ms[b] - plan.ziel_ms[a] < 1000
    starts = sorted(plan.start_ms.values())
    abstaende = [b - a for a, b in pairwise(starts)]
    assert min(abstaende) >= 30_000 and max(abstaende) <= 91_000 and len(set(abstaende)) > 40
    for von, bis in (plan.offline_start, plan.offline_ziel):
        assert von < bis
    starts_offline = [
        t for t in plan.start_ms.values() if plan.offline_start[0] <= t <= plan.offline_start[1]
    ]
    ziele_offline = [
        t for t in plan.ziel_ms.values() if plan.offline_ziel[0] <= t <= plan.offline_ziel[1]
    ]
    assert len(starts_offline) >= 5 and len(ziele_offline) >= 5


def test_rennplan_reproduzierbar():
    assert rennplan().ziel_ms == rennplan().ziel_ms
    assert rennplan(seed=1).ziel_ms != rennplan().ziel_ms


def test_sollergebnis():
    plan = rennplan()
    soll = sollergebnis(plan, lade_klassen(STANDARD_KLASSEN_DATEI))
    assert soll[plan.dns].status == "DNS" and soll[plan.dnf].status == "DNF"
    assert min(s.rang_gesamt for s in soll.values() if s.rang_gesamt) == 1
    assert soll[17].klasse == "Offen"


def test_zwei_strecken():
    """Fünf Jugendliche auf 5 km; Ränge beginnen auf jeder Strecke bei 1."""
    plan = rennplan()
    klassen = lade_klassen(STANDARD_KLASSEN_DATEI)
    soll = sollergebnis(plan, klassen, strecken_dra_a1(klassen))
    jugend = [s for s in soll.values() if s.strecke == "5 km"]
    assert sorted(s.startnummer for s in jugend) == sorted(JUGEND)
    assert all(9 * 60_000 <= s.fahrzeit_ms <= 16 * 60_000 for s in jugend)
    for strecke in ("5 km", "20 km"):
        raenge = sorted(
            s.rang_gesamt for s in soll.values() if s.strecke == strecke and s.rang_gesamt
        )
        assert raenge[0] == 1 and raenge == list(range(1, len(raenge) + 1))
