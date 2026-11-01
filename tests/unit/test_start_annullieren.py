"""K 4: Versehentlichen Start annullieren – eigenes Ereignis, append-only, Fahrer wird DNS."""

from app.auswertung import Art, Status, auswerten
from tests.unit import test_auswertung
from tests.unit.test_auswertung import MIN, T0, TEILNEHMER, _e, start, zeile, ziel

config = test_auswertung.config  # Fixture


def annullieren(nr, t, notiz="nicht da, versehentlich gestartet"):
    return _e(Art.START_ANNULLIERT, startnummer=nr, erstellt_ms=t, notiz=notiz)


def test_annullierter_start_zaehlt_nicht_fahrer_dns(config):
    ev = [start(1, T0), start(2, T0 + MIN), ziel(2, T0 + 21 * MIN), annullieren(1, T0 + 5 * MIN)]
    erg = auswerten(TEILNEHMER, ev, config)
    z = zeile(erg, 1)
    assert z.start_ms is None and z.status == Status.DNS
    assert zeile(erg, 2).status == Status.OK and zeile(erg, 2).rang_gesamt == 1
    assert len(erg.startstempel) == 2  # nichts gelöscht: der Stempel bleibt sichtbar


def test_nach_dnf_und_mit_zieltipp_wird_dns(config):
    """31.10.: ersatzweise DNF gesetzt; die Annullierung danach macht DNS daraus."""
    dnf = _e("STATUS", startnummer=1, wert="DNF", erstellt_ms=T0 + 12 * MIN)
    ev = [start(1, T0), ziel(1, T0 + 20 * MIN), dnf, annullieren(1, T0 + 30 * MIN)]
    z = zeile(auswerten(TEILNEHMER, ev, config), 1)
    assert z.status == Status.DNS and z.start_ms is None and z.fahrzeit_ms is None


def test_spaeterer_echter_start_zaehlt_wieder(config):
    ok = _e("STATUS", startnummer=1, wert="OK", erstellt_ms=T0 + 6 * MIN)
    ev = [start(1, T0), annullieren(1, T0 + 5 * MIN), ok, start(1, T0 + 10 * MIN),
          ziel(1, T0 + 30 * MIN)]  # fmt: skip
    z = zeile(auswerten(TEILNEHMER, ev, config), 1)
    assert z.start_ms == T0 + 10 * MIN and z.status == Status.OK and z.fahrzeit_ms == 20 * MIN


def test_storno_der_annullierung_stellt_den_start_wieder_her(config):
    a = annullieren(1, T0 + 5 * MIN)
    storno = _e("STORNO", bezug_uuid=a.uuid, startnummer=1, erstellt_ms=T0 + 6 * MIN)
    ev = [start(1, T0), ziel(1, T0 + 20 * MIN), a, storno]
    z = zeile(auswerten(TEILNEHMER, ev, config), 1)
    assert z.start_ms == T0 and z.status == Status.OK


def test_ohne_annullierung_unveraendert(config):
    ev = [start(1, T0), ziel(1, T0 + 20 * MIN)]
    z = zeile(auswerten(TEILNEHMER, ev, config), 1)
    assert z.start_ms == T0 and z.status == Status.OK and z.fahrzeit_ms == 20 * MIN
