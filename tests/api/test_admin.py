"""Admin-Oberfläche über den TestClient."""

import re
import uuid
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app import admin_auth
from app.models import Ereignis, Teilnehmer, Veranstaltung
from tests.conftest import csv_importieren

FIXTURE = Path(__file__).resolve().parents[2] / "config" / "teilnehmer_beispiel.csv"
T0 = int(datetime(2026, 10, 10, 10, 0, tzinfo=ZoneInfo("Europe/Berlin")).timestamp() * 1000)
MIN = 60_000
KOPF = "startnummer;vorname;nachname;geschlecht;geburtsjahr;verein\n"


@pytest.fixture(autouse=True)
def keine_sperre():
    admin_auth._fehlversuche.clear()


def csrf(html: str) -> str:
    return re.search(r'name="csrf" value="([0-9a-f]+)"', html).group(1)


@pytest.fixture
def admin(client):
    r = client.post("/admin/anmelden", data={"token": "tok-admin"}, follow_redirects=False)
    assert r.status_code == 303
    client.csrf = csrf(client.get("/admin").text)
    return client


def post(client, pfad, **daten):
    return client.post(pfad, data={"csrf": client.csrf, **daten}, follow_redirects=True)


@pytest.fixture
def v(admin):
    r = post(
        admin, "/admin/veranstaltungen", name="Kottenforst", datum="2026-10-10", aktivieren="1"
    )
    assert "angelegt" in r.text
    with admin.app.state.sessionmaker() as s:
        v = s.scalars(select(Veranstaltung)).one()
    csv = KOPF + "1;Anna;A;w;1990;RC\n2;Ben;B;m;1985;\n3;Carl;C;m;1970;\n"
    _, r = csv_importieren(admin, v.id, csv.encode(), admin.csrf)
    assert "3 Teilnehmer importiert" in r.text
    return v


def geraet(client, v, art, **kw):
    e = {
        "uuid": str(uuid.uuid4()),
        "art": art,
        "geraet": "test",
        "erstellt_ms": kw.get("zeit_ms") or T0,
        **kw,
    }
    token = v.token_start if art == "START" else v.token_ziel
    r = client.post("/api/ereignisse", json={"ereignisse": [e]},
                    headers={"Authorization": f"Bearer {token}"})  # fmt: skip
    assert r.status_code == 200, r.text
    return e


def stand(client, v):
    return client.get("/api/stand", headers={"Authorization": f"Bearer {v.token_ziel}"}).json()


def tn(client, v, nr):
    return next(t for t in stand(client, v)["teilnehmer"] if t["startnummer"] == nr)


# --- Anmeldung ----------------------------------------------------------------


def test_ohne_sitzung_anmeldeformular_mit_link_zu_ergebnissen(client):
    r = client.get("/admin")
    assert r.status_code == 401
    assert 'name="token"' in r.text and 'name="pin"' not in r.text
    assert 'href="/ergebnisse"' in r.text
    assert client.get("/admin/v/1").status_code == 401


def test_anmelden_mit_token_im_formular(client):
    r = client.post("/admin/anmelden", data={"token": "tok-admin"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    assert client.get("/admin").status_code == 200


def test_falscher_token(client):
    r = client.post("/admin/anmelden", data={"token": "falsch"})
    assert r.status_code == 401 and "Admin-Token falsch" in r.text
    assert client.get("/admin").status_code == 401


def test_alt_link_setzt_sitzung_und_entfernt_token(client):
    r = client.get("/admin?token=tok-admin", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    assert "rcb_admin=" in r.headers["set-cookie"]
    assert client.get("/admin").status_code == 200
    r = client.get("/admin?token=tok-admin", follow_redirects=False)  # mit Sitzung
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    r = client.get("/admin?token=falsch", follow_redirects=False)
    assert r.status_code == 303  # bestehende Sitzung bleibt, Token verschwindet aus der URL


def test_alt_link_mit_falschem_token(client):
    r = client.get("/admin?token=falsch")
    assert r.status_code == 401 and "Admin-Token falsch" in r.text


def test_sperre_15_minuten_nach_5_fehlversuchen(client, caplog, monkeypatch):
    jetzt = [1_000_000.0]
    monkeypatch.setattr(admin_auth.time, "time", lambda: jetzt[0])
    with caplog.at_level("WARNING", logger="app.admin_auth"):
        for _ in range(5):
            assert client.post("/admin/anmelden", data={"token": "falsch"}).status_code == 401
    assert sum("fehlgeschlagen" in m for m in caplog.messages) == 5
    assert "testclient" in caplog.text  # IP im Log
    assert client.post("/admin/anmelden", data={"token": "tok-admin"}).status_code == 429
    jetzt[0] += 14 * 60
    assert client.post("/admin/anmelden", data={"token": "tok-admin"}).status_code == 429
    jetzt[0] += 61
    r = client.post("/admin/anmelden", data={"token": "tok-admin"}, follow_redirects=False)
    assert r.status_code == 303


def test_cookie_eigenschaften_rolle_und_manipulation(client):
    r = client.post("/admin/anmelden", data={"token": "tok-admin"}, follow_redirects=False)
    kopf = r.headers["set-cookie"].lower()
    assert "httponly" in kopf and "samesite=strict" in kopf and "path=/admin" in kopf
    assert "max-age=43200" in kopf  # 12 h
    assert r.headers["set-cookie"].split("=", 1)[1].startswith("admin.")  # Rolle in der Sitzung
    wert = r.headers["set-cookie"].split("=", 1)[1].split(";")[0]
    _rolle, ablauf, sig = wert.split(".")
    client.cookies.set(admin_auth.COOKIE, f"superadmin.{ablauf}.{sig}", path="/admin")
    assert client.get("/admin").status_code == 401  # Rolle nicht fälschbar
    client.cookies.set(admin_auth.COOKIE, "admin.9999999999.falsch", path="/admin")
    assert client.get("/admin").status_code == 401


def test_secure_cookie_bei_https(einstellungen):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(einstellungen), base_url="https://testserver") as c:
        r = c.post("/admin/anmelden", data={"token": "tok-admin"}, follow_redirects=False)
        assert "secure" in r.headers["set-cookie"].lower()


def test_csrf_pflicht(admin):
    r = admin.post("/admin/veranstaltungen", data={"name": "X", "datum": "2026-10-10"})
    assert r.status_code == 403


def test_abmelden(admin):
    post(admin, "/admin/abmelden")
    assert admin.get("/admin").status_code == 401


# --- Einrichtung ------------------------------------------------------------------


def test_alle_seiten_erreichbar(admin, v):
    for pfad in ("", "/stammdaten", "/klassen", "/teilnehmer", "/geraete", "/rennen", "/protokoll",
                 "/ergebnis"):  # fmt: skip
        assert admin.get(f"/admin/v/{v.id}{pfad}").status_code == 200, pfad
    assert "Kottenforst" in admin.get("/admin").text


def test_geraetelinks_mit_qr(admin, v):
    html = admin.get(f"/admin/v/{v.id}/geraete").text
    assert f"/start?token={v.token_start}" in html
    assert f"/ziel?token={v.token_ziel}&amp;sicht=rennleitung" in html
    assert html.count("<svg") == 4  # Start, Ziel, Rennleitung, Zielmonitor


def test_neue_veranstaltung_ist_aktiv_und_geraete_funktionieren(admin, v):
    assert stand(admin, v)["veranstaltung"]["name"] == "Kottenforst"
    assert [t["startnummer"] for t in stand(admin, v)["teilnehmer"]] == [1, 2, 3]


def test_import_fehler_nichts_uebernommen(admin, v):
    csv = KOPF + "4;D;D;m;1980;\n4;E;E;w;1980;\n"
    vorschau, r = csv_importieren(admin, v.id, csv.encode(), admin.csrf)
    assert "Startnummer 4 doppelt" in vorschau.text and "1 Fehler, 0 Warnungen" in vorschau.text
    assert 'id="importieren" disabled' in vorschau.text
    assert "Import nicht möglich" in r.text
    assert len(stand(admin, v)["teilnehmer"]) == 3


def test_import_ersetzen_50(admin, v):
    _, r = csv_importieren(admin, v.id, FIXTURE.read_bytes(), admin.csrf, ersetzen=True)
    assert "50 Teilnehmer importiert" in r.text


def test_teilnehmer_hinzufuegen_bearbeiten_loeschen(admin, v):
    r = post(admin, f"/admin/v/{v.id}/teilnehmer", startnummer="9", vorname="Eva", nachname="Neu",
             geschlecht="w", geburtsjahr="1999", verein="")  # fmt: skip
    assert "Nr. 9 Eva Neu gespeichert" in r.text
    with admin.app.state.sessionmaker() as s:
        t = s.scalars(select(Teilnehmer).where(Teilnehmer.startnummer == 9)).one()
    assert admin.get(f"/admin/v/{v.id}/teilnehmer/{t.id}").status_code == 200
    r = post(admin, f"/admin/v/{v.id}/teilnehmer", tid=str(t.id), startnummer="9", vorname="Eva",
             nachname="Neumann", geschlecht="w", geburtsjahr="1999", verein="RC")  # fmt: skip
    assert "Neumann gespeichert" in r.text
    r = post(admin, f"/admin/v/{v.id}/teilnehmer", startnummer="1", vorname="X", nachname="Y",
             geschlecht="m", geburtsjahr="1980", verein="")  # fmt: skip
    assert "bereits vergeben" in r.text
    r = post(admin, f"/admin/v/{v.id}/teilnehmer/{t.id}/loeschen")
    assert "gelöscht" in r.text


def test_token_erneuern_alter_link_ungueltig(admin, v):
    alt = v.token_start
    r = post(admin, f"/admin/v/{v.id}/token", rolle="start")
    assert "erneuert" in r.text
    assert admin.get("/api/stand", headers={"Authorization": f"Bearer {alt}"}).status_code == 401
    with admin.app.state.sessionmaker() as s:
        neu = s.get(Veranstaltung, v.id).token_start
    assert neu != alt and neu in admin.get(f"/admin/v/{v.id}/geraete").text
    assert admin.get("/api/stand", headers={"Authorization": f"Bearer {neu}"}).status_code == 200


# --- Korrekturen ---------------------------------------------------------------------


def korr(admin, v, aktion, **daten):
    return post(admin, f"/admin/v/{v.id}/korrektur", aktion=aktion, **daten)


def test_jede_korrekturart(admin, v):
    for nr in (1, 2, 3):
        geraet(admin, v, "START", startnummer=nr, zeit_ms=T0 + nr * MIN)
    z1 = geraet(admin, v, "ZIEL", startnummer=1, zeit_ms=T0 + 21 * MIN)  # war Nr. 2
    geraet(admin, v, "ZIEL", startnummer=3, zeit_ms=T0 + 23 * MIN)
    doppelt = geraet(admin, v, "ZIEL", startnummer=3, zeit_ms=T0 + 24 * MIN)

    assert "umgebucht" in korr(admin, v, "umbuchen", stempel=z1["uuid"], startnummer="2",
                               notiz="Nr. 2 war es").text  # fmt: skip
    assert tn(admin, v, 2)["fahrzeit_ms"] == 19 * MIN
    assert "storniert" in korr(admin, v, "stornieren", stempel=doppelt["uuid"], notiz="Doppel").text
    assert tn(admin, v, 3)["fahrzeit_ms"] == 20 * MIN
    assert "getauscht" in korr(admin, v, "tauschen", startnummer="2", startnummer_b="3", art="ZIEL",
                               notiz="vertauscht").text  # fmt: skip
    assert (tn(admin, v, 2)["fahrzeit_ms"], tn(admin, v, 3)["fahrzeit_ms"]) == (21 * MIN, 18 * MIN)
    assert "eingetragen" in korr(admin, v, "manuell", startnummer="1", art="ZIEL",
                                 uhrzeit="10:30:00,5", notiz="Handstoppuhr").text  # fmt: skip
    assert tn(admin, v, 1)["fahrzeit_ms"] == 29 * MIN + 500
    assert "Status DNF" in korr(admin, v, "status", startnummer="1", wert="DNF", notiz="Sturz").text
    assert tn(admin, v, 1)["status"] == "DNF"

    html = admin.get(f"/admin/v/{v.id}/protokoll").text
    for notiz in ("Nr. 2 war es", "Doppel", "vertauscht", "Handstoppuhr", "Sturz"):
        assert notiz in html
    assert "storniert" in html
    gefiltert = admin.get(f"/admin/v/{v.id}/protokoll?nr=1").text
    assert "Sturz" in gefiltert and "Doppel" not in gefiltert  # Storno betraf Nr. 3


def test_korrektur_ohne_notiz_wird_abgewiesen(admin, v):
    r = korr(admin, v, "status", startnummer="1", wert="DNF", notiz=" ")
    assert "Notiz ist Pflicht" in r.text and tn(admin, v, 1)["status"] == "OFFEN"


# --- DSQ und Ergebnisstatus -------------------------------------------------------------


def test_dsq_bestaetigen_verwerfen_und_offiziell(admin, v):
    for nr in (1, 2):
        geraet(admin, v, "START", startnummer=nr, zeit_ms=T0)
        geraet(admin, v, "ZIEL", startnummer=nr, zeit_ms=T0 + (10 + nr) * MIN)
    for nr in (1, 2):
        geraet(admin, v, "STATUS", startnummer=nr, wert="DSQ_VORLAEUFIG", notiz=f"Grund {nr}")
    post(
        admin,
        f"/admin/v/{v.id}/korrektur",
        aktion="status",
        startnummer="3",
        wert="DNS",
        notiz="fehlt",
    )
    seite = admin.get(f"/admin/v/{v.id}/rennen").text
    assert "Vorläufige DSQ (2)" in seite and "Grund 1" in seite
    assert "gesperrt" in admin.get(f"/admin/v/{v.id}/ergebnis").text
    assert ">2 Aufgaben<" in admin.get("/admin").text  # vorläufige DSQ zählen als Aufgaben
    r = post(admin, f"/admin/v/{v.id}/ergebnisstatus", wert="OFFIZIELL", bestaetigung="1", notiz="")
    assert "kann nicht offiziell" in r.text

    r = post(
        admin, f"/admin/v/{v.id}/dsq", startnummer="1", entscheidung="bestaetigen", notiz="Video"
    )
    assert "bestätigt" in r.text and tn(admin, v, 1)["status"] == "DSQ"
    r = post(
        admin,
        f"/admin/v/{v.id}/dsq",
        startnummer="2",
        entscheidung="verwerfen",
        notiz="kein Verstoß",
    )
    assert "verworfen" in r.text and tn(admin, v, 2)["dsq_vorlaeufig"] is None
    assert "Vorläufige DSQ" not in admin.get(f"/admin/v/{v.id}/rennen").text
    assert "Ergebnis offiziell erklären" in admin.get(f"/admin/v/{v.id}/ergebnis").text

    r = post(
        admin,
        f"/admin/v/{v.id}/ergebnisstatus",
        wert="OFFIZIELL",
        bestaetigung="1",
        notiz="geprüft",
    )
    assert "Ergebnis ist offiziell" in r.text and "Offizielles Ergebnis, Stand" in r.text
    r = post(admin, f"/admin/v/{v.id}/ergebnisstatus", wert="VORLAEUFIG", notiz="Einspruch")
    assert "wieder vorläufig" in r.text
    protokoll = admin.get(f"/admin/v/{v.id}/protokoll").text
    assert "geprüft" in protokoll and "Einspruch" in protokoll and "Video" in protokoll


# --- Lebenszyklus und Exporte ----------------------------------------------------------------


def test_abschliessen_exporte_anonymisieren(admin, v):
    geraet(admin, v, "START", startnummer=1, zeit_ms=T0)
    geraet(admin, v, "ZIEL", startnummer=1, zeit_ms=T0 + 15 * MIN)
    vorher = tn(admin, v, 1)
    assert "Namen der Veranstaltung" in post(admin, f"/admin/v/{v.id}/abschliessen").text
    r = post(admin, f"/admin/v/{v.id}/abschliessen", bestaetigung="1", name="falsch")
    assert "Namen der Veranstaltung" in r.text
    r = post(admin, f"/admin/v/{v.id}/abschliessen", bestaetigung="1", name="Kottenforst")
    assert "abgeschlossen" in r.text
    # Gerätetokens ungültig, Admin-Korrekturen abgewiesen, lesen weiter möglich
    assert (
        admin.get("/api/stand", headers={"Authorization": f"Bearer {v.token_ziel}"}).status_code
        == 410
    )
    assert (
        "schreibgeschützt" in korr(admin, v, "status", startnummer="1", wert="DNF", notiz="x").text
    )
    assert admin.get(f"/admin/v/{v.id}/protokoll").status_code == 200
    ergebnis = admin.get(f"/admin/v/{v.id}/export/ergebnis.csv")
    assert ergebnis.headers["content-type"].startswith("text/csv")
    text = ergebnis.content.decode("utf-8-sig")
    assert "Kottenforst" in text and "Vorläufiges Ergebnis" in text and "Anna;A;RC" in text
    assert "15:00,0" in text
    protokoll = admin.get(f"/admin/v/{v.id}/export/protokoll.csv").content.decode("utf-8-sig")
    assert protokoll.count("\n") == 3 and "START" in protokoll and "ZIEL" in protokoll

    r = post(admin, f"/admin/v/{v.id}/anonymisieren", bestaetigung="1", name="falsch")
    assert "Namen der Veranstaltung" in r.text
    r = post(admin, f"/admin/v/{v.id}/anonymisieren", bestaetigung="1", name="Kottenforst")
    assert "anonymisiert" in r.text
    with admin.app.state.sessionmaker() as s:
        t = s.scalars(select(Teilnehmer).where(Teilnehmer.startnummer == 1)).one()
        assert (t.vorname, t.nachname, t.verein, t.geburtsjahr) == ("", "", None, 0)
    seite = admin.get(f"/admin/v/{v.id}/ergebnis").text
    assert "Anna" not in seite and "Damen 31-40" in seite and "15:00,0" in seite
    _ = vorher


def test_faellige_werden_angezeigt(admin, v):
    with admin.app.state.sessionmaker() as s:
        x = s.get(Veranstaltung, v.id)
        x.anonymisieren_ab = date(2020, 1, 1)
        s.commit()
    html = admin.get("/admin").text
    assert "Anonymisierung fällig" in html and "erst abschließen" in html


def test_komplett_loeschen(admin, v):
    geraet(admin, v, "START", startnummer=1, zeit_ms=T0)
    post(admin, "/admin/veranstaltungen", name="Bleibt", datum="2026-11-01")
    r = post(admin, f"/admin/v/{v.id}/loeschen", bestaetigung="1", name="Kottenforst")
    assert "komplett gelöscht" in r.text
    with admin.app.state.sessionmaker() as s:
        assert [x.name for x in s.scalars(select(Veranstaltung))] == ["Bleibt"]
        assert s.scalars(select(Ereignis)).all() == []
        assert s.scalars(select(Teilnehmer)).all() == []


# --- Sperre hinter dem Proxy -------------------------------------------------


@pytest.fixture
def ueber_proxy(einstellungen):
    """TestClient, dessen Anfragen vom lokalen Proxy (127.0.0.1) kommen."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(einstellungen), client=("127.0.0.1", 50000)) as c:
        yield c


def falsch(c, xff, **kopf):
    return c.post("/admin/anmelden", data={"token": "falsch"},
                  headers={"X-Forwarded-For": xff, **kopf})  # fmt: skip


def test_zwei_xff_adressen_sperren_sich_nicht_gegenseitig(ueber_proxy, caplog):
    with caplog.at_level("WARNING", logger="app.admin_auth"):
        for _ in range(5):
            assert falsch(ueber_proxy, "203.0.113.5").status_code == 401
    assert "203.0.113.5" in caplog.text
    r = ueber_proxy.post("/admin/anmelden", data={"token": "tok-admin"},
                         headers={"X-Forwarded-For": "203.0.113.5"}, follow_redirects=False)  # fmt: skip
    assert r.status_code == 429
    r = ueber_proxy.post("/admin/anmelden", data={"token": "tok-admin"},
                         headers={"X-Forwarded-For": "198.51.100.9"}, follow_redirects=False)  # fmt: skip
    assert r.status_code == 303


def test_gefaelschtes_xff_von_fremder_adresse_hilft_nicht(client):
    # Anfragen kommen nicht vom Proxy → X-Forwarded-For wird ignoriert, alle zählen zusammen
    for i in range(5):
        falsch(client, f"203.0.113.{i}")
    r = client.post("/admin/anmelden", data={"token": "tok-admin"},
                    headers={"X-Forwarded-For": "198.51.100.9"})  # fmt: skip
    assert r.status_code == 429


def test_gefaelschte_kette_ueber_proxy(ueber_proxy):
    # Angreifer setzt vorne beliebige Adressen; zählt wird die vom Proxy angehängte (rechte)
    for i in range(5):
        falsch(ueber_proxy, f"10.9.9.{i}, 203.0.113.5")
    r = ueber_proxy.post("/admin/anmelden", data={"token": "tok-admin"},
                         headers={"X-Forwarded-For": "1.1.1.1, 203.0.113.5"})  # fmt: skip
    assert r.status_code == 429


def test_https_und_geraetelinks_hinter_proxy(ueber_proxy):
    r = ueber_proxy.post("/admin/anmelden", data={"token": "tok-admin"},
                         headers={"X-Forwarded-Proto": "https"}, follow_redirects=False)  # fmt: skip
    assert "secure" in r.headers["set-cookie"].lower()
    ueber_proxy.cookies.set(admin_auth.COOKIE, r.cookies[admin_auth.COOKIE], path="/admin")
    html = ueber_proxy.get("/admin", headers={"X-Forwarded-Proto": "https"}).text
    csrf_wert = csrf(html)
    ueber_proxy.post("/admin/veranstaltungen", data={"csrf": csrf_wert, "name": "X",
                     "datum": "2026-10-10", "aktivieren": "1"},
                     headers={"X-Forwarded-Proto": "https"})  # fmt: skip
    seite = ueber_proxy.get("/admin/v/1/geraete", headers={"X-Forwarded-Proto": "https"}).text
    assert 'href="https://testserver/start?token=' in seite


def test_x_forwarded_proto_von_fremden_wird_ignoriert(client):
    r = client.post("/admin/anmelden", data={"token": "tok-admin"},
                    headers={"X-Forwarded-Proto": "https"}, follow_redirects=False)  # fmt: skip
    assert "secure" not in r.headers["set-cookie"].lower()


# --- Merkmale Abzeichen / außer Konkurrenz ----------------------------------------


def _wertungssatz(admin, name):
    from app import saetze
    from app.models import Wertungssatz

    with admin.app.state.sessionmaker() as s:
        return saetze.nach_name(s, Wertungssatz, name).id


def _tid(admin, nr):
    with admin.app.state.sessionmaker() as s:
        return s.scalars(select(Teilnehmer).where(Teilnehmer.startnummer == nr)).one().id


def test_wertung_aus_kein_abzeichen_im_admin(admin, v):
    html = admin.get(f"/admin/v/{v.id}/teilnehmer").text
    assert 'id="wertung-aus"' in html and "Abzeichen</th>" not in html
    assert 'data-merkmal="abzeichen"' not in html and 'name="abzeichen"' not in html
    assert "a. K.</th>" in html
    r = post(
        admin,
        f"/admin/v/{v.id}/teilnehmer/{_tid(admin, 1)}/merkmal",
        merkmal="abzeichen",
        wert="ja",
    )
    assert "Wertung aus" in r.text


def test_merkmale_umschalten_zaehlen_protokollieren(admin, v):
    post(
        admin,
        f"/admin/v/{v.id}/regeln",
        wertungssatz_id=str(_wertungssatz(admin, "DRA A.1 flach")),
        strecke_km="20,0",
    )
    html = admin.get(f"/admin/v/{v.id}/teilnehmer").text
    assert "Abzeichen: 0" in html and "a. K.): 0" in html and "Abzeichen</th>" in html
    assert 'name="abzeichen"' in admin.get(f"/admin/v/{v.id}/teilnehmer/neu").text
    r = post(
        admin,
        f"/admin/v/{v.id}/teilnehmer/{_tid(admin, 1)}/merkmal",
        merkmal="abzeichen",
        wert="ja",
    )
    assert "Nr. 1: Abzeichen ja" in r.text and "Abzeichen: 1" in r.text
    r = post(admin, f"/admin/v/{v.id}/teilnehmer/{_tid(admin, 2)}/merkmal",
             merkmal="ausser_konkurrenz", wert="ja")  # fmt: skip
    assert "a. K.): 1" in r.text
    protokoll = admin.get(f"/admin/v/{v.id}/protokoll").text
    assert "Abzeichen: ja (Nr. 1)" in protokoll and "Außer Konkurrenz: ja (Nr. 2)" in protokoll
    assert "Merkmal" in protokoll


def test_ausser_konkurrenz_im_admin_ergebnis_und_csv(admin, v):
    for nr in (1, 2):
        geraet(admin, v, "START", startnummer=nr, zeit_ms=T0)
        geraet(admin, v, "ZIEL", startnummer=nr, zeit_ms=T0 + (20 + nr) * MIN)
    post(
        admin,
        f"/admin/v/{v.id}/teilnehmer/{_tid(admin, 1)}/merkmal",
        merkmal="ausser_konkurrenz",
        wert="ja",
    )
    html = admin.get(f"/admin/v/{v.id}/ergebnis").text
    zeile1 = html.split('data-nr="1"')[1].split("</tr>")[0]
    assert "a. K." in zeile1 and "21:00,0" in zeile1
    csv = admin.get(f"/admin/v/{v.id}/export/ergebnis.csv").content.decode("utf-8-sig")
    assert ";;1;Anna;A;RC;w;Damen 31-40;21:00,0;a. K.;" in csv
    assert "1;1;2;Ben;B;;m;Herren 41-50;22:00,0;;" in csv


# --- CSV-Hinweis und Beispiel-CSV ---------------------------------------------------


def test_csv_hinweis_aus_der_importdefinition(admin, v):
    from app.teilnehmer_import import JA_NEIN_TEXT, SPALTEN

    html = admin.get(f"/admin/v/{v.id}/import").text
    hinweis = html.split('id="csv-hinweis"')[1].split("</section>")[0]
    for s in SPALTEN:
        assert f"<code>{s.name}</code>" in hinweis, s.name
    assert {"abzeichen", "ausser_konkurrenz"} <= {s.name for s in SPALTEN}
    assert JA_NEIN_TEXT in hinweis and "m, w, d" in hinweis
    assert "<code>;</code> oder <code>,</code>" in hinweis
    assert "Groß-/Kleinschreibung" in hinweis and "nein" in hinweis
    assert hinweis.count("Pflicht") == 5 and hinweis.count("optional") == 3


def test_beispiel_csv_download_und_importierbar(admin, v):
    r = admin.get("/admin/beispiel-teilnehmer.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.content == FIXTURE.read_bytes()
    _, r = csv_importieren(admin, v.id, r.content, admin.csrf, ersetzen=True)
    assert "50 Teilnehmer importiert" in r.text


def test_beispiel_csv_nur_angemeldet(client):
    assert client.get("/admin/beispiel-teilnehmer.csv").status_code == 401
