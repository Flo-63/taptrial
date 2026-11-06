"""Anmeldung im Modus `proxy` (Forward-Auth): Token-Prüfung, Kontozuordnung, fail closed."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.einstellungen import Einstellungen
from app.main import create_app
from app.models import AdminAktion, Benutzer, Rollenzuweisung
from tests.proxy_hilfe import Anmeldedienst, einstellungen_proxy, schluesselpaar

UID = "a1b2c3"


@pytest.fixture
def dienst():
    d = Anmeldedienst.starten()
    yield d
    d.beenden()


def _app(tmp_path, dienst, **mehr):
    e = Einstellungen(db_pfad=str(tmp_path / "p.sqlite3"), token_admin="tok-admin",
                      **einstellungen_proxy(dienst, **mehr))  # fmt: skip
    return create_app(e)


@pytest.fixture
def proxy(tmp_path, dienst):
    with TestClient(_app(tmp_path, dienst, superadmins="chefin.admin")) as c:
        c.dienst = dienst
        yield c


def _benutzer(client, name):
    with client.app.state.sessionmaker() as s:
        return s.scalars(select(Benutzer).where(Benutzer.benutzername == name)).first()


def _rolle(client, name, rolle="superadmin", **wo):
    with client.app.state.sessionmaker() as s:
        b = s.scalars(select(Benutzer).where(Benutzer.benutzername == name)).first()
        if b is None:
            b = Benutzer(benutzername=name, angelegt_ms=0)
            s.add(b)
        b.rollen.append(Rollenzuweisung(rolle=rolle, **wo))
        s.commit()


def test_gueltiges_token_meldet_an(proxy):
    _rolle(proxy, "erika.muster")
    r = proxy.get("/admin", headers=proxy.dienst.kopf(UID, "erika.muster", name="Erika Muster",
                                                      email="erika@beispiel.test"))  # fmt: skip
    assert r.status_code == 200 and "Kein Zugriff" not in r.text
    b = _benutzer(proxy, "erika.muster")
    assert (b.uid, b.name, b.email) == (UID, "Erika Muster", "erika@beispiel.test")
    assert b.letzte_anmeldung_ms


def test_email_wird_bei_jeder_anmeldung_aktualisiert(proxy):
    _rolle(proxy, "erika.muster")
    proxy.get("/admin", headers=proxy.dienst.kopf(UID, "erika.muster", email="alt@beispiel.test"))
    proxy.get("/admin", headers=proxy.dienst.kopf(UID, "erika.muster", email="neu@beispiel.test"))
    assert _benutzer(proxy, "erika.muster").email == "neu@beispiel.test"


@pytest.mark.parametrize("fall", ["signatur", "abgelaufen", "aussteller", "uid", "name",
                                  "ohne_token", "ohne_benutzername", "kaputt"])  # fmt: skip
def test_ungueltig_meldet_nicht_an(proxy, fall, caplog):
    _rolle(proxy, "erika.muster")
    d = proxy.dienst
    kopf = {
        "signatur": d.kopf(UID, "erika.muster", token=d.token(UID, "erika.muster",
                                                             schluessel=schluesselpaar())),
        "abgelaufen": d.kopf(UID, "erika.muster", token=d.token(UID, "erika.muster", ablauf_s=-120)),
        "aussteller": d.kopf(UID, "erika.muster",
                             token=d.token(UID, "erika.muster", aussteller="https://fremd.test/")),
        "uid": d.kopf("andere-uid", "erika.muster", token=d.token(UID, "erika.muster")),
        "name": d.kopf(UID, "chefin.admin", token=d.token(UID, "erika.muster")),
        "ohne_token": {k: v for k, v in d.kopf(UID, "erika.muster").items() if k != "X-Authentik-Jwt"},
        "ohne_benutzername": d.kopf(UID, "erika.muster", token=d.token(UID, "")),
        "kaputt": d.kopf(UID, "erika.muster", token="x.y.z"),
    }[fall]  # fmt: skip
    r = proxy.get("/admin", headers=kopf)
    assert r.status_code == 401 and "Anmeldung nicht möglich" in r.text
    assert "Admin-Anmeldung (proxy) abgelehnt" in caplog.text
    assert _benutzer(proxy, "erika.muster").uid is None  # nichts übernommen


def test_kopfzeilen_ohne_token_auf_anderen_routen_wirkungslos(proxy):
    """Nur /admin/* liest die Kopfzeilen; öffentliche Seiten und die API ignorieren sie."""
    _rolle(proxy, "chefin.admin")
    kopf = proxy.dienst.kopf(UID, "chefin.admin")
    assert proxy.get("/ergebnisse", headers=kopf).status_code == 200
    assert proxy.get("/api/stand", headers=kopf).status_code == 401
    assert "rcb_admin" not in proxy.cookies  # keine Sitzung außerhalb von /admin


def test_absender_ausserhalb_des_netzes(tmp_path, dienst):
    app = _app(tmp_path, dienst, proxy_netze="198.51.100.0/24", superadmins="chefin.admin")
    kopf = dienst.kopf(UID, "chefin.admin")
    with TestClient(app, client=("203.0.113.9", 5000)) as fremd:
        r = fremd.get("/admin", headers=kopf)
        assert r.status_code == 401
    with TestClient(app, client=("198.51.100.7", 5000)) as traefik:
        assert traefik.get("/admin", headers=kopf).status_code == 200


def test_erstanmeldung_mit_vorab_angelegtem_konto(proxy):
    _rolle(proxy, "max.mustermann", "eventadmin")
    proxy.get("/admin", headers=proxy.dienst.kopf(UID, "max.mustermann"))
    with proxy.app.state.sessionmaker() as s:
        konten = s.scalars(select(Benutzer)).all()
        assert [(b.benutzername, b.uid) for b in konten] == [("max.mustermann", UID)]
        assert "Konto verknüpft" in {a.aktion for a in s.scalars(select(AdminAktion))}


def test_anderer_name_in_gross_klein_uebernimmt_nichts(proxy):
    """Beim Anmeldedienst können „Max.Mustermann“ und „max.mustermann“ zwei Personen sein."""
    _rolle(proxy, "max.mustermann", "eventadmin")
    r = proxy.get("/admin", headers=proxy.dienst.kopf("fremd", "Max.Mustermann"))
    assert r.status_code == 403  # neues Konto ohne Rolle
    assert _benutzer(proxy, "max.mustermann").uid is None
    r = proxy.get("/admin", headers=proxy.dienst.kopf("noch-einer", "Chefin.Admin"))
    assert r.status_code == 403  # kein Superadmin aus TAPTRIAL_SUPERADMINS=chefin.admin


def test_name_mit_anderer_uid_wird_nicht_uebernommen(proxy, caplog):
    _rolle(proxy, "max.mustermann")
    proxy.get("/admin", headers=proxy.dienst.kopf(UID, "max.mustermann"))
    r = proxy.get("/admin", headers=proxy.dienst.kopf("fremde-uid", "max.mustermann"))
    assert r.status_code == 401 and "gehört schon einem anderen Konto" in caplog.text


def test_unbekannter_nutzer_bekommt_konto_ohne_rolle(proxy):
    with proxy.app.state.sessionmaker() as s:
        from app import installation

        installation.speichern(s, {"zugang_kontakt": "die Rennleitung (rl@beispiel.test)"})
    r = proxy.get("/admin", headers=proxy.dienst.kopf(UID, "neu.ling", name="Neu Ling"))
    assert r.status_code == 403 and "Kein Zugriff" in r.text
    assert "Bitte an die Rennleitung (rl@beispiel.test) wenden" in r.text
    with proxy.app.state.sessionmaker() as s:
        b = s.scalars(select(Benutzer)).one()
        assert b.uid == UID and b.rollen == []
        aktion = s.scalars(select(AdminAktion)).one()
        assert (aktion.aktion, aktion.benutzer_id) == ("Konto angelegt", b.id)
    # auch jede andere Admin-Seite: kein Zugriff
    assert proxy.get("/admin/saetze", headers=proxy.dienst.kopf(UID, "neu.ling")).status_code == 403


def test_superadmin_aus_der_einstellung(proxy):
    r = proxy.get("/admin/saetze/branding", headers=proxy.dienst.kopf("uid-chefin", "chefin.admin"))
    assert r.status_code == 200


def test_deaktivierter_benutzer(proxy):
    _rolle(proxy, "erika.muster")
    with proxy.app.state.sessionmaker() as s:
        s.scalars(select(Benutzer)).one().aktiv = False
        s.commit()
    r = proxy.get("/admin", headers=proxy.dienst.kopf(UID, "erika.muster"))
    assert r.status_code == 403 and "deaktiviert" in r.text


def test_superadmin_aus_der_einstellung_ist_nicht_aussperrbar(proxy):
    proxy.get("/admin", headers=proxy.dienst.kopf("uid-chefin", "chefin.admin"))
    with proxy.app.state.sessionmaker() as s:
        s.scalars(select(Benutzer)).one().aktiv = False
        s.commit()
    assert (
        proxy.get("/admin", headers=proxy.dienst.kopf("uid-chefin", "chefin.admin")).status_code
        == 200
    )


def test_passphrase_im_modus_proxy_abgeschaltet(proxy):
    r = proxy.post("/admin/anmelden", data={"token": "tok-admin"}, follow_redirects=False)
    assert r.status_code == 404 and "rcb_admin" not in r.cookies
    assert proxy.get("/admin?token=tok-admin").status_code == 401
    assert proxy.get("/api/stand", headers={"Authorization": "Bearer tok-admin"}).status_code == 401


def test_zugangsgruppe_und_zielgruppe(tmp_path, dienst):
    app = _app(tmp_path, dienst, superadmins="chefin.admin", proxy_gruppe="taptrial")
    with TestClient(app) as c:
        ohne = dienst.kopf("u1", "chefin.admin", groups=["andere"])
        assert c.get("/admin", headers=ohne).status_code == 401
        falsche_aud = dienst.kopf("u1", "chefin.admin", aud="fremd", groups=["taptrial"])
        assert c.get("/admin", headers=falsche_aud).status_code == 401
        gut = dienst.kopf("u1", "chefin.admin", groups=["taptrial"])
        assert c.get("/admin", headers=gut).status_code == 200


def test_ohne_zielgruppe_keine_anmeldung(tmp_path, dienst, caplog):
    """Ohne Zielgruppe gälte ein Token einer anderen Anwendung desselben Anmeldedienstes."""
    with TestClient(_app(tmp_path, dienst, superadmins="chefin.admin", proxy_jwt_audience="")) as c:
        assert "TAPTRIAL_PROXY_JWT_AUDIENCE" in caplog.text
        assert c.get("/admin", headers=dienst.kopf("u1", "chefin.admin")).status_code == 401


def test_abmelden_setzt_kein_neues_cookie(proxy):
    _rolle(proxy, "erika.muster")
    kopf = proxy.dienst.kopf(UID, "erika.muster")
    import re

    csrf = re.search(r'name="csrf" value="([0-9a-f]+)"', proxy.get("/admin", headers=kopf).text)
    r = proxy.post("/admin/abmelden", data={"csrf": csrf.group(1)}, headers=kopf)
    gesetzt = [c for c in r.headers.get_list("set-cookie") if c.startswith("rcb_admin=b")]
    assert not gesetzt and "rcb_admin" not in proxy.cookies


def test_unbekannte_schluessel_id_laedt_nicht_bei_jeder_anfrage(proxy, monkeypatch):
    import jwt

    from tests.proxy_hilfe import schluesselpaar

    _rolle(proxy, "erika.muster")
    proxy.get("/admin", headers=proxy.dienst.kopf(UID, "erika.muster"))  # Schlüssel geladen
    geladen = []
    client = proxy.app.state.proxy_schluessel._client
    echt = client.fetch_data
    monkeypatch.setattr(client, "fetch_data", lambda: geladen.append(1) or echt())
    for i in range(5):
        token = jwt.encode(
            {"sub": UID}, schluesselpaar(), algorithm="RS256", headers={"kid": f"x{i}"}
        )
        assert (
            proxy.get(
                "/admin", headers=proxy.dienst.kopf(UID, "erika.muster", token=token)
            ).status_code
            == 401
        )
    assert geladen == []


def test_ohne_jwks_einstellung_keine_anmeldung(tmp_path, dienst, caplog):
    e = Einstellungen(db_pfad=str(tmp_path / "x.sqlite3"), token_admin="tok-admin",
                      anmeldung="proxy", superadmins="chefin.admin")  # fmt: skip
    with TestClient(create_app(e)) as c:
        assert "TAPTRIAL_PROXY_JWKS_URL" in caplog.text  # beim Start gemeldet
        r = c.get("/admin", headers=dienst.kopf("u1", "chefin.admin"))
        assert r.status_code == 401
        assert c.get("/ergebnisse").status_code == 200  # öffentliche Seiten laufen weiter


def test_abmelden_mit_und_ohne_logout_adresse(tmp_path, dienst):
    for url, erwartet in (("/outpost.goauthentik.io/sign_out", 303), ("", 200)):
        app = _app(tmp_path, dienst, superadmins="chefin.admin", proxy_logout_url=url)
        with TestClient(app) as c:
            kopf = dienst.kopf("u1", "chefin.admin")
            import re

            csrf = re.search(r'name="csrf" value="([0-9a-f]+)"', c.get("/admin", headers=kopf).text)
            r = c.post("/admin/abmelden", data={"csrf": csrf.group(1)}, headers=kopf,
                       follow_redirects=False)  # fmt: skip
            assert r.status_code == erwartet
            if url:
                assert r.headers["location"] == url
            else:
                assert "Browser schließen" in r.text


def test_vorschau_cookie_nach_admin_anfrage(proxy):
    """Öffentliche Seiten erkennen die Admin-Vorschau am Cookie, das /admin setzt."""
    _rolle(proxy, "erika.muster")
    r = proxy.get("/admin", headers=proxy.dienst.kopf(UID, "erika.muster"))
    assert r.status_code == 200 and "rcb_admin" in proxy.cookies
    # nur das Cookie kommt dazu, keine zweite Content-Length (Browser lehnen das ab)
    assert len(r.headers.get_list("content-length")) == 1


def test_oeffentliche_vorschau_nur_fuer_eigene_veranstaltung(proxy):
    from datetime import date

    from app.models import Veranstaltung

    with proxy.app.state.sessionmaker() as s:
        eigen = Veranstaltung(name="Eigene Vorschau", datum=date(2026, 5, 1))
        fremd = Veranstaltung(name="Fremde Vorschau", datum=date(2026, 5, 2))
        s.add_all([eigen, fremd])
        s.commit()
        ids = eigen.id, fremd.id
    _rolle(proxy, "erika.muster", "eventadmin", veranstaltung_id=ids[0])
    proxy.get("/admin", headers=proxy.dienst.kopf(UID, "erika.muster"))  # setzt das Cookie
    eigen_html = proxy.get(f"/ergebnisse?veranstaltung={ids[0]}&vorschau=1").text
    fremd_html = proxy.get(f"/ergebnisse?veranstaltung={ids[1]}&vorschau=1").text
    assert "Eigene Vorschau" in eigen_html and "Fremde Vorschau" not in fremd_html
