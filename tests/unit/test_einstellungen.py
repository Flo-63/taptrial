import logging
import re
from dataclasses import fields
from pathlib import Path

import pytest

from app import umgebung
from app.einstellungen import Einstellungen, EinstellungsFehler

PFLICHT = ("TAPTRIAL_TOKEN_ADMIN",)
LANG = "eine-ausreichend-lange-passphrase"
FELDER = ("token_admin", "db_pfad", "klassen_datei", "wertungen_verzeichnis", "vertraute_proxys")


@pytest.fixture(autouse=True)
def saubere_umgebung(monkeypatch):
    for feld in (*FELDER, *(f.name for f in fields(Einstellungen)), "token_start", "token_ziel",
                 "admin_pin"):  # fmt: skip
        for praefix in ("TAPTRIAL_", "RCB_"):
            monkeypatch.delenv(f"{praefix}{feld.upper()}", raising=False)
    umgebung._gemeldet.clear()


def test_einstellungen_aus_umgebung(monkeypatch):
    monkeypatch.setenv("TAPTRIAL_DB_PFAD", "/data/x.sqlite3")
    monkeypatch.setenv("TAPTRIAL_TOKEN_ADMIN", LANG)
    monkeypatch.setenv("TAPTRIAL_ADMIN_PIN", "9999")  # wird ignoriert (keine PIN mehr)
    e = Einstellungen.aus_umgebung()  # Start/Ziel-Tokens kommen aus der DB, nicht nötig
    assert e.db_pfad == "/data/x.sqlite3"
    assert e.token_admin == LANG and not hasattr(e, "admin_pin")
    assert e.klassen_datei.endswith("config/altersklassen.toml")


@pytest.mark.parametrize("feld", FELDER)
def test_alte_namen_gelten_weiter_mit_hinweis(monkeypatch, caplog, feld):
    """Bestehende Installationen mit RCB_… laufen unverändert weiter."""
    wert = "127.0.0.1" if feld == "vertraute_proxys" else f"{LANG}-alt"
    monkeypatch.setenv("RCB_TOKEN_ADMIN", LANG)
    monkeypatch.setenv(f"RCB_{feld.upper()}", wert)
    with caplog.at_level(logging.WARNING, logger="app.umgebung"):
        e = Einstellungen.aus_umgebung()
        Einstellungen.aus_umgebung()  # zweites Lesen: kein weiterer Hinweis
    assert getattr(e, feld) == wert
    zeilen = [r.getMessage() for r in caplog.records if f"RCB_{feld.upper()}" in r.getMessage()]
    assert len(zeilen) == 1 and "veraltet" in zeilen[0] and f"TAPTRIAL_{feld.upper()}" in zeilen[0]


def test_neuer_name_gewinnt_ohne_hinweis(monkeypatch, caplog):
    monkeypatch.setenv("TAPTRIAL_TOKEN_ADMIN", LANG + "-neu")
    monkeypatch.setenv("RCB_TOKEN_ADMIN", LANG + "-alt")
    with caplog.at_level(logging.WARNING, logger="app.umgebung"):
        assert Einstellungen.aus_umgebung().token_admin == LANG + "-neu"
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


def _env_beispiel() -> str:
    text = (Path(__file__).resolve().parents[2] / ".env.example").read_text(encoding="utf-8")
    return re.search(r"^TAPTRIAL_TOKEN_ADMIN=(.*)$", text, re.MULTILINE).group(1)


def test_platzhalter_aus_env_example_startet_nicht(monkeypatch):
    monkeypatch.setenv("TAPTRIAL_TOKEN_ADMIN", _env_beispiel())
    with pytest.raises(
        EinstellungsFehler, match=r"Platzhalter aus \.env\.example.*App startet nicht"
    ):
        Einstellungen.aus_umgebung()


@pytest.mark.parametrize("wert", ["kurz", "x" * 23, "x" * 20 + "    "])
def test_zu_kurze_passphrase_startet_nicht(monkeypatch, wert):
    monkeypatch.setenv("TAPTRIAL_TOKEN_ADMIN", wert)
    with pytest.raises(
        EinstellungsFehler, match="zu kurz .mindestens 24 Zeichen.*App startet nicht"
    ):
        Einstellungen.aus_umgebung()


def test_alter_name_wird_in_der_meldung_genannt(monkeypatch):
    monkeypatch.setenv("RCB_TOKEN_ADMIN", "kurz")
    with pytest.raises(EinstellungsFehler, match="zu kurz"):
        Einstellungen.aus_umgebung()


def test_passphrase_ab_24_zeichen_gilt(monkeypatch):
    monkeypatch.setenv("TAPTRIAL_TOKEN_ADMIN", "x" * 24)
    assert Einstellungen.aus_umgebung().token_admin == "x" * 24


def test_anmeldung_standard_token_und_proxy_einstellungen(monkeypatch):
    monkeypatch.setenv("TAPTRIAL_TOKEN_ADMIN", LANG)
    e = Einstellungen.aus_umgebung()
    assert e.anmeldung == "token" and not e.proxy
    monkeypatch.setenv("TAPTRIAL_ANMELDUNG", "Proxy")
    monkeypatch.setenv("TAPTRIAL_SUPERADMINS", "Erika.Muster, max.m ,")
    monkeypatch.setenv("TAPTRIAL_PROXY_JWT_AUDIENCE", "taptrial")
    monkeypatch.setenv("TAPTRIAL_PROXY_JWKS_URL", "https://a.test/jwks/")
    monkeypatch.setenv("TAPTRIAL_PROXY_HEADER_UID", "X-Remote-Uid")
    e = Einstellungen.aus_umgebung()
    assert e.proxy and e.superadmin_namen == {"Erika.Muster", "max.m"}  # Groß/Klein zählt
    assert e.proxy_header_uid == "X-Remote-Uid" and e.proxy_fehlt() == ["TAPTRIAL_PROXY_JWT_ISSUER"]


@pytest.mark.parametrize(("name", "wert", "text"), [
    ("TAPTRIAL_ANMELDUNG", "lokal", "„token“ oder „proxy“"),
    ("TAPTRIAL_PROXY_JWKS_URL", "file:///etc/jwks.json", "https://"),
    ("TAPTRIAL_PROXY_NETZE", "198.51.100.0/99", "TAPTRIAL_PROXY_NETZE"),
])  # fmt: skip
def test_ungueltige_anmeldeeinstellung_startet_nicht(monkeypatch, name, wert, text):
    monkeypatch.setenv("TAPTRIAL_TOKEN_ADMIN", LANG)
    monkeypatch.setenv(name, wert)
    with pytest.raises(EinstellungsFehler, match=text):
        Einstellungen.aus_umgebung()
