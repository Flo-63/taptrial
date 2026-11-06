"""Admin: Benutzer und Rollen (Seite „Benutzer“) und Event-Admins einer Veranstaltung.
Fachlogik und Rechte für Rollen in app/benutzer.py; das Recht für die Route in app/rechte_admin.py."""

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app import admin_auth
from app import benutzer as bz
from app.admin import AdminDep, _seite, _veranstaltung, _zurueck
from app.auth import SessionDep
from app.models import AdminAktion, Benutzer, Rollenzuweisung, Veranstaltung, Verein
from app.rechte_admin import V_LESEN, darf

router = APIRouter(prefix="/admin", include_in_schema=False)
KONTO_AKTIONEN = ("Konto angelegt", "Rolle vergeben", "Rolle entzogen", "Konto aktiviert",
                  "Konto deaktiviert", "Anmeldung ohne Rolle")  # fmt: skip


def _konto(session, bid: int) -> Benutzer:
    b = session.get(Benutzer, bid)
    if b is None:
        raise HTTPException(404, "Konto nicht gefunden")
    return b


def _ausfuehren(request: Request, csrf: str, ziel: str, aktion) -> object:
    admin_auth.csrf_pruefen(request, csrf)
    try:
        meldung = aktion()
    except bz.BenutzerFehler as e:
        return _zurueck(ziel, fehler=str(e))
    request.state.aktion_text = meldung
    return _zurueck(ziel, ok=meldung)


@router.get("/benutzer", response_class=HTMLResponse)
def benutzer_seite(request: Request, session: SessionDep, _a: AdminDep):
    veranstaltungen = [v for v in session.scalars(select(Veranstaltung).order_by(Veranstaltung.datum.desc()))
                       if darf(_a, V_LESEN, v)]  # fmt: skip
    konten = bz.alle(session)
    return _seite(request, "admin_benutzer.html", bereich="benutzer", hilfe="benutzer", konten=konten,
                  veranstaltungen=veranstaltungen, vereine=list(session.scalars(select(Verein))),
                  rolle_text=lambda r: bz.rolle_text(r, session), person=_a,
                  darf_rolle=lambda r: bz.darf_rolle(_a, r.rolle, r.verein_id,
                                                     session.get(Veranstaltung, r.veranstaltung_id)
                                                     if r.veranstaltung_id else None),
                  superadmin_namen=request.app.state.einstellungen.superadmin_namen,
                  proxy=request.app.state.einstellungen.proxy,
                  aktionen=session.scalars(select(AdminAktion).where(AdminAktion.aktion.in_(KONTO_AKTIONEN))
                                           .order_by(AdminAktion.id.desc()).limit(100)).all())  # fmt: skip


@router.post("/benutzer")
def benutzer_anlegen(request: Request, session: SessionDep, _a: AdminDep,
                     csrf: Annotated[str, Form()] = "", benutzername: Annotated[str, Form()] = "",
                     name: Annotated[str, Form()] = ""):  # fmt: skip
    return _ausfuehren(request, csrf, "/admin/benutzer",
                       lambda: f"Konto {bz.anlegen(session, _a, benutzername, name).anzeige} angelegt.")  # fmt: skip


@router.post("/benutzer/{bid}/rolle")
def rolle_vergeben(request: Request, bid: int, session: SessionDep, _a: AdminDep,
                   csrf: Annotated[str, Form()] = "", rolle: Annotated[str, Form()] = "",
                   veranstaltung: Annotated[str, Form()] = "", verein: Annotated[str, Form()] = ""):  # fmt: skip
    b = _konto(session, bid)
    vid = int(veranstaltung) if veranstaltung.isdigit() else None
    verein_id = int(verein) if verein.isdigit() else None
    return _ausfuehren(request, csrf, "/admin/benutzer",
                       lambda: bz.vergeben(session, _a, b, rolle, vid, verein_id))  # fmt: skip


@router.post("/benutzer/{bid}/rolle/{rid}/entziehen")
def rolle_entziehen(request: Request, bid: int, rid: int, session: SessionDep, _a: AdminDep,
                    csrf: Annotated[str, Form()] = ""):  # fmt: skip
    b = _konto(session, bid)
    r = session.get(Rollenzuweisung, rid)
    if r is None:
        raise HTTPException(404, "Rolle nicht gefunden")
    return _ausfuehren(request, csrf, "/admin/benutzer", lambda: bz.entziehen(session, _a, b, r))


@router.post("/benutzer/{bid}/aktiv")
def aktiv_setzen(request: Request, bid: int, session: SessionDep, _a: AdminDep,
                 csrf: Annotated[str, Form()] = "", aktiv: Annotated[str, Form()] = ""):  # fmt: skip
    b = _konto(session, bid)
    return _ausfuehren(request, csrf, "/admin/benutzer",
                       lambda: bz.aktiv_setzen(session, _a, b, aktiv == "1"))  # fmt: skip


# --- Event-Admins einer Veranstaltung ------------------------------------------------------------


@router.get("/v/{vid}/admins", response_class=HTMLResponse)
def admins_seite(request: Request, vid: int, session: SessionDep, _a: AdminDep):
    v = _veranstaltung(session, vid)
    zuweisungen = list(session.scalars(select(Rollenzuweisung).where(
        Rollenzuweisung.veranstaltung_id == v.id, Rollenzuweisung.rolle == "eventadmin")))  # fmt: skip
    vergeben = {r.benutzer_id for r in zuweisungen}
    konten = [b for b in bz.alle(session) if b.id not in vergeben and b.aktiv]
    return _seite(request, "admin_v_admins.html", v=v, reiter="admins", zuweisungen=zuweisungen,
                  konten=konten, hilfe="benutzer")  # fmt: skip


@router.post("/v/{vid}/admins")
def admin_zuweisen(request: Request, vid: int, session: SessionDep, _a: AdminDep,
                   csrf: Annotated[str, Form()] = "", benutzer: Annotated[str, Form()] = ""):  # fmt: skip
    v = _veranstaltung(session, vid)
    ziel = f"/admin/v/{v.id}/admins"
    if not benutzer.isdigit():
        admin_auth.csrf_pruefen(request, csrf)
        return _zurueck(ziel, fehler="Bitte ein Konto wählen.")
    b = _konto(session, int(benutzer))
    return _ausfuehren(request, csrf, ziel, lambda: bz.vergeben(session, _a, b, "eventadmin", v.id))


@router.post("/v/{vid}/admins/{rid}/entziehen")
def admin_entziehen(request: Request, vid: int, rid: int, session: SessionDep, _a: AdminDep,
                    csrf: Annotated[str, Form()] = ""):  # fmt: skip
    v = _veranstaltung(session, vid)
    r = session.get(Rollenzuweisung, rid)
    if r is None or r.veranstaltung_id != v.id:
        raise HTTPException(404, "Zuweisung nicht gefunden")
    return _ausfuehren(request, csrf, f"/admin/v/{v.id}/admins",
                       lambda: bz.entziehen(session, _a, r.benutzer, r))  # fmt: skip
