"""S 6: Neue App-Version, während ein Gerät ungesendete Einträge hat (Deploy im Rennen).

Zwei echte Stände auf demselben Port und derselben Datenbank: Der zweite ist eine Kopie der App
mit geändertem Geräte-Skript, also neuem Hash und neuem Service-Worker-Cache."""

import os
import shutil
import subprocess
import sys
import time

import httpx
import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import (
    PROJEKT,
    TOKENS,
    Server,
    _freier_port,
    _seed,
    jetzt_synchronisieren,
    oeffnen,
    offline,
    warte_bis,
)
from tests.e2e.test_start import starten

MERKMAL = "window.neueVersion = true;\n"


class Staende:
    """Startet die alte App, dann auf demselben Port und mit derselben Datenbank die neue."""

    def __init__(self, tmp_path):
        self.db = tmp_path / "zeitfahren.sqlite3"
        self.neu = tmp_path / "neu"
        self.port = _freier_port()
        self.server = Server(f"http://localhost:{self.port}")
        self.proc = None
        for ordner in ("app", "config", "docs/handbuch"):
            shutil.copytree(PROJEKT / ordner, self.neu / ordner,
                            ignore=shutil.ignore_patterns("__pycache__"))  # fmt: skip
        shutil.copy2(PROJEKT / "pyproject.toml", self.neu)
        with (self.neu / "app" / "static" / "js" / "geraet.js").open("a", encoding="utf-8") as f:
            f.write(MERKMAL)

    def starten(self, ordner) -> str:
        umgebung = {**os.environ, "TAPTRIAL_DB_PFAD": str(self.db),
                    "TAPTRIAL_TOKEN_ADMIN": TOKENS["admin"]}  # fmt: skip
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
             "--port", str(self.port)],
            cwd=ordner, env=umgebung, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )  # fmt: skip
        ende = time.monotonic() + 30
        while time.monotonic() < ende:
            try:
                return httpx.get(f"{self.server.url}/version").json()["oberflaeche"]
            except httpx.TransportError:
                time.sleep(0.05)
        raise RuntimeError("Server startet nicht")

    def stoppen(self) -> None:
        self.proc.terminate()
        self.proc.wait(timeout=5)


@pytest.fixture
def staende(tmp_path, teilnehmer_anzahl, teilnehmer_liste, zwei_strecken, rundkurs):
    s = Staende(tmp_path)
    _seed(str(s.db), teilnehmer_anzahl, teilnehmer_liste, zwei_strecken, rundkurs)
    yield s
    if s.proc is not None and s.proc.poll() is None:
        s.stoppen()


def starts(server):
    return [e for e in server.ereignisse() if e["art"] == "START"]


def zwei_starts_offline(ctx, server):
    p = oeffnen(ctx, server, "start")
    expect(p.locator("#btn-start")).to_be_visible()
    offline(ctx, True)
    starten(p, 2)
    expect(p.locator("#ausstehend")).to_have_text("2 ausstehend")
    return p


def neuer_worker_aktiv(p, version) -> None:
    """Prüft nach einem Update: nur noch der Cache der neuen Version."""
    p.evaluate("navigator.serviceWorker.getRegistration().then((r) => r.update())")
    warte_bis(
        lambda: p.evaluate("caches.keys()") == [f"rcb-shell-{version}"],
        text="nur noch der Cache der neuen Version",
    )


def test_alte_seite_sendet_nach_dem_update_ungesendete_eintraege(staende, neues_geraet):
    alt = staende.starten(PROJEKT)
    server = staende.server
    ctx = neues_geraet()
    p = zwei_starts_offline(ctx, server)
    staende.stoppen()
    neu = staende.starten(staende.neu)
    assert neu != alt
    offline(ctx, False)
    jetzt_synchronisieren(p)  # noch der Code der alten Version im Speicher
    warte_bis(lambda: len(starts(server)) == 2, text="beide Starts bei der neuen Version")
    expect(p.locator("#ausstehend")).to_have_text("0 ausstehend")
    neuer_worker_aktiv(p, neu)
    p.reload()
    p.wait_for_function("window.geraetBereit === true")
    assert p.evaluate("window.neueVersion") is True


def test_eintraege_ueberstehen_das_update_der_seite_und_gehen_danach_durch(staende, neues_geraet):
    alt = staende.starten(PROJEKT)
    server = staende.server
    ctx = neues_geraet()
    p = zwei_starts_offline(ctx, server)
    zeiten = sorted(e["zeit_ms"] for e in p.evaluate("window.geraet.ausstehend()"))
    staende.stoppen()
    neu = staende.starten(staende.neu)
    assert neu != alt
    # Seite und Worker aktualisieren sich, das Senden kommt erst danach durch (schlechtes Netz)
    ctx.route("**/api/ereignisse", lambda route: route.abort())
    offline(ctx, False)
    neuer_worker_aktiv(p, neu)
    p.reload()
    p.wait_for_function("window.geraetBereit === true")
    assert p.evaluate("window.neueVersion") is True
    expect(p.locator("#ausstehend")).to_have_text("2 ausstehend")
    assert starts(server) == []
    ctx.unroute("**/api/ereignisse")
    jetzt_synchronisieren(p)
    warte_bis(lambda: len(starts(server)) == 2, text="Starts nach dem Update übertragen")
    assert (
        sorted(e["zeit_ms"] for e in starts(server)) == zeiten
    )  # Zeit des Tippens, nicht des Sync
    expect(p.locator("#ausstehend")).to_have_text("0 ausstehend")


def test_offline_bleibt_das_geraet_nach_dem_update_benutzbar(staende, neues_geraet):
    staende.starten(PROJEKT)
    ctx = neues_geraet()
    p = oeffnen(ctx, staende.server, "start")
    staende.stoppen()
    neu = staende.starten(staende.neu)
    neuer_worker_aktiv(p, neu)
    p.reload()
    p.wait_for_function("window.geraetBereit === true")
    staende.stoppen()
    offline(ctx, True)
    p.reload()  # aus dem Cache der neuen Version, Server ist aus
    p.wait_for_function("window.geraetBereit === true")
    assert p.evaluate("window.neueVersion") is True
    p.click("#btn-start")
    expect(p.locator("#ausstehend")).to_have_text("1 ausstehend")
