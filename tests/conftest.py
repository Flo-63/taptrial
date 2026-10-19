import pytest
from fastapi.testclient import TestClient

from app.einstellungen import Einstellungen
from app.main import create_app


@pytest.fixture
def einstellungen(tmp_path) -> Einstellungen:
    return Einstellungen(
        db_pfad=str(tmp_path / "test.sqlite3"),
        token_admin="tok-admin",
    )


@pytest.fixture
def client(einstellungen) -> TestClient:
    with TestClient(create_app(einstellungen)) as c:
        yield c


def csv_importieren(client, vid: int, inhalt: bytes, csrf: str, ersetzen: bool = False):
    """CSV hochladen (→ Vorschau) und übernehmen. Gibt (Vorschau-Antwort, End-Antwort) zurück."""
    vorschau = client.post(f"/admin/v/{vid}/import", data={"csrf": csrf},
                           files={"datei": ("t.csv", inhalt)})  # fmt: skip
    daten = {"csrf": csrf, "modus": "ersetzen" if ersetzen else "ergaenzen", "bestaetigung": "1"}
    return vorschau, client.post(f"/admin/v/{vid}/import/uebernehmen", data=daten)
