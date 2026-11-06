"""Rechtematrix im Admin: jede Route × Rolle × eigene/fremde Veranstaltung (Modus proxy).

Die Erwartung steht hier unabhängig vom Code (Rechtematrix im Betriebshandbuch). Eine neue
Admin-Route ohne Eintrag in app/rechte_admin.py lässt `test_jede_admin_route_hat_ein_recht`
fehlschlagen; die Route selbst wird bis dahin abgewiesen."""

import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import (
    admin,
    admin_benutzer,
    admin_einrichtung,
    admin_geraetedatei,
    admin_import,
    admin_saetze,
    api,
    hilfe,
    oeffentlich,
    seiten,
)
from app import rechte_admin as ra
from app.admin_auth import admin_pruefen
from app.einstellungen import Einstellungen
from app.main import create_app
from app.models import Benutzer, Rollenzuweisung, Veranstaltung, Verein
from tests.proxy_hilfe import Anmeldedienst, einstellungen_proxy

ROUTER = (admin, admin_benutzer, admin_einrichtung, admin_geraetedatei, admin_import, admin_saetze, api, hilfe,
          oeffentlich, seiten)  # fmt: skip
PERSONEN = ("superadmin", "vereinsadmin", "eventadmin", "ohne")
# Wer darf was? (eigen = Veranstaltung im eigenen Verein bzw. zugewiesen)
ERWARTET = {
    ra.OFFEN: lambda p, eigen: True,
    ra.ANGEMELDET: lambda p, eigen: p != "ohne",
    ra.INSTALLATION: lambda p, eigen: p == "superadmin",
    ra.BENUTZER: lambda p, eigen: p in ("superadmin", "vereinsadmin"),
    ra.VORLAGEN: lambda p, eigen: p in ("superadmin", "vereinsadmin"),
    ra.ANLEGEN: lambda p, eigen: p in ("superadmin", "vereinsadmin"),
    ra.V_LESEN: lambda p, eigen: (
        p == "superadmin" or (eigen and p in ("vereinsadmin", "eventadmin"))
    ),
    ra.V_FUEHREN: lambda p, eigen: (
        p == "superadmin" or (eigen and p in ("vereinsadmin", "eventadmin"))
    ),
    ra.V_ADMINS: lambda p, eigen: p == "superadmin" or (eigen and p == "vereinsadmin"),
    ra.V_VERWALTEN: lambda p, eigen: p == "superadmin" or (eigen and p == "vereinsadmin"),
}
PLATZHALTER = {"sid": "1", "tid": "1", "bid": "999", "rid": "999", "art": "klassen", "format": "csv", "tab": "allgemein",
               "schritt": "2"}  # fmt: skip


VERWALTEN = {"aktivieren", "deaktivieren", "abschliessen", "anonymisieren", "loeschen"}


def soll_recht(methode: str, pfad: str) -> str:
    """Erwartetes Recht je Route, als Regeln aus der Rechtematrix (nicht aus dem Code)."""
    if pfad in ("/admin/anmelden", "/admin/abmelden"):
        return ra.OFFEN
    if pfad in ("/admin", "/admin/beispiel-teilnehmer.csv"):
        return ra.ANGEMELDET
    if pfad.startswith(("/admin/saetze/branding", "/admin/saetze/rechtliches")):
        return ra.INSTALLATION
    if pfad.startswith("/admin/saetze"):
        return ra.VORLAGEN
    if pfad.startswith("/admin/benutzer"):
        return ra.BENUTZER
    if pfad in ("/admin/einrichtung/neu", "/admin/veranstaltungen"):
        return ra.ANLEGEN
    if pfad.startswith(("/admin/v/{vid}", "/admin/einrichtung/{vid}")):
        teile = pfad.split("/")
        if methode == "POST" and teile[-1] in VERWALTEN and teile[-2] == "{vid}":
            return ra.V_VERWALTEN
        if pfad.startswith("/admin/v/{vid}/admins"):
            return ra.V_ADMINS
        return ra.V_LESEN if methode == "GET" else ra.V_FUEHREN
    raise AssertionError(f"keine Regel für {methode} {pfad}")


def admin_routen():
    for modul in ROUTER:
        for r in modul.router.routes:
            if r.path.startswith("/admin"):
                for methode in sorted(r.methods - {"HEAD"}):
                    yield methode, r.path, r


def _hat_pruefung(dependant) -> bool:
    return any(d.call is admin_pruefen or _hat_pruefung(d) for d in dependant.dependencies)


def test_jede_admin_route_hat_ein_recht():
    routen = {(m, p) for m, p, _ in admin_routen()}
    ohne = routen - set(ra.ROUTEN)
    assert not ohne, f"Admin-Route ohne Recht in app/rechte_admin.py: {sorted(ohne)}"
    assert not set(ra.ROUTEN) - routen, "Eintrag ohne Route"
    assert set(ra.ROUTEN.values()) <= set(ERWARTET)
    falsch = {r: (ist, soll_recht(*r)) for r, ist in ra.ROUTEN.items() if ist != soll_recht(*r)}
    assert not falsch, f"Recht weicht von der Matrix ab (ist, soll): {falsch}"


def test_jede_geschuetzte_route_prueft():
    """Außer der Anmeldung selbst läuft jede Admin-Route durch die zentrale Prüfung."""
    ungeprueft = [(m, p) for m, p, r in admin_routen()
                  if (m, p) != ("POST", "/admin/anmelden") and not _hat_pruefung(r.dependant)]  # fmt: skip
    assert not ungeprueft


@pytest.fixture(scope="module")
def umgebung(tmp_path_factory):
    ordner = tmp_path_factory.mktemp("matrix")
    dienst = Anmeldedienst.starten()
    e = Einstellungen(db_pfad=str(ordner / "m.sqlite3"), token_admin="tok-admin",
                      **einstellungen_proxy(dienst, superadmins="chefin"))  # fmt: skip
    with TestClient(create_app(e), raise_server_exceptions=False) as c:
        with c.app.state.sessionmaker() as s:
            v1 = Veranstaltung(name="Eigen", datum=date(2026, 5, 1))
            v2 = Veranstaltung(name="Nicht zugewiesen", datum=date(2026, 5, 2))
            s.add_all([v1, v2])
            s.flush()
            fremd = Verein(name="Anderer Verein")
            s.add(fremd)
            s.flush()
            v3 = Veranstaltung(name="Fremder Verein", datum=date(2026, 5, 3), verein_id=fremd.id)
            s.add(v3)
            s.flush()
            for name, rolle, wo in (("vereinsadmin", "vereinsadmin", {"verein_id": v1.verein_id}),
                                    ("eventadmin", "eventadmin", {"veranstaltung_id": v1.id}),
                                    ("ohne", None, {})):  # fmt: skip
                b = Benutzer(benutzername=name, angelegt_ms=0)
                if rolle:
                    b.rollen.append(Rollenzuweisung(rolle=rolle, **wo))
                s.add(b)
            s.commit()
            c.ziele = {  # (eigen, fremd) je Person
                "superadmin": (v1.id, v3.id),
                "vereinsadmin": (v1.id, v3.id),
                "eventadmin": (v1.id, v2.id),
                "ohne": (v1.id, v3.id),
            }
        c.kopf = {
            p: dienst.kopf(f"uid-{p}", "chefin" if p == "superadmin" else p) for p in PERSONEN
        }
        yield c
    dienst.beenden()


def _verweigert(r) -> bool:
    return r.status_code == 403 and r.headers.get("X-TapTrial-Recht") in (
        "verweigert",
        "keine-rolle",
    )


@pytest.mark.parametrize(("methode", "pfad"), sorted({(m, p) for m, p, _ in admin_routen()}))
def test_matrix(umgebung, methode, pfad):
    recht = soll_recht(methode, pfad)
    fehler = []
    for person in PERSONEN:
        for eigen, vid in zip((True, False), umgebung.ziele[person], strict=True):
            if "{vid}" not in pfad and not eigen:
                continue
            url = re.sub(
                r"\{(\w+)\}",
                lambda m, vid=vid: str(vid) if m.group(1) == "vid" else PLATZHALTER[m.group(1)],
                pfad,
            )
            r = umgebung.request(
                methode, url, headers=umgebung.kopf[person], follow_redirects=False
            )
            darf = ERWARTET[recht](person, eigen)
            if r.status_code == 401:  # Anmeldung gescheitert: wäre weder erlaubt noch verweigert
                fehler.append(f"{person}: 401")
            if darf == _verweigert(r):
                fehler.append(
                    f"{person} {'eigen' if eigen else 'fremd'}: {r.status_code}, erwartet {'erlaubt' if darf else 'verweigert'}"
                )
    assert not fehler, f"{methode} {pfad} ({recht}): " + "; ".join(fehler)
