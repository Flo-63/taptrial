"""Tests mit echten Sicherungen hängen nicht davon ab, welche Sicherungen lokal liegen: Sie sind
grün oder überspringen mit Grund – bei leerem Verzeichnis, nur alten und nur aktuellen Dateien."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.einstellungen import Einstellungen
from app.main import create_app
from tests.unit.test_migration_saetze import alte_datenbank

PROJEKT = Path(__file__).resolve().parents[2]
DATEIEN = [
    "tests/unit/test_migrationen.py", "tests/unit/test_migration_runden.py",
    "tests/unit/test_migration_saetze.py", "tests/unit/test_migration_strecken.py",
    "tests/unit/test_auswertung_sicherungen.py",
]  # fmt: skip
AUSWAHL = "backup or echten or ergebnisse_unveraendert"


def _alt(verzeichnis: Path) -> None:
    alte_datenbank(verzeichnis / "backup-2026-10-01-120000.sqlite3", True)


def _aktuell(verzeichnis: Path) -> None:
    """Wie eine Sicherung nach dem Deploy: Daten eines Rennens, schon im aktuellen Schema."""
    pfad = verzeichnis / "backup-2026-11-04-131233.sqlite3"
    alte_datenbank(pfad, True)
    with TestClient(create_app(Einstellungen.fuer_tests(db_pfad=str(pfad)))):
        pass  # migriert beim Start


def _lauf(verzeichnis: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "TAPTRIAL_BACKUP_VERZEICHNIS": str(verzeichnis), "COLUMNS": "300"}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rA", "-p", "no:cacheprovider", "-n", "0",
         "-k", AUSWAHL, *DATEIEN],
        cwd=PROJEKT, env=env, capture_output=True, text=True, check=False,
    )  # fmt: skip


@pytest.mark.parametrize("fall", ["leer", "nur-alt", "nur-aktuell", "alt-und-aktuell"])
def test_gruen_oder_uebersprungen_egal_welche_sicherungen(tmp_path, fall):
    if "alt" in fall.replace("aktuell", ""):
        _alt(tmp_path)
    if "aktuell" in fall:
        _aktuell(tmp_path)
    r = _lauf(tmp_path)
    assert r.returncode == 0, r.stdout[-3000:]
    assert "failed" not in r.stdout and "error" not in r.stdout.lower()
    abbruch_geprueft = "PASSED tests/unit/test_migrationen.py::test_abbruch_mit_kopie" in r.stdout
    if fall == "nur-aktuell":  # nichts zu migrieren: Abbruchtest überspringt mit Grund
        assert "keine Sicherung mit älterem Schema" in r.stdout and not abbruch_geprueft
    if fall in ("nur-alt", "alt-und-aktuell"):
        assert "keine Sicherung mit älterem Schema" not in r.stdout and abbruch_geprueft
