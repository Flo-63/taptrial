"""Gesamtzeit, Rang und Status bleiben über alle Sicherungen unverändert (Stand vor den Änderungen
aus dem Sicherheits-Review). Nur Prüfsummen im Repo, keine Namen; gerechnet wird auf Kopien."""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import korrekturen as k
from app.einstellungen import Einstellungen
from app.main import create_app
from app.models import Veranstaltung
from tests.unit.test_migration_saetze import BACKUPS

ERWARTET = json.loads((Path(__file__).parent / "sicherungen_ergebnisse.json").read_text())
PRUEFBAR = [b for b in BACKUPS if b.name in ERWARTET]


def _kopie(original: Path, ziel: Path) -> None:
    quelle = sqlite3.connect(f"file:{original}?mode=ro&immutable=1", uri=True)
    kopie = sqlite3.connect(ziel)
    quelle.backup(kopie)
    kopie.close()
    quelle.close()


@pytest.mark.skipif(not PRUEFBAR, reason="keine bekannte Sicherung unter ~/rcb-backups/")
@pytest.mark.parametrize("backup", PRUEFBAR, ids=lambda p: p.name)
def test_ergebnisse_unveraendert(tmp_path, backup):
    pfad = tmp_path / "kopie.sqlite3"
    _kopie(backup, pfad)
    app = create_app(Einstellungen.fuer_tests(db_pfad=str(pfad)))
    with TestClient(app), app.state.sessionmaker() as s:
        kern = {}
        for v in s.scalars(select(Veranstaltung).order_by(Veranstaltung.id)):
            kern[str(v.id)] = [
                [z.startnummer, z.status.value, z.start_ms, z.ziel_ms, z.fahrzeit_ms, z.rang_gesamt,
                 z.rang_klasse, z.klasse, z.strecke]
                for z in k.ergebnis(s, v).zeilen
            ]  # fmt: skip
    assert (
        hashlib.sha256(json.dumps(kern, sort_keys=True).encode()).hexdigest()
        == ERWARTET[backup.name]
    )
