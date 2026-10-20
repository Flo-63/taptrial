"""E2E: echter uvicorn-Server mit frischer DB, Chromium mit mobilem Viewport."""

import os
import re
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx
import pytest

from app import umgebung
from app.db import erzeuge_engine, erzeuge_sessionmaker, tabellen_anlegen
from app.models import Teilnehmer, Veranstaltung

PROJEKT = Path(__file__).resolve().parents[2]
# Posten am Messpunkt „Kreisel“ (Fixture rundkurs)
TOKENS = {
    "start": "e2e-start",
    "ziel": "e2e-ziel",
    "admin": "e2e-admin",
    "posten": "e2e-posten-kreisel",
    "posten-berg": "e2e-posten-berg",
    "start-nord": "e2e-start-nord",  # Weiterer Startort (rundkurs „orte“)
    "ziel-hof": "e2e-ziel-hof",  # weiterer Zielort (rundkurs „zielorte“)
}
GERAET = "Pixel 7"


@dataclass
class Server:
    url: str

    def api(self, pfad: str, rolle: str = "admin") -> dict:
        r = httpx.get(f"{self.url}{pfad}", headers={"Authorization": f"Bearer {TOKENS[rolle]}"})
        r.raise_for_status()
        return r.json()

    def stand(self) -> dict:
        return self.api("/api/stand")

    def ereignisse(self) -> list[dict]:
        return self.api("/api/ereignisse")["ereignisse"]

    def senden(self, ereignisse: list[dict], rolle: str = "start") -> None:
        r = httpx.post(
            f"{self.url}/api/ereignisse",
            json={"ereignisse": ereignisse},
            headers={"Authorization": f"Bearer {TOKENS[rolle]}"},
        )
        r.raise_for_status()

    def starten(self, *nummern: int, abstand_ms: int = 1000) -> dict[int, int]:
        """Startet Fahrer direkt über die API (schneller als über die Oberfläche)."""
        t0 = int(time.time() * 1000) - 60_000
        zeiten = {nr: t0 + i * abstand_ms for i, nr in enumerate(nummern)}
        self.senden(
            [
                {
                    "uuid": str(uuid.uuid4()),
                    "art": "START",
                    "startnummer": nr,
                    "zeit_ms": t,
                    "erstellt_ms": t,
                    "geraet": "start-api",
                }
                for nr, t in zeiten.items()
            ]
        )
        return zeiten

    def abschliessen(self, vid: int = 1) -> None:
        """Schließt die Veranstaltung über die Admin-Oberfläche ab (Anmeldung, CSRF, Formular)."""
        with httpx.Client(base_url=self.url, follow_redirects=True) as c:
            c.post("/admin/anmelden", data={"token": TOKENS["admin"]})
            csrf = re.search(r'name="csrf" value="([0-9a-f]+)"', c.get("/admin").text).group(1)
            r = c.post(
                f"/admin/v/{vid}/abschliessen",
                data={"csrf": csrf, "bestaetigung": "1", "name": "E2E-Zeitfahren"},
            )
            assert "abgeschlossen" in r.text

    def admin_post(self, pfad: str, **daten) -> str:
        """Formular im Admin abschicken (Anmeldung, CSRF), gibt die Folgeseite zurück."""
        with httpx.Client(base_url=self.url, follow_redirects=True) as c:
            c.post("/admin/anmelden", data={"token": TOKENS["admin"]})
            csrf = re.search(r'name="csrf" value="([0-9a-f]+)"', c.get("/admin").text).group(1)
            return c.post(pfad, data={"csrf": csrf, **daten}).text

    def teilnehmer(self, nr: int) -> dict:
        return next(t for t in self.stand()["teilnehmer"] if t["startnummer"] == nr)

    def seite(self, rolle: str) -> str:
        pfad = (
            "posten" if rolle.startswith("posten") else rolle.split("-")[0]
        )  # „posten-berg“, „start-nord“
        return f"{self.url}/{pfad}?token={TOKENS[rolle]}"


def _freier_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _seed(
    db_pfad: str, anzahl: int, liste: list | None = None, zwei_strecken: bool = False,
    rundkurs: str | None = None,
) -> None:  # fmt: skip
    engine = erzeuge_engine(db_pfad)
    tabellen_anlegen(engine)
    with erzeuge_sessionmaker(engine)() as s:
        v = Veranstaltung(
            name="E2E-Zeitfahren",
            datum=date(2026, 10, 10),
            aktiv=True,
            token_start=TOKENS["start"],
            token_ziel=TOKENS["ziel"],
        )
        s.add(v)
        s.flush()
        for f in liste or []:
            s.add(
                Teilnehmer(
                    veranstaltung_id=v.id,
                    startnummer=f.startnummer,
                    vorname=f.vorname,
                    nachname=f.nachname,
                    geschlecht=f.geschlecht,
                    geburtsjahr=f.geburtsjahr,
                    verein=f.verein,
                    abzeichen=f.abzeichen,
                    ausser_konkurrenz=f.ausser_konkurrenz,
                )
            )
        if zwei_strecken:  # DRA A.1 mit Vorschlag „5 km“ (Jugend) und „20 km“
            from app import saetze, strecken
            from app.models import Wertungssatz

            saetze.standardsaetze_anlegen(s)
            v.klassensatz_id = saetze.standard_klassensatz(s).id
            a1 = saetze.nach_name(s, Wertungssatz, "DRA A.1 flach")
            v.wertungssatz_id = a1.id
            strecken.anlegen(s, v, strecken.vorschlag(saetze.klassen_von(saetze.standard_klassensatz(s)),
                                                       saetze.wertung_von(a1)))  # fmt: skip
        if rundkurs in ("orte", "zielorte", "nord"):  # Weiterer Start- bzw. Zielort
            from app import strecken
            from app.models import Messpunkt, StreckeKlasse

            strecken.sicherstellen(s, v)
            kurz, lang = strecken.strecken_von(s, v)
            if rundkurs in ("orte", "nord"):
                ort = Messpunkt(
                    veranstaltung_id=v.id,
                    position=1,
                    name="Nord",
                    art="start",
                    token=TOKENS["start-nord"],
                )
            else:
                ort = Messpunkt(
                    veranstaltung_id=v.id,
                    position=1,
                    name="Hof",
                    art="ziel",
                    token=TOKENS["ziel-hof"],
                )
            s.add(ort)
            s.flush()
            if rundkurs in ("orte", "nord"):
                kurz.startort_id = ort.id
            else:
                kurz.zielort_id = ort.id
            damen = [sk.klasse for sk in lang.klassen if sk.klasse.startswith("Damen")]
            if rundkurs != "nord":
                lang.klassen = [sk for sk in lang.klassen if not sk.klasse.startswith("Damen")]
                kurz.klassen = [*kurz.klassen, *(StreckeKlasse(klasse=k) for k in damen)]
        elif rundkurs:  # Segmente: eine Strecke, Beispielstrecke mit Messpunkten Kreisel und Berg
            from app import saetze, segmente, strecken
            from app.models import Messpunkt
            from app.simulation import beispiel_teile

            saetze.standardsaetze_anlegen(s)
            v.klassensatz_id = saetze.standard_klassensatz(s).id
            mps = [
                Messpunkt(
                    veranstaltung_id=v.id, position=0, name="Kreisel", token=TOKENS["posten"]
                ),
                Messpunkt(
                    veranstaltung_id=v.id, position=1, name="Berg", token=TOKENS["posten-berg"]
                ),
            ]
            s.add_all(mps)
            s.flush()  # Messpunkte zuerst: IDs 1 und 2 (die Standardorte kommen danach)
            strecken.sicherstellen(s, v)
            strecke = strecken.strecken_von(s, v)[-1]
            ids = {"P1": str(mps[0].id), "P2": str(mps[1].id)}
            strecke.teile_json = segmente.nach_json(beispiel_teile(ids))
            if rundkurs == "gemeinsam":  # Kreisel auch auf der kurzen Strecke (Damen)
                from app.models import StreckeKlasse

                kurz = strecken.strecken_von(s, v)[0]
                kurz.teile_json = segmente.nach_json((
                    segmente.TeilDef((segmente.SegmentDef("Start→Kreisel", segmente.START, ids["P1"]),)),
                    segmente.TeilDef((segmente.SegmentDef("Kreisel→Ziel", ids["P1"], segmente.ZIEL),)),
                ))  # fmt: skip
                damen = [sk.klasse for sk in strecke.klassen if sk.klasse.startswith("Damen")]
                strecke.klassen = [
                    sk for sk in strecke.klassen if not sk.klasse.startswith("Damen")
                ]
                kurz.klassen = [*(sk for sk in kurz.klassen if not sk.klasse.startswith("Damen")),
                                *(StreckeKlasse(klasse=k) for k in damen)]  # fmt: skip
            if rundkurs == "eigen":  # Dazu ein eigener Abschnitt Berg→Kreisel
                import json

                strecke.abschnitte_json = json.dumps({"eigene": [{"id": "a1", "name": "Abfahrt",
                                                                  "von": ids["P2"], "nach": ids["P1"],
                                                                  "art": "wiederkehrend"}]})  # fmt: skip
            if rundkurs == "monitor":  # Zielmonitor: Live-Feld „Kreisel→Berg“
                import json

                s.flush()
                v.monitor_json = json.dumps({"seiten": [{"typ": "ergebnis", "an": True, "dauer": 10, "live": True,
                                                          "abzeichen": True, "strecken": []}],
                                             "live_abschnitte": [f"{strecke.id}:s1.0"]})  # fmt: skip
        for nr in range(1, anzahl + 1) if liste is None else ():
            s.add(
                Teilnehmer(
                    veranstaltung_id=v.id,
                    startnummer=nr,
                    vorname=f"Vorname{nr}",
                    nachname=f"Nachname{nr}",
                    geschlecht="w" if nr % 2 else "m",
                    geburtsjahr=1960 + nr,
                )
            )
        s.commit()
    engine.dispose()


@pytest.fixture
def teilnehmer_anzahl() -> int:
    return 10


def _warte_auf_server(url: str, stop) -> None:
    for _ in range(150):
        try:
            if httpx.get(f"{url}/up").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    stop()
    raise RuntimeError("Server startet nicht")


@pytest.fixture
def teilnehmer_liste():
    """Eigene Teilnehmerliste (z. B. für die Simulation); None = synthetische Teilnehmer."""


@pytest.fixture
def zwei_strecken() -> bool:
    """True: Veranstaltung mit DRA A.1 und den Strecken „5 km“/„20 km“."""
    return False


@pytest.fixture
def rundkurs() -> str | None:
    """None, „messpunkte“ (Beispielstrecke mit Messpunkten Kreisel und Berg, Segmente),
    „monitor“ (dasselbe mit Segmentzeiten auf dem Zielmonitor), „eigen“ (dasselbe mit eigenem
    Abschnitt „Abfahrt“ Berg→Kreisel) oder „gemeinsam“ (mit
    zwei_strecken: Kreisel auch auf der kurzen Strecke der Damen), „orte“ bzw. „zielorte“
    (mit zwei_strecken: Damen auf der kurzen Strecke mit Startort „Nord“ bzw. Zielort „Hof“),
    „nord“ (mit zwei_strecken: Klassen wie vorgeschlagen, die kurze Strecke startet in „Nord“)."""
    return None


@pytest.fixture
def server(tmp_path, teilnehmer_anzahl, teilnehmer_liste, zwei_strecken, rundkurs):
    """Echter Server mit frischer DB.

    Ist TAPTRIAL_E2E_IMAGE gesetzt (scripts/container_e2e.sh), läuft der Server im Docker-Container
    dieses Images, sonst als uvicorn-Prozess.
    """
    daten = tmp_path / "daten"
    daten.mkdir()
    db = daten / "zeitfahren.sqlite3"
    _seed(str(db), teilnehmer_anzahl, teilnehmer_liste, zwei_strecken, rundkurs)
    port = _freier_port()
    secrets_env = {
        "TAPTRIAL_TOKEN_ADMIN": TOKENS["admin"],
    }
    image = umgebung.wert("e2e_image")
    if image:
        daten.chmod(0o777)
        db.chmod(0o666)
        name = f"rcb-e2e-{uuid.uuid4().hex[:8]}"
        env_args = [arg for k, v in secrets_env.items() for arg in ("-e", f"{k}={v}")]
        subprocess.run(
            ["docker", "run", "-d", "--name", name, "-p", f"127.0.0.1:{port}:8000",
             "-v", f"{daten}:/data", *env_args, image],
            check=True, capture_output=True,
        )  # fmt: skip

        def stop():
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
    else:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
             "--port", str(port)],
            cwd=PROJEKT,
            env={**os.environ, "TAPTRIAL_DB_PFAD": str(db), **secrets_env},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )  # fmt: skip

        def stop():
            proc.terminate()
            proc.wait(timeout=5)

    url = f"http://localhost:{port}"
    _warte_auf_server(url, stop)
    yield Server(url)
    stop()


@pytest.fixture
def neues_geraet(browser, playwright):
    """Erzeugt getrennte Browser-Kontexte (eigener Speicher je Gerät)."""
    kontexte = []

    def erzeugen():
        ctx = browser.new_context(**playwright.devices[GERAET], service_workers="allow")
        kontexte.append(ctx)
        return ctx

    yield erzeugen
    for ctx in kontexte:
        ctx.close()


def oeffnen(ctx, server: Server, rolle: str):
    """Öffnet die Geräteseite und wartet, bis der Service Worker die Seite kontrolliert."""
    page = ctx.new_page()
    page.goto(server.seite(rolle))
    page.wait_for_function("navigator.serviceWorker && navigator.serviceWorker.controller")
    page.wait_for_function("window.geraetBereit === true")
    return page


def offline(ctx, an: bool) -> None:
    """Funkloch simulieren."""
    ctx.set_offline(an)


def jetzt_synchronisieren(page) -> None:
    page.evaluate("window.geraet.sync()")


def warte_bis(bedingung, timeout: float = 15.0, text: str = "Bedingung") -> None:
    ende = time.monotonic() + timeout
    while time.monotonic() < ende:
        if bedingung():
            return
        time.sleep(0.2)
    raise AssertionError(f"{text} nicht erfüllt innerhalb {timeout} s")


def einstellungen(page, reiter: str) -> None:
    """Reiter der Einstellungen öffnen: allgemein, klassen, zielmonitor."""
    admin_seite(page, "einstellungen")
    if reiter != "allgemein":
        page.click(f"#einstellungen-reiter a[data-reiter={reiter}]")
        page.wait_for_selector(f"#einstellungen-reiter a[data-reiter={reiter}][aria-current=page]")


def admin_seite(page, name: str) -> None:
    """Seite einer Veranstaltung über die Seitenleiste öffnen (auf dem Handy über „Menü“)."""
    knopf = page.locator("#menue-knopf")
    if knopf.is_visible() and knopf.get_attribute("aria-expanded") != "true":
        knopf.click()
    page.click(f".sl-nav a[data-seite={name}]")
    page.wait_for_selector(f".sl-nav a[data-seite={name}][aria-current=page]", state="attached")
