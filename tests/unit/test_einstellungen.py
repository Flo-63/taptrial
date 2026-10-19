import pytest

from app.einstellungen import Einstellungen, EinstellungsFehler

PFLICHT = ("RCB_TOKEN_ADMIN",)


def test_einstellungen_aus_umgebung(monkeypatch):
    monkeypatch.setenv("RCB_DB_PFAD", "/data/x.sqlite3")
    monkeypatch.delenv("RCB_TOKEN_START", raising=False)
    monkeypatch.delenv("RCB_TOKEN_ZIEL", raising=False)
    monkeypatch.setenv("RCB_TOKEN_ADMIN", "a")
    monkeypatch.setenv("RCB_ADMIN_PIN", "9999")  # wird ignoriert (keine PIN mehr)
    e = Einstellungen.aus_umgebung()  # Start/Ziel-Tokens kommen aus der DB, nicht nötig
    assert e.db_pfad == "/data/x.sqlite3"
    assert e.token_admin == "a" and not hasattr(e, "admin_pin")
    assert e.klassen_datei.endswith("config/altersklassen.toml")


@pytest.mark.parametrize("fehlt", PFLICHT)
def test_fehlende_pflichtvariable_ist_fehler(monkeypatch, fehlt):
    for name in PFLICHT:
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv(fehlt, "  ")  # leer zählt als fehlend
    with pytest.raises(EinstellungsFehler, match=fehlt):
        Einstellungen.aus_umgebung()


def test_alle_fehlenden_werden_genannt(monkeypatch):
    for name in PFLICHT:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(EinstellungsFehler) as e:
        Einstellungen.aus_umgebung()
    assert all(name in str(e.value) for name in PFLICHT)


def test_zufallswerte_nur_ueber_fuer_tests():
    e = Einstellungen.fuer_tests(token_admin="a")
    assert e.token_admin == "a"
    with pytest.raises(TypeError):
        Einstellungen()  # type: ignore[call-arg]
