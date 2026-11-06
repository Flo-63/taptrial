"""Benutzerverwaltung: Konten, Rollen, Event-Admins einer Veranstaltung, Protokoll."""

import re
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.einstellungen import Einstellungen
from app.main import create_app
from app.models import AdminAktion, Benutzer, Rollenzuweisung, Veranstaltung, Verein
from tests.api import test_admin
from tests.api.test_admin import post
from tests.proxy_hilfe import Anmeldedienst, einstellungen_proxy

admin = test_admin.admin  # Fixture (Modus token, Passphrase)


def _konto(client, name):
    with client.app.state.sessionmaker() as s:
        b = s.scalars(select(Benutzer).where(Benutzer.benutzername == name)).first()
        return b and (b.id, sorted(r.rolle for r in b.rollen), b.aktiv)


def _protokoll(client):
    with client.app.state.sessionmaker() as s:
        return [
            (a.benutzer, a.aktion, a.text)
            for a in s.scalars(select(AdminAktion).order_by(AdminAktion.id))
        ]


def _veranstaltung(client, name="Kottenforst", verein_id=None):
    with client.app.state.sessionmaker() as s:
        v = Veranstaltung(name=name, datum=date(2026, 5, 1), verein_id=verein_id)
        s.add(v)
        s.commit()
        return v.id


def test_konto_anlegen_und_rollen_im_modus_token(admin):
    r = admin.get("/admin/benutzer")
    assert r.status_code == 200 and "Anmeldung zurzeit mit der Passphrase" in r.text
    r = post(admin, "/admin/benutzer", benutzername="erika.muster", name="Erika  Muster")
    assert "Konto Erika Muster (erika.muster) angelegt." in r.text
    assert "gibt es schon" in post(admin, "/admin/benutzer", benutzername="erika.muster").text
    assert "Benutzername:" in post(admin, "/admin/benutzer", benutzername="Erika Muster").text
    bid = _konto(admin, "erika.muster")[0]
    vid = _veranstaltung(admin)
    r = post(admin, f"/admin/benutzer/{bid}/rolle", rolle="eventadmin", veranstaltung=str(vid))
    assert "Event-Admin „Kottenforst“ an Erika Muster (erika.muster) vergeben." in r.text
    assert (
        "bitte eine Veranstaltung"
        in post(admin, f"/admin/benutzer/{bid}/rolle", rolle="eventadmin").text
    )
    post(admin, f"/admin/benutzer/{bid}/rolle", rolle="vereinsadmin")
    assert _konto(admin, "erika.muster")[1] == ["eventadmin", "vereinsadmin"]
    with admin.app.state.sessionmaker() as s:
        rid = (
            s.scalars(select(Rollenzuweisung).where(Rollenzuweisung.rolle == "vereinsadmin"))
            .one()
            .id
        )
    r = post(admin, f"/admin/benutzer/{bid}/rolle/{rid}/entziehen")
    assert "entzogen" in r.text and _konto(admin, "erika.muster")[1] == ["eventadmin"]
    protokoll = _protokoll(admin)
    assert ("Admin (Passphrase)", "Konto angelegt", "Erika Muster (erika.muster)") in protokoll
    assert any(a[1] == "Rolle vergeben" and "Kottenforst" in a[2] for a in protokoll)
    assert any(a[1] == "Rolle entzogen" for a in protokoll)
    assert not any(
        a[1] == "Benutzer anlegen" for a in protokoll
    )  # kein zweiter, allgemeiner Eintrag
    assert sum(1 for a in protokoll if a[1] == "Konto angelegt") == 1  # abgelehnte nicht


def test_ohne_rolle_oben_hervorgehoben(admin):
    post(admin, "/admin/benutzer", benutzername="b.mit", name="Anna Mit")
    post(admin, "/admin/benutzer", benutzername="z.ohne", name="Zora Ohne")
    bid = _konto(admin, "b.mit")[0]
    post(admin, f"/admin/benutzer/{bid}/rolle", rolle="superadmin")
    html = admin.get("/admin/benutzer").text
    assert html.index('data-benutzer="z.ohne"') < html.index('data-benutzer="b.mit"')
    assert re.search(r'data-benutzer="z.ohne" class="zeile-neu', html)


def test_deaktivieren(admin):
    post(admin, "/admin/benutzer", benutzername="erika.muster")
    bid = _konto(admin, "erika.muster")[0]
    assert "deaktiviert" in post(admin, f"/admin/benutzer/{bid}/aktiv", aktiv="0").text
    assert _konto(admin, "erika.muster")[2] is False
    post(admin, f"/admin/benutzer/{bid}/aktiv", aktiv="1")
    assert _konto(admin, "erika.muster")[2] is True


def test_event_admin_von_der_veranstaltung_aus(admin):
    vid = _veranstaltung(admin)
    post(admin, "/admin/benutzer", benutzername="erika.muster", name="Erika Muster")
    bid = _konto(admin, "erika.muster")[0]
    assert "Event-Admins" in admin.get(f"/admin/v/{vid}").text  # Seitenleiste
    r = post(admin, f"/admin/v/{vid}/admins", benutzer=str(bid))
    assert "Event-Admin „Kottenforst“ an Erika Muster (erika.muster) vergeben." in r.text
    assert 'data-benutzer="erika.muster"' in r.text
    with admin.app.state.sessionmaker() as s:
        rid = s.scalars(select(Rollenzuweisung)).one().id
    assert "entzogen" in post(admin, f"/admin/v/{vid}/admins/{rid}/entziehen").text
    assert _konto(admin, "erika.muster")[1] == []
    with admin.app.state.sessionmaker() as s:  # das Protokoll gehört zur Veranstaltung
        assert {
            a.veranstaltung_id for a in s.scalars(select(AdminAktion)) if "Rolle" in a.aktion
        } == {vid}


def test_kontakt_fuer_kein_zugriff_im_branding(admin):
    r = post(
        admin,
        "/admin/saetze/branding",
        vereinsname="RCB",
        zugang_kontakt=" Rennleitung  (rl@beispiel.test) ",
    )
    assert "gespeichert" in r.text and 'value="Rennleitung (rl@beispiel.test)"' in r.text


# --- Vereinsadmin (Modus proxy) ---------------------------------------------------------------


@pytest.fixture
def vereinsadmin(tmp_path):
    dienst = Anmeldedienst.starten()
    e = Einstellungen(db_pfad=str(tmp_path / "v.sqlite3"), token_admin="tok-admin",
                      **einstellungen_proxy(dienst, superadmins="chefin"))  # fmt: skip
    with TestClient(create_app(e)) as c:
        with c.app.state.sessionmaker() as s:
            eigen = Veranstaltung(name="Eigen", datum=date(2026, 5, 1))
            s.add(eigen)
            s.flush()
            fremd_verein = Verein(name="Anderer")
            s.add(fremd_verein)
            s.flush()
            fremd = Veranstaltung(name="Fremd", datum=date(2026, 5, 2), verein_id=fremd_verein.id)
            va = Benutzer(benutzername="vera", name="Vera Verein", angelegt_ms=0)
            va.rollen.append(Rollenzuweisung(rolle="vereinsadmin", verein_id=eigen.verein_id))
            s.add_all([fremd, va, Benutzer(benutzername="neu", angelegt_ms=0)])
            s.commit()
            c.ids = {"eigen": eigen.id, "fremd": fremd.id}
        c.dienst = dienst
        c.headers.update(dienst.kopf("uid-vera", "vera"))
        c.csrf = re.search(r'name="csrf" value="([0-9a-f]+)"', c.get("/admin/benutzer").text).group(
            1
        )
        yield c
    dienst.beenden()


def test_vereinsadmin_darf_nur_event_admins_im_eigenen_verein(vereinsadmin):
    c = vereinsadmin
    bid = _konto(c, "neu")[0]
    html = c.get("/admin/benutzer").text
    assert "Vera Verein" in html and "Vereinsadmin" in html  # Kopf: Name und Rolle
    assert 'value="superadmin"' not in html and "Deaktivieren" not in html and "Fremd" not in html
    assert (
        "vergeben"
        in post(
            c, f"/admin/benutzer/{bid}/rolle", rolle="eventadmin", veranstaltung=str(c.ids["eigen"])
        ).text
    )
    for rolle, vid in (
        ("superadmin", ""),
        ("vereinsadmin", ""),
        ("eventadmin", str(c.ids["fremd"])),
    ):
        r = post(c, f"/admin/benutzer/{bid}/rolle", rolle=rolle, veranstaltung=vid)
        assert "darfst du nicht vergeben" in r.text, rolle
    assert "nur ein Superadmin" in post(c, f"/admin/benutzer/{bid}/aktiv", aktiv="0").text
    assert _konto(c, "neu")[1] == ["eventadmin"] and _konto(c, "neu")[2] is True
    # Vereinsadmin-Rolle eines anderen entziehen: nein
    with c.app.state.sessionmaker() as s:
        rid = (
            s.scalars(select(Rollenzuweisung).where(Rollenzuweisung.rolle == "vereinsadmin"))
            .one()
            .id
        )
    vera = _konto(c, "vera")[0]
    assert (
        "darfst du nicht entziehen" in post(c, f"/admin/benutzer/{vera}/rolle/{rid}/entziehen").text
    )


def test_event_admin_sieht_nur_seine_veranstaltung(vereinsadmin):
    c = vereinsadmin
    bid = _konto(c, "neu")[0]
    post(c, f"/admin/benutzer/{bid}/rolle", rolle="eventadmin", veranstaltung=str(c.ids["eigen"]))
    with c.app.state.sessionmaker() as s:
        s.add(Veranstaltung(name="Zweite im Verein", datum=date(2026, 6, 1)))
        s.commit()
    html = c.get("/admin").text  # Vereinsadmin: beide eigenen, nicht die des anderen Vereins
    assert "Eigen" in html and "Zweite im Verein" in html and "Fremd" not in html
    assert "Neue Veranstaltung einrichten" in html
    html = c.get("/admin", headers=c.dienst.kopf("uid-neu", "neu")).text  # Event-Admin
    assert "Eigen" in html and "Zweite im Verein" not in html and "Fremd" not in html
    assert "Neue Veranstaltung einrichten" not in html and 'id="nav-vorlagen"' not in html
    assert 'id="nav-benutzer"' not in html and "Event-Admin" in html
