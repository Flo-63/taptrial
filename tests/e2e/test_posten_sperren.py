"""Posten-Test 28.10.: nicht gestartete Fahrer gesperrt, „Rückgängig“ nach dem Zieleinlauf gesperrt."""

import time
import uuid

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import jetzt_synchronisieren, oeffnen, warte_bis
from tests.e2e.test_ziel import kachel


@pytest.fixture
def rundkurs():
    return "messpunkte"


def durchfahrten(server, nr):
    return [e for e in server.ereignisse() if e["art"] == "DURCHFAHRT" and e["startnummer"] == nr]


def ziel_senden(server, nr):
    jetzt = int(time.time() * 1000)
    server.senden(
        [
            {
                "uuid": str(uuid.uuid4()),
                "art": "ZIEL",
                "startnummer": nr,
                "zeit_ms": jetzt,
                "erstellt_ms": jetzt,
                "geraet": "ziel-api",
            }
        ],
        rolle="ziel",
    )


def test_nicht_gestartete_gesperrt_alle_fahrer_gibt_frei(server, neues_geraet):
    server.starten(1)
    p = oeffnen(neues_geraet(), server, "posten")
    expect(kachel(p, 1)).to_be_enabled()
    expect(kachel(p, 2)).to_have_class("kachel nicht-gestartet")
    expect(kachel(p, 2)).to_be_disabled()
    # Start noch nicht gemeldet (z. B. Startgerät ohne Netz): „Alle Fahrer“ gibt sie frei
    p.click("#toggle-alle")
    expect(kachel(p, 2)).to_be_enabled()
    kachel(p, 2).click()
    jetzt_synchronisieren(p)
    warte_bis(lambda: len(durchfahrten(server, 2)) == 1, text="Durchfahrt Nr. 2")
    p.click("#toggle-alle")
    expect(kachel(p, 3)).to_be_disabled()


def test_rueckgaengig_gesperrt_wenn_schon_im_ziel(server, neues_geraet):
    server.starten(1)
    p = oeffnen(neues_geraet(), server, "posten")
    kachel(p, 1).click()
    expect(p.locator("#btn-einlauf-zurueck")).to_be_enabled()
    ziel_senden(server, 1)
    jetzt_synchronisieren(p)
    expect(p.locator("#btn-einlauf-zurueck")).to_be_disabled(timeout=10_000)
    expect(p.locator("#im-ziel-hinweis")).to_have_text(
        "Schon im Ziel – Rückgängig nur über die Rennleitung"
    )
    expect(p.locator("#btn-plus")).to_be_enabled()  # ±1 s bleibt möglich


class TestZielAmPosten:
    """Ist der Posten selbst das Ziel der Strecke, bleibt „Rückgängig“ wie am Zielgerät."""

    @pytest.fixture
    def rundkurs(self):
        return "ziel_berg"

    @pytest.fixture
    def zwei_strecken(self):
        return True

    def test_rueckgaengig_bleibt_fuer_eigenen_zieltipp(self, server, neues_geraet):
        server.starten(1)
        b = oeffnen(neues_geraet(), server, "posten-berg")
        kachel(b, 1).click()
        jetzt_synchronisieren(b)
        warte_bis(lambda: server.teilnehmer(1)["ziel_ms"] is not None, text="Zielzeit Nr. 1")
        jetzt_synchronisieren(b)
        expect(b.locator("#letzter-text")).to_contain_text("Nr. 1")
        expect(b.locator("#btn-einlauf-zurueck")).to_be_enabled()
        expect(b.locator("#im-ziel-hinweis")).to_have_count(0)
