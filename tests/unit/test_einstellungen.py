import logging

import pytest

from app import umgebung
from app.einstellungen import Einstellungen, EinstellungsFehler

PFLICHT = ("TAPTRIAL_TOKEN_ADMIN",)
FELDER = ("token_admin", "db_pfad", "klassen_datei", "wertungen_verzeichnis", "vertraute_proxys")


@pytest.fixture(autouse=True)
def saubere_umgebung(monkeypatch):
    for feld in (*FELDER, "token_start", "token_ziel", "admin_pin"):
        for praefix in ("TAPTRIAL_", "RCB_"):
            monkeypatch.delenv(f"{praefix}{feld.upper()}", raising=False)
    umgebung._gemeldet.clear()


def test_einstellungen_aus_umgebung(monkeypatch):
    monkeypatch.setenv("TAPTRIAL_DB_PFAD", "/data/x.sqlite3")
    monkeypatch.setenv("TAPTRIAL_TOKEN_ADMIN", "a")
    monkeypatch.setenv("TAPTRIAL_ADMIN_PIN", "9999")  # wird ignoriert (keine PIN mehr)
    e = Einstellungen.aus_umgebung()  # Start/Ziel-Tokens kommen aus der DB, nicht nötig
    assert e.db_pfad == "/data/x.sqlite3"
    assert e.token_admin == "a" and not hasattr(e, "admin_pin")
    assert e.klassen_datei.endswith("config/altersklassen.toml")


@pytest.mark.parametrize("feld", FELDER)
def test_alte_namen_gelten_weiter_mit_hinweis(monkeypatch, caplog, feld):
    """Bestehende Installationen mit RCB_… laufen unverändert weiter."""
    wert = "127.0.0.1" if feld == "vertraute_proxys" else "alt-wert"
    monkeypatch.setenv("RCB_TOKEN_ADMIN", "a")
    monkeypatch.setenv(f"RCB_{feld.upper()}", wert)
    with caplog.at_level(logging.WARNING, logger="app.umgebung"):
        e = Einstellungen.aus_umgebung()
        Einstellungen.aus_umgebung()  # zweites Lesen: kein weiterer Hinweis
    assert getattr(e, feld) == wert
    zeilen = [r.getMessage() for r in caplog.records if f"RCB_{feld.upper()}" in r.getMessage()]
    assert len(zeilen) == 1 and "veraltet" in zeilen[0] and f"TAPTRIAL_{feld.upper()}" in zeilen[0]


def test_neuer_name_gewinnt_ohne_hinweis(monkeypatch, caplog):
    monkeypatch.setenv("TAPTRIAL_TOKEN_ADMIN", "neu")
    monkeypatch.setenv("RCB_TOKEN_ADMIN", "alt")
    with caplog.at_level(logging.WARNING, logger="app.umgebung"):
        assert Einstellungen.aus_umgebung().token_admin == "neu"
    assert not caplog.records


@pytest.mark.parametrize("fehlt", PFLICHT)
def test_fehlende_pflichtvariable_ist_fehler(monkeypatch, fehlt):
    for name in PFLICHT:
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv(fehlt, "  ")  # leer zählt als fehlend
    with pytest.raises(EinstellungsFehler, match=fehlt):
        Einstellungen.aus_umgebung()


def test_alle_fehlenden_werden_genannt(monkeypatch):
    with pytest.raises(EinstellungsFehler) as e:
        Einstellungen.aus_umgebung()
    assert all(name in str(e.value) for name in PFLICHT)
    assert "Admin-Passphrase" in str(e.value) and ".env.example" in str(e.value)


def test_zufallswerte_nur_ueber_fuer_tests():
    e = Einstellungen.fuer_tests(token_admin="a")
    assert e.token_admin == "a"
    with pytest.raises(TypeError):
        Einstellungen()  # type: ignore[call-arg]
