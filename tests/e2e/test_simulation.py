"""Simulation eines ganzen Rennens.

50 Fahrer, zufällige Startabstände, 3 Überholungen, 2 fast gleichzeitige Zieleinläufe,
2 Offline-Abschnitte (Start und Ziel, jeweils mit Neuladen der Seite), 1 DNS, 1 DNF.
Bedient werden die echten Geräteseiten (Start und Ziel in getrennten Browser-Kontexten).
Die Geräteuhr wird vor jedem Tipp auf den geplanten Zeitpunkt gesetzt (`clock.set_fixed_time`),
so läuft das Rennen in Sekunden ab und die Sollzeiten sind exakt bekannt.
"""

from datetime import datetime

import pytest
from playwright.sync_api import expect

from app.einstellungen import STANDARD_KLASSEN_DATEI
from app.klassen import lade_klassen
from app.simulation import ZEITZONE, rennplan, sollergebnis, strecken_dra_a1, ueberholungen
from tests.e2e.conftest import jetzt_synchronisieren, oeffnen, offline, warte_bis


@pytest.fixture(params=[2026, 7, 99], ids=lambda seed: f"seed{seed}")
def plan(request):
    """Drei verschiedene, reproduzierbare Zufallsrennen."""
    return rennplan(seed=request.param)


@pytest.fixture
def teilnehmer_liste(plan):
    return plan.fahrer


@pytest.fixture
def zwei_strecken():
    return True  # Jugend 5 km, Erwachsene 20 km (DRA A.1)


def uhr(page, ms: int) -> None:
    page.clock.set_fixed_time(datetime.fromtimestamp(ms / 1000, ZEITZONE))


def neu_laden(page) -> None:
    page.reload()
    page.wait_for_function("window.geraetBereit === true")


def kachel(page, nr):
    return page.locator(f"#fahrer button[data-nr='{nr}']")


def ausstehend(page) -> int:
    return int(page.locator("#ausstehend").inner_text().split()[0])


def test_simulation_50_fahrer(plan, server, neues_geraet):
    assert ueberholungen(plan) >= 3
    start_ctx, ziel_ctx = neues_geraet(), neues_geraet()
    start = oeffnen(start_ctx, server, "start")
    ziel = oeffnen(ziel_ctx, server, "ziel")
    a, b = plan.gleichzeitig
    # Kacheln zeigen die Strecke als Kennzeichen (Text + Farbe)
    expect(kachel(ziel, 4).locator(".strecke-marke")).to_have_text("5 km")
    expect(kachel(ziel, 1).locator(".strecke-marke")).to_have_text("20 km")

    for zeit, art, nr in plan.ereignisse():
        if art == "START":
            uhr(start, zeit)
            expect(start.locator("#naechster-nr")).to_have_text(str(nr))
            vorher = ausstehend(start)
            start.click("#btn-start")
            expect(start.locator("#naechster-nr")).not_to_have_text(str(nr))
            if plan.offline_start[0] <= zeit <= plan.offline_start[1]:
                expect(start.locator("#ausstehend")).to_have_text(f"{vorher + 1} ausstehend")
        elif art == "DNS":
            uhr(start, zeit)
            expect(start.locator("#naechster-nr")).to_have_text(str(nr))
            start.click("#btn-dns")
            start.click("#btn-dns")
            expect(start.locator("#naechster-nr")).not_to_have_text(str(nr))
        elif art == "ZIEL":
            uhr(ziel, zeit)
            kachel(ziel, nr).click()
            expect(kachel(ziel, nr)).to_have_class("kachel erledigt", timeout=3000)
        elif art == "ZIEL_OHNE_NUMMER":
            uhr(ziel, zeit)
            ziel.click("#btn-ohne-nummer")
            if nr == b:  # beide Stempel erfasst → Rennleitung ordnet zu
                expect(ziel.locator("#badge-offen")).to_have_text("(2)")
                ziel.click("#tab-rennleitung")
                ziel.click("#rl-tab-offen")
                ziel.locator("#offene-zeiten button").first.click()
                ziel.locator(f"#auswahl button[data-nr='{a}']").click()
                expect(ziel.locator("#offene-zeiten button")).to_have_count(1)
                ziel.locator("#offene-zeiten button").first.click()
                ziel.locator(f"#auswahl button[data-nr='{b}']").click()
                expect(ziel.locator("#offene-zeiten button")).to_have_count(0)
                ziel.click("#tab-erfassen")
        elif art == "DNF":
            uhr(ziel, zeit)
            ziel.click("#tab-rennleitung")
            ziel.click("#rl-tab-status")
            ziel.locator(f"#status-auswahl button[data-nr='{nr}']").click()
            ziel.click("#btn-status-dnf")
            ziel.click("#btn-status-dnf")
            expect(ziel.locator("#status-box")).to_be_hidden()
            ziel.click("#tab-erfassen")
            expect(kachel(ziel, nr)).to_have_count(0)
        elif art == "OFFLINE_START":
            offline(start_ctx, True)
            neu_laden(start)  # Seite im Funkloch neu laden: App-Shell aus dem Service Worker
        elif art == "OFFLINE_ZIEL":
            offline(ziel_ctx, True)
            neu_laden(ziel)
        elif art == "ONLINE_START":
            assert ausstehend(start) >= 5
            offline(start_ctx, False)
        elif art == "ONLINE_ZIEL":
            assert ausstehend(ziel) >= 5
            offline(ziel_ctx, False)

    # alles synchronisiert?
    for page in (start, ziel):
        jetzt_synchronisieren(page)
        expect(page.locator("#ausstehend")).to_have_text("0 ausstehend", timeout=15_000)
    warte_bis(lambda: len(server.stand()["im_ziel"]) == 48, text="48 Zieleinläufe auf dem Server")

    stand = server.stand()
    assert stand["konflikte"] == 0 and stand["offene_ziele"] == []
    assert len(stand["gestartet"]) == 49

    # Ergebnis gegen Sollwerte
    klassen = lade_klassen(STANDARD_KLASSEN_DATEI)
    soll = sollergebnis(plan, klassen, strecken_dra_a1(klassen))  # Ränge je Strecke
    assert [s["name"] for s in stand["strecken"]] == ["5 km", "20 km"]
    ist = {t["startnummer"]: t for t in stand["teilnehmer"]}
    oeffentlich = {z["startnummer"]: z for z in server.api("/api/oeffentlich")["gesamt"]}
    for nr, s in soll.items():
        assert ist[nr]["status"] == s.status, nr
        assert ist[nr]["fahrzeit_ms"] == s.fahrzeit_ms, nr
        assert ist[nr]["klasse"] == s.klasse, nr
        assert stand["strecken"][ist[nr]["strecke"]]["name"] == s.strecke, nr
        assert oeffentlich[nr]["rang"] == s.rang_gesamt, nr
        assert oeffentlich[nr]["rang_klasse"] == s.rang_klasse, nr
    reihenfolge = [z["startnummer"] for z in server.api("/api/oeffentlich")["gesamt"]]
    erwartet = [s.startnummer for s in sorted(
        (s for s in soll.values() if s.status == "OK"), key=lambda s: (s.fahrzeit_ms // 100, s.startnummer)
    )]  # fmt: skip
    assert reihenfolge[:48] == erwartet
    assert reihenfolge[48:] == [plan.dnf, plan.dns]  # DNF vor DNS am Ende


@pytest.mark.parametrize("rundkurs", ["nord"])
@pytest.mark.parametrize("plan", [2026], indirect=True, ids=["seed2026"])
def test_simulation_zwei_startorte(plan, server, neues_geraet, rundkurs):
    """Jugend (5 km) startet am Startort „Nord“, Erwachsene (20 km) am Standard-Start,
    beide enden am gemeinsamen Ziel. Die zwei Startgeräte sind abwechselnd offline (mit Neuladen).
    Erwartet: keine Doppelstarts, Ergebnis wie ohne Störungen (Sollwerte)."""
    klassen = lade_klassen(STANDARD_KLASSEN_DATEI)
    soll = sollergebnis(plan, klassen, strecken_dra_a1(klassen))
    ctx = {"nord": neues_geraet(), "start": neues_geraet(), "ziel": neues_geraet()}
    geraet = {"nord": oeffnen(ctx["nord"], server, "start-nord"), "start": oeffnen(ctx["start"], server, "start"),
              "ziel": oeffnen(ctx["ziel"], server, "ziel")}  # fmt: skip
    ziel = geraet["ziel"]

    def startgeraet(nr: int) -> str:
        return "nord" if soll[nr].strecke == "5 km" else "start"

    a, b = plan.gleichzeitig
    gesammelt = {}  # Einträge, die ein Startgerät im Funkloch gesammelt hat
    for zeit, art, nr in plan.ereignisse():
        if art in ("START", "DNS"):
            page = geraet[startgeraet(nr)]
            uhr(page, zeit)
            expect(page.locator("#naechster-nr")).to_have_text(str(nr))
            if art == "START":
                page.click("#btn-start")
            else:
                page.click("#btn-dns")
                page.click("#btn-dns")
            expect(page.locator("#naechster-nr")).not_to_have_text(str(nr))
        elif art == "ZIEL":
            uhr(ziel, zeit)
            kachel(ziel, nr).click()
            expect(kachel(ziel, nr)).to_have_class("kachel erledigt", timeout=3000)
        elif art == "ZIEL_OHNE_NUMMER":
            uhr(ziel, zeit)
            ziel.click("#btn-ohne-nummer")
            if nr == b:
                ziel.click("#tab-rennleitung")
                ziel.click("#rl-tab-offen")
                for x in (a, b):
                    ziel.locator("#offene-zeiten button").first.click()
                    ziel.locator(f"#auswahl button[data-nr='{x}']").click()
                expect(ziel.locator("#offene-zeiten button")).to_have_count(0)
                ziel.click("#tab-erfassen")
        elif art == "DNF":
            uhr(ziel, zeit)
            ziel.click("#tab-rennleitung")
            ziel.click("#rl-tab-status")
            ziel.locator(f"#status-auswahl button[data-nr='{nr}']").click()
            ziel.click("#btn-status-dnf")
            ziel.click("#btn-status-dnf")
            ziel.click("#tab-erfassen")
        elif art in ("OFFLINE_START", "OFFLINE_ZIEL"):  # im Wechsel: erst Nord, dann Start offline
            name = "nord" if art == "OFFLINE_START" else "start"
            offline(ctx[name], True)
            neu_laden(geraet[name])
        elif art in ("ONLINE_START", "ONLINE_ZIEL"):
            name = "nord" if art == "ONLINE_START" else "start"
            gesammelt[name] = ausstehend(geraet[name])
            offline(ctx[name], False)

    assert (
        gesammelt["nord"] >= 1 and gesammelt["start"] >= 1
    )  # beide Startgeräte waren offline aktiv
    for page in geraet.values():
        jetzt_synchronisieren(page)
        expect(page.locator("#ausstehend")).to_have_text("0 ausstehend", timeout=15_000)
    warte_bis(lambda: len(server.stand()["im_ziel"]) == 48, text="48 Zieleinläufe auf dem Server")
    starts = [e["startnummer"] for e in server.ereignisse() if e["art"] == "START"]
    assert len(starts) == len(set(starts)) == 49  # keine Doppelstarts
    stand = server.stand()
    assert stand["konflikte"] == 0 and stand["offene_ziele"] == []
    ist = {t["startnummer"]: t for t in stand["teilnehmer"]}
    oeffentlich = {z["startnummer"]: z for z in server.api("/api/oeffentlich")["gesamt"]}
    for nr, s in soll.items():
        assert (ist[nr]["status"], ist[nr]["fahrzeit_ms"]) == (s.status, s.fahrzeit_ms), nr
        assert stand["strecken"][ist[nr]["strecke"]]["name"] == s.strecke, nr
        assert (oeffentlich[nr]["rang"], oeffentlich[nr]["rang_klasse"]) == (
            s.rang_gesamt,
            s.rang_klasse,
        ), nr
