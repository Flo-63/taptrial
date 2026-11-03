"""S 4: Sperre nach Fehlversuchen an der API, öffentliche Sichtbarkeit, Vorschau nur für den Admin."""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import auth
from app import korrekturen as k
from app.models import Veranstaltung
from app.proxy import absender
from tests.api import test_admin
from tests.api.test_admin import T0, geraet

admin, v, keine_sperre = test_admin.admin, test_admin.v, test_admin.keine_sperre  # Fixtures


def stand(client, token, **kopf):
    return client.get("/api/stand", headers={"Authorization": f"Bearer {token}", **kopf})


def sperren(client):
    for i in range(auth.API_MAX_FEHLVERSUCHE):
        assert stand(client, f"falsch-{i}").status_code == 401
    assert stand(client, "falsch-noch-einer").status_code == 429


def test_falsche_tokens_sperren_den_absender_nicht_die_geraete(admin, v):
    sperren(admin)
    assert stand(admin, v.token_start).status_code == 200  # Gerät im Rennen läuft weiter
    assert stand(admin, v.token_ziel).status_code == 200
    # auch der richtige Admin-Token hilft dem gesperrten Absender nicht
    assert stand(admin, "tok-admin").status_code == 429
    # andere Absender bleiben frei
    anderer = TestClient(admin.app, client=("203.0.113.9", 1234))
    assert stand(anderer, "tok-admin").status_code == 200


def test_altes_geraet_zaehlt_nur_einmal(admin, v):
    for _ in range(auth.API_MAX_FEHLVERSUCHE * 3):
        assert stand(admin, "alter-link").status_code == 401
    assert stand(admin, "tok-admin").status_code == 200


def test_sperre_endet_nach_der_frist(admin, v, monkeypatch):
    sperren(admin)
    jetzt = auth.time.time()
    monkeypatch.setattr(auth.time, "time", lambda: jetzt + auth.API_SPERRE_S + 1)
    assert stand(admin, "tok-admin").status_code == 200


def test_anmeldung_bleibt_von_der_api_sperre_getrennt(admin, v):
    """Falsche Gerätelinks im selben Netz sperren die Anmeldung des Admins nicht."""
    sperren(admin)
    r = admin.post("/admin/anmelden", data={"token": "tok-admin"}, follow_redirects=False)
    assert r.status_code == 303


def test_geraeteseite_bei_gesperrtem_absender_kein_fehler(admin, v):
    sperren(admin)
    assert admin.get("/start?token=falsch-weiter").status_code == 200
    assert f"token={v.token_start}" in admin.get(f"/start?token={v.token_start}").text


def test_speicher_bleibt_begrenzt_und_die_sperre_besteht(admin, v, monkeypatch):
    sperren(admin)
    monkeypatch.setattr(auth, "API_MAX_ABSENDER", 3)
    jetzt = auth.time.time()
    alle = admin.app.state.api_fehlversuche
    alle["alt-1"], alle["alt-2"] = {"x": jetzt - 100}, {"x": jetzt - 50}
    for i in range(3):
        stand(TestClient(admin.app, client=(f"203.0.113.{i}", 1)), "falsch")
    assert len(alle) <= 3 and "alt-1" not in alle  # der älteste weicht, nicht alles
    assert stand(admin, "falsch-noch-einer").status_code == 429


def test_anmeldezaehler_bleibt_begrenzt(client, monkeypatch):
    from app import admin_auth

    admin_auth._fehlversuche.clear()
    monkeypatch.setattr(admin_auth, "MAX_ABSENDER", 3)
    for i in range(6):
        anderer = TestClient(client.app, client=(f"203.0.113.{i}", 1))
        anderer.post("/admin/anmelden", data={"token": "falsch"})
    assert len(admin_auth._fehlversuche) <= 3
    admin_auth._fehlversuche.clear()


@pytest.mark.parametrize(
    ("ip", "schluessel"),
    [
        ("203.0.113.9", "203.0.113.9"),
        ("2001:db8:1:2:3:4:5:6", "2001:db8:1:2::/64"),
        ("2001:db8:1:2:ffff:ffff:ffff:ffff", "2001:db8:1:2::/64"),
        ("::ffff:203.0.113.9", "203.0.113.9"),
        ("?", "?"),
    ],
)
def test_absender(ip, schluessel):
    assert absender(ip) == schluessel


# --- Öffentlich sichtbar ----------------------------------------------------------------------


@pytest.fixture
def gast(admin):
    return TestClient(admin.app)


def _andere_aktiv(admin):
    with admin.app.state.sessionmaker() as s:
        s.add(Veranstaltung(name="Andere", datum=date(2026, 11, 1), aktiv=False))
        s.commit()
        andere = s.scalars(select(Veranstaltung).where(Veranstaltung.name == "Andere")).one()
        return andere.id


def _setzen(admin, vid, **felder):
    with admin.app.state.sessionmaker() as s:
        v = s.get(Veranstaltung, vid)
        for name, wert in felder.items():
            setattr(v, name, wert)
        s.commit()


def _sichtbar(c, vid):
    return c.get(f"/api/oeffentlich?veranstaltung={vid}").json().get("veranstaltung") is not None


def test_nicht_aktiv_und_laufend_nur_fuer_den_admin(admin, v, gast):
    geraet(admin, v, "START", startnummer=1, zeit_ms=T0)
    assert _sichtbar(gast, v.id)  # aktiv: öffentlich
    _setzen(admin, v.id, aktiv=False)
    assert not _sichtbar(gast, v.id) and _sichtbar(admin, v.id)
    for pfad in ("/ergebnisse", "/ergebnisse/inhalt", "/ergebnisse/druck"):
        assert v.name not in gast.get(f"{pfad}?veranstaltung={v.id}").text, pfad
        assert v.name in admin.get(f"{pfad}?veranstaltung={v.id}").text, pfad
    assert gast.get(f"/ergebnisse.csv?veranstaltung={v.id}").status_code == 404
    assert admin.get(f"/ergebnisse.csv?veranstaltung={v.id}").status_code == 200


def test_abgeschlossen_oeffentlich_erst_mit_offiziellem_ergebnis(admin, v, gast):
    geraet(admin, v, "START", startnummer=1, zeit_ms=T0)
    _setzen(admin, v.id, aktiv=False, zustand="abgeschlossen")
    assert not _sichtbar(gast, v.id)
    _setzen(admin, v.id, zustand="laufend", aktiv=True)
    geraet(admin, v, "ZIEL", startnummer=1, zeit_ms=T0 + 60_000)
    with admin.app.state.sessionmaker() as s:
        k.ergebnis_offiziell(s, s.get(Veranstaltung, v.id), "fertig")
    _setzen(admin, v.id, aktiv=False, zustand="abgeschlossen")
    assert _sichtbar(gast, v.id)
    _setzen(admin, v.id, zustand="anonymisiert")
    assert _sichtbar(gast, v.id)


def test_vorschau_wirkt_nur_mit_anmeldung(admin, v, gast):
    _setzen(admin, v.id, aktiv=False)
    assert not _sichtbar(gast, f"{v.id}&vorschau=1")
    gast.cookies.set("rcb_admin", "admin.9999999999.gefaelscht", path="/")
    assert not _sichtbar(gast, f"{v.id}&vorschau=1")
    for kaputt in ("admin.1.äöü", "admin.².x", "admin.99999999999999999999999.x"):
        kopf = {"Cookie": f"rcb_admin={kaputt}".encode()}
        assert TestClient(admin.app).get("/ergebnisse", headers=kopf).status_code == 200
    assert _sichtbar(admin, f"{v.id}&vorschau=1")


def test_vorschau_vor_der_freigabe_nur_mit_anmeldung(admin, v, gast):
    _setzen(admin, v.id, einrichtung_schritt=2)  # aktiv, aber nicht freigegeben, keine Zeiten
    daten = gast.get("/api/oeffentlich?vorschau=1").json()
    assert daten["keine_ergebnisse"]
    assert "keine_ergebnisse" not in admin.get("/api/oeffentlich?vorschau=1").json()


def test_abmelden_loescht_die_sitzung_fuer_alle_seiten(admin, v):
    _setzen(admin, v.id, aktiv=False)
    assert _sichtbar(admin, v.id)
    r = admin.post("/admin/abmelden", data={"csrf": admin.csrf}, follow_redirects=False)
    kopf = [w.lower() for k_, w in r.headers.multi_items() if k_.lower() == "set-cookie"]
    assert any("path=/;" in w or w.endswith("path=/") for w in kopf)
    assert any("path=/admin" in w for w in kopf)  # auch Sitzungen von vor dem Update
    assert not _sichtbar(admin, v.id)


@pytest.mark.parametrize("wert", [10**30, -1, 0])
def test_ungewoehnliche_id_ist_kein_fehler(admin, gast, v, wert):
    assert gast.get(f"/ergebnisse?veranstaltung={wert}").status_code == 200


# --- S 7 ---------------------------------------------------------------------------------------


def test_abgelaufene_sitzung_fuehrt_zur_anmeldung(admin, v):
    from app import admin_auth

    class Anfrage:
        app = admin.app

    alt = admin_auth.cookie_wert(Anfrage, jetzt=auth.time.time() - admin_auth.SITZUNG_S - 1)
    admin.cookies.set(admin_auth.COOKIE, alt, path="/")
    r = admin.get(f"/admin/v/{v.id}")
    assert r.status_code == 401 and 'name="token"' in r.text


@pytest.mark.parametrize("ziel", ["//evil.example", "/admin//evil.example", "https://evil.example"])
def test_ruecksprung_nur_auf_eigene_adressen(client, ziel):
    html = client.get("/ergebnisse/druck", params={"zurueck": ziel}).text
    assert "evil.example" not in html


def test_groesse_der_anfrage_ist_begrenzt_auch_ohne_laenge(client):
    from app.main import MAX_ANFRAGE_BYTES

    zu_viel = b"x" * (MAX_ANFRAGE_BYTES + 1)
    assert client.post("/api/ereignisse", content=zu_viel).status_code == 413

    def stueckweise():
        for _ in range(11):
            yield b"x" * 1_000_000

    r = client.post("/api/ereignisse", content=stueckweise())
    assert r.status_code == 413 and "zu groß" in r.text
    assert client.get("/up").status_code == 200


def test_normale_geraetedatei_passt_unter_die_grenze(admin, v):
    r = admin.post(f"/admin/v/{v.id}/geraetedatei", data={"csrf": admin.csrf},
                   files={"datei": ("x.json", b"x" * 2_000_001)})  # fmt: skip
    assert r.status_code == 200 and "zu groß" in r.text
