"""Ranglisten je Abschnitt (schnellste Durchfahrt, Gleichstand, a. K., DSQ, DNF)."""

from app import ranglisten as rl
from app import segmente as sg
from tests.unit.test_auswertung import TN
from tests.unit.test_segmente import MIN, RUNDE, _e, rennen

TEILNEHMER = [TN(1, "A", "Eins", "m", 1980), TN(2, "B", "Zwei", "m", 1981), TN(3, "C", "Drei", "m", 1982, ausser_konkurrenz=True),
              TN(4, "D", "Vier", "m", 1983)]  # fmt: skip


def auswerten(ereignisse):
    from datetime import date

    from app.auswertung import AuswertungsConfig, MesspunktDef, StreckenDef, auswerten
    from app.einstellungen import STANDARD_KLASSEN_DATEI
    from app.klassen import lade_klassen
    from tests.unit.test_segmente import beispiel

    klassen = lade_klassen(STANDARD_KLASSEN_DATEI)
    alle = frozenset(k.name for k in klassen) | {"Offen"}
    cfg = AuswertungsConfig(klassen=klassen, stichtag=date(2026, 12, 31), strecken=(
        StreckenDef("Rund", 160, alle, (MesspunktDef("1", "P1"), MesspunktDef("2", "P2")), beispiel(True),
                    tuple(sg.abschnitte(beispiel(True))) + (RUNDE,)),))  # fmt: skip
    return auswerten(TEILNEHMER, ereignisse, cfg)


def fahrer(nr, runden_min, ab=0, ziel=True):
    """Kreisel bei 3 min, dann Runden (Kreisel → Kreisel) mit den angegebenen Minuten."""
    start = ab * MIN
    ev = [_e("START", start, f"s{nr}", nr=nr)]
    t = start + 3 * MIN
    ev.append(_e("DURCHFAHRT", t, f"{nr}k0", nr=nr, wert="1"))
    for k, m in enumerate(runden_min, start=1):
        ev.append(_e("DURCHFAHRT", t + m * MIN // 2, f"{nr}b{k}", nr=nr, wert="2"))
        t += m * MIN
        ev.append(_e("DURCHFAHRT", t, f"{nr}k{k}", nr=nr, wert="1"))
    if ziel:
        ev.append(_e("ZIEL", t + 2 * MIN, f"z{nr}", nr=nr))
    return ev


def test_rangliste_runde_schnellste_durchfahrt():
    ev = (
        fahrer(1, [10, 9])
        + fahrer(2, [9, 10], ab=1)
        + fahrer(3, [8, 8], ab=2)
        + fahrer(4, [11, 12], ab=3)
    )
    ev.append(_e("STATUS", 10**12, "st4", nr=4, wert="DNF"))
    erg = auswerten(ev)
    assert ("0:a1", "Runde") in rl.verfuegbar(erg)
    r = rl.rangliste(erg, "0:a1")
    assert [(z.rang, z.startnummer, z.zeit, z.durchfahrt) for z in r.zeilen] == [
        (None, 3, "8:00,0", "Runde (1)"),  # a. K.: ohne Rang, nach Zeit einsortiert
        (1, 1, "9:00,0", "Runde (2)"), (1, 2, "9:00,0", "Runde (1)"),  # gleiche Zeit, gleicher Rang
        (3, 4, "11:00,0", "Runde (1)")]  # DNF gewertet  # fmt: skip
    assert r.zeilen[-1].status == "DNF"
    # DSQ ausgeschlossen; unbekannter Schlüssel → None
    ev.append(_e("STATUS", 10**12 + 1, "st2", nr=2, wert="DSQ"))
    assert [z.startnummer for z in rl.rangliste(auswerten(ev), "0:a1").zeilen] == [3, 1, 4]
    assert rl.rangliste(erg, "0:xx") is None


def test_rennen_aus_test_segmente_hat_rangliste():
    erg = auswerten(rennen())
    assert [z.startnummer for z in rl.rangliste(erg, "0:a1").zeilen] == [1, 2]
