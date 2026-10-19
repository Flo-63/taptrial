"""Admin: Klassen- und Wertungssätze anlegen, als Tabelle bearbeiten, kopieren, löschen,
Import/Export als TOML und CSV (Phase 12). Fachlogik in app/saetze.py."""

from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select

from app import admin_auth, installation
from app import saetze as sz
from app.admin import AdminDep, _seite, _zurueck
from app.auth import SessionDep
from app.models import Klassensatz, Veranstaltung, Wertungssatz
from app.wertung import ARTEN

router = APIRouter(prefix="/admin/saetze", include_in_schema=False)
LEERZEILEN = 5
KLASSEN_FELDER = ("name", "geschlecht", "alter_von", "alter_bis")
STUFEN_FELDER = ("klasse", "strecke_km", "gold", "silber", "bronze")


def _satz(session, modell, sid: int):
    satz = session.get(modell, sid)
    if satz is None:
        raise HTTPException(404, "Satz nicht gefunden")
    return satz


async def _zeilen(request: Request, felder: tuple[str, ...]) -> tuple[dict, list[dict]]:
    """Tabellenformular → (übrige Felder, Zeilen). Angehakte „entfernen“-Zeilen fallen weg."""
    form = await request.form()
    anzahl = int(form.get("anzahl", "0") or 0)
    zeilen = []
    for i in range(anzahl):
        if form.get(f"entfernen_{i}"):
            continue
        zeilen.append({f: str(form.get(f"{f}_{i}", "")) for f in felder})
    return {k: str(v) for k, v in form.items()}, zeilen


def _download(inhalt: bytes | str, dateiname: str, typ: str) -> Response:
    if isinstance(inhalt, str):
        inhalt = inhalt.encode("utf-8")
    return Response(inhalt, media_type=typ,
                    headers={"Content-Disposition": f'attachment; filename="{dateiname}"'})  # fmt: skip


def _dateiname(name: str, endung: str) -> str:
    erlaubt = "".join(c if c.isalnum() or c in "-_" else "-" for c in name.lower())
    return f"{erlaubt.strip('-') or 'satz'}.{endung}"


def _eingefroren(session) -> int:
    return len(
        session.scalars(select(Veranstaltung).where(Veranstaltung.regeln_json.is_not(None))).all()
    )


# --- Übersicht ----------------------------------------------------------------------------


def vorlagen_leiste(session) -> dict:
    """Seitenleiste der Vorlagen im gleichen Aufbau wie in einer Veranstaltung (Test 08.10., 13)."""
    return {"titel": "Vorlagen", "gruppen": [
        ("Wertung", [("klassen", "Klassensätze", "/admin/saetze/klassen", len(sz.klassensaetze(session))),
                     ("wertung", "Wertungssätze", "/admin/saetze/wertung", len(sz.wertungssaetze(session)))]),
        ("Gestaltung", [("branding", "Branding", "/admin/saetze/branding", None),
                        ("urkunden", "Urkunden-Vorlagen", "/admin/saetze/urkunden", None)]),
        ("Installation", [("rechtliches", "Impressum & Datenschutz", "/admin/saetze/rechtliches", None)]),
    ]}  # fmt: skip


def _vorlagen_seite(request, session, vorlage: str, reiter: str | None, **kontext):
    return _seite(request, vorlage, bereich="vorlagen", leiste=vorlagen_leiste(session),
                  reiter=reiter, **kontext)  # fmt: skip


@router.get("")
def uebersicht(_a: AdminDep):
    return RedirectResponse("/admin/saetze/klassen", status_code=307)


def _liste(request, session, art: str):
    return _vorlagen_seite(
        request, session, "admin_saetze.html", art, art=art,
        klassensaetze=sz.klassensaetze(session), wertungssaetze=sz.wertungssaetze(session),
        benutzt=lambda satz: sz.benutzt_von(session, satz), arten=ARTEN,
        eingefroren=_eingefroren(session),
    )  # fmt: skip


@router.get("/klassen", response_class=HTMLResponse)
def klassensaetze(request: Request, session: SessionDep, _a: AdminDep):
    return _liste(request, session, "klassen")


@router.get("/wertung", response_class=HTMLResponse)
def wertungssaetze(request: Request, session: SessionDep, _a: AdminDep):
    return _liste(request, session, "wertung")


@router.get("/branding", response_class=HTMLResponse)
def branding(request: Request, session: SessionDep, _a: AdminDep):
    """Release 1: Vereinsname der Installation (global); Logo und Farben folgen (Roadmap)."""
    return _vorlagen_seite(request, session, "admin_branding.html", "branding",
                           werte=installation.lesen(session))  # fmt: skip


@router.post("/branding")
async def branding_speichern(request: Request, session: SessionDep, _a: AdminDep):
    form = await request.form()
    admin_auth.csrf_pruefen(request, str(form.get("csrf") or ""))
    name = " ".join(str(form.get("vereinsname") or "").split())[
        : installation.MAX_LAENGE["vereinsname"]
    ]
    installation.speichern(session, {"vereinsname": name})
    return _zurueck("/admin/saetze/branding", ok="Branding gespeichert.")


def _rechtliches_seite(request, session, werte=None, fehler=None, status_code=200):
    return _vorlagen_seite(request, session, "admin_rechtliches.html", "rechtliches",
                           werte=werte or installation.lesen(session), RECHTLICHES=installation.RECHTLICHES,
                           **({"fehler": fehler} if fehler else {}), status_code=status_code)  # fmt: skip


@router.get("/rechtliches", response_class=HTMLResponse)
def rechtliches(request: Request, session: SessionDep, _a: AdminDep):
    """Release 3: Impressum und Datenschutz der Installation (Link oder Text)."""
    return _rechtliches_seite(request, session)


@router.post("/rechtliches")
async def rechtliches_speichern(request: Request, session: SessionDep, _a: AdminDep):
    form = await request.form()
    admin_auth.csrf_pruefen(request, str(form.get("csrf") or ""))
    try:
        werte = installation.rechtliches_pruefen(form)
    except installation.EingabeFehler as e:
        eingabe = {**installation.lesen(session), **{k: str(form.get(k) or "") for k in installation.SCHLUESSEL
                                                        if k != "vereinsname"}}  # fmt: skip
        return _rechtliches_seite(request, session, eingabe, str(e), 422)
    installation.speichern(session, werte)
    return _zurueck("/admin/saetze/rechtliches", ok="Impressum und Datenschutz gespeichert.")


@router.get("/urkunden", response_class=HTMLResponse)
def urkunden_vorlagen(request: Request, session: SessionDep, _a: AdminDep):
    return _vorlagen_seite(request, session, "admin_vorlage_platzhalter.html", "urkunden",
                           titel="Urkunden-Vorlagen", text="Layout, Logo und Unterschriften der Urkunden.")  # fmt: skip


@router.get("/import", response_class=HTMLResponse)
def importseite(request: Request, session: SessionDep, _a: AdminDep):
    return _vorlagen_seite(request, session, "admin_saetze_import.html", None,
                           klassensaetze=sz.klassensaetze(session))  # fmt: skip


# --- Klassensätze -------------------------------------------------------------------------


def _klassen_seite(request, satz, name, zeilen, status_code=200, fehler=None, session=None):
    zeilen = list(zeilen) + [dict.fromkeys(KLASSEN_FELDER, "")] * LEERZEILEN
    return _seite(request, "admin_klassensatz.html", status_code=status_code, satz=satz, bereich="vorlagen",
                  leiste=vorlagen_leiste(session), reiter="klassen",
                  name=name, zeilen=zeilen, fehler=fehler or request.query_params.get("fehler"),
                  benutzt=sz.benutzt_von(session, satz) if satz else [])  # fmt: skip


@router.get("/klassen/neu", response_class=HTMLResponse)
def klassen_neu(request: Request, session: SessionDep, _a: AdminDep):
    return _klassen_seite(request, None, "", [], session=session)


@router.get("/klassen/{sid}", response_class=HTMLResponse)
def klassen_bearbeiten(request: Request, sid: int, session: SessionDep, _a: AdminDep):
    satz = _satz(session, Klassensatz, sid)
    return _klassen_seite(request, satz, satz.name, sz.klassen_zeilen(satz), session=session)


@router.post("/klassen/{sid}")
async def klassen_speichern(request: Request, sid: str, session: SessionDep, _a: AdminDep):
    felder, zeilen = await _zeilen(request, KLASSEN_FELDER)
    admin_auth.csrf_pruefen(request, felder.get("csrf"))
    satz = None if sid == "neu" else _satz(session, Klassensatz, int(sid))
    try:
        satz, warnungen = sz.klassensatz_speichern(session, satz, felder.get("name", ""), zeilen)
    except sz.SatzFehler as e:
        session.rollback()
        return _klassen_seite(request, satz, felder.get("name", ""), zeilen, 422, str(e), session)
    return _zurueck(f"/admin/saetze/klassen/{satz.id}", ok=f"Klassensatz „{satz.name}“ gespeichert.",
                    warnung="; ".join(warnungen) or None)  # fmt: skip


@router.get("/klassen/{sid}/export.{format}")
def klassen_export(sid: int, format: str, session: SessionDep, _a: AdminDep):
    satz = _satz(session, Klassensatz, sid)
    if format == "toml":
        return _download(sz.klassen_toml(satz), _dateiname(satz.name, "toml"), "application/toml")
    if format == "csv":
        return _download(
            sz.klassen_csv(satz), _dateiname(satz.name, "csv"), "text/csv; charset=utf-8"
        )
    raise HTTPException(404)


@router.post("/klassen-import")
async def klassen_importieren(
    request: Request, session: SessionDep, _a: AdminDep, csrf: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "", datei: Annotated[UploadFile | None, File()] = None,
):  # fmt: skip
    admin_auth.csrf_pruefen(request, csrf)
    if datei is None or not datei.filename:
        return _zurueck("/admin/saetze/import", fehler="Bitte eine TOML- oder CSV-Datei wählen.")
    try:
        name_datei, zeilen = sz.klassen_import(await datei.read(), datei.filename)
        satz, warnungen = sz.klassensatz_speichern(
            session, None, name.strip() or name_datei or datei.filename.rsplit(".", 1)[0], zeilen
        )
    except sz.SatzFehler as e:
        session.rollback()
        return _zurueck("/admin/saetze/import", fehler=f"Import abgebrochen: {e}")
    return _zurueck(f"/admin/saetze/klassen/{satz.id}", ok=f"Klassensatz „{satz.name}“ importiert.",
                    warnung="; ".join(warnungen) or None)  # fmt: skip


# --- Wertungssätze ------------------------------------------------------------------------


def _wertung_seite(request, session, satz, werte: dict, zeilen, status_code=200, fehler=None):
    ks_id = int(werte.get("klassensatz_id") or 0) or (satz.klassensatz_id if satz else None)
    ks = session.get(Klassensatz, ks_id) if ks_id else sz.standard_klassensatz(session)
    klassen = [k.name for k in ks.klassen]
    zeilen = [z for z in zeilen if any(str(w).strip() for w in z.values())]
    benutzt = {str(z["klasse"]).strip().lower() for z in zeilen}
    if len(benutzt - {""}) < len(klassen) and not any(not str(z["klasse"]).strip() for z in zeilen):
        zeilen.append(dict.fromkeys(STUFEN_FELDER, ""))  # eine freie Zeile, solange Klassen fehlen
    for z in zeilen:  # Auswahl je Zeile: unbenutzte Klassen + die eigene (auch wenn unbekannt)
        eigene = str(z["klasse"]).strip()
        z["optionen"] = [
            k for k in klassen if k.lower() not in benutzt or k.lower() == eigene.lower()
        ]
        if eigene and eigene.lower() not in {k.lower() for k in klassen}:
            z["optionen"].insert(0, eigene)
    klassen_je_satz = {str(x.id): [k.name for k in x.klassen] for x in sz.klassensaetze(session)}
    return _seite(request, "admin_wertungssatz.html", status_code=status_code, satz=satz, bereich="vorlagen",
                  leiste=vorlagen_leiste(session), reiter="wertung",
                  werte=werte, zeilen=zeilen, arten=ARTEN, klassensaetze=sz.klassensaetze(session),
                  klassensatz=ks, klassen_je_satz=klassen_je_satz,
                  alle_benutzt=not {k.lower() for k in klassen} - benutzt,
                  fehler=fehler or request.query_params.get("fehler"),
                  benutzt=sz.benutzt_von(session, satz) if satz else [])  # fmt: skip


def _wertung_werte(satz: Wertungssatz | None) -> dict:
    if satz is None:
        return {"name": "", "beschreibung": "", "art": "gesamtzeit", "strecke_km_min": "",
                "strecke_km_max": "", "klassensatz_id": ""}  # fmt: skip
    return {"name": satz.name, "beschreibung": satz.beschreibung or "", "art": satz.art,
            "strecke_km_min": sz._km_text(satz.strecke_km_min),
            "strecke_km_max": sz._km_text(satz.strecke_km_max),
            "klassensatz_id": str(satz.klassensatz_id)}  # fmt: skip


@router.get("/wertung/neu", response_class=HTMLResponse)
def wertung_neu(request: Request, session: SessionDep, _a: AdminDep):
    werte = _wertung_werte(None)
    werte["klassensatz_id"] = str(sz.standard_klassensatz(session).id)
    return _wertung_seite(request, session, None, werte, [])


@router.get("/wertung/{sid}", response_class=HTMLResponse)
def wertung_bearbeiten(request: Request, sid: int, session: SessionDep, _a: AdminDep):
    satz = _satz(session, Wertungssatz, sid)
    return _wertung_seite(request, session, satz, _wertung_werte(satz), sz.wertung_zeilen(satz))


@router.post("/wertung/{sid}")
async def wertung_speichern(request: Request, sid: str, session: SessionDep, _a: AdminDep):
    werte, zeilen = await _zeilen(request, STUFEN_FELDER)
    admin_auth.csrf_pruefen(request, werte.get("csrf"))
    satz = None if sid == "neu" else _satz(session, Wertungssatz, int(sid))
    try:
        satz, warnungen = sz.wertungssatz_speichern(
            session, satz, werte.get("name", ""), werte.get("art", ""), werte.get("strecke_km_min"),
            werte.get("strecke_km_max"), werte.get("klassensatz_id"), zeilen,
            beschreibung=werte.get("beschreibung", ""),
        )  # fmt: skip
    except sz.SatzFehler as e:
        session.rollback()
        return _wertung_seite(request, session, satz, werte, zeilen, 422, str(e))
    return _zurueck(f"/admin/saetze/wertung/{satz.id}", ok=f"Wertungssatz „{satz.name}“ gespeichert.",
                    warnung="; ".join(warnungen) or None)  # fmt: skip


@router.get("/wertung/{sid}/export.{format}")
def wertung_export(sid: int, format: str, session: SessionDep, _a: AdminDep):
    satz = _satz(session, Wertungssatz, sid)
    if format == "toml":
        return _download(sz.wertung_toml(satz), _dateiname(satz.name, "toml"), "application/toml")
    if format == "csv":
        return _download(
            sz.wertung_csv(satz), _dateiname(satz.name, "csv"), "text/csv; charset=utf-8"
        )
    raise HTTPException(404)


@router.post("/wertung-import")
async def wertung_importieren(
    request: Request, session: SessionDep, _a: AdminDep, csrf: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "", art: Annotated[str, Form()] = "gesamtzeit",
    strecke_km_min: Annotated[str, Form()] = "", strecke_km_max: Annotated[str, Form()] = "",
    klassensatz_id: Annotated[str, Form()] = "",
    datei: Annotated[UploadFile | None, File()] = None,
):  # fmt: skip
    admin_auth.csrf_pruefen(request, csrf)
    if datei is None or not datei.filename:
        return _zurueck("/admin/saetze/import", fehler="Bitte eine TOML- oder CSV-Datei wählen.")
    try:
        d = sz.wertung_import(await datei.read(), datei.filename)
        satz, warnungen = sz.wertungssatz_speichern(
            session, None, name.strip() or d.get("beschreibung") or datei.filename.rsplit(".", 1)[0],
            d.get("art") or art, d.get("strecke_km_min", strecke_km_min),
            d.get("strecke_km_max", strecke_km_max), klassensatz_id, d["zeilen"],
            beschreibung=d.get("beschreibung") or "",
        )  # fmt: skip
    except sz.SatzFehler as e:
        session.rollback()
        return _zurueck("/admin/saetze/import", fehler=f"Import abgebrochen: {e}")
    return _zurueck(f"/admin/saetze/wertung/{satz.id}", ok=f"Wertungssatz „{satz.name}“ importiert.",
                    warnung="; ".join(warnungen) or None)  # fmt: skip


# --- Kopieren und Löschen (beide Arten) ----------------------------------------------------

ARTEN_SATZ = {"klassen": Klassensatz, "wertung": Wertungssatz}


@router.post("/{art}/{sid}/kopieren")
def kopieren(request: Request, art: str, sid: int, session: SessionDep, _a: AdminDep,
             csrf: Annotated[str, Form()] = ""):  # fmt: skip
    admin_auth.csrf_pruefen(request, csrf)
    if art not in ARTEN_SATZ:
        raise HTTPException(404)
    neu = sz.kopieren(session, _satz(session, ARTEN_SATZ[art], sid))
    return _zurueck(f"/admin/saetze/{art}/{neu.id}", ok=f"Kopie „{neu.name}“ angelegt.")


@router.post("/{art}/{sid}/loeschen")
def loeschen(request: Request, art: str, sid: int, session: SessionDep, _a: AdminDep,
             csrf: Annotated[str, Form()] = ""):  # fmt: skip
    admin_auth.csrf_pruefen(request, csrf)
    if art not in ARTEN_SATZ:
        raise HTTPException(404)
    satz = _satz(session, ARTEN_SATZ[art], sid)
    name = satz.name
    try:
        sz.loeschen(session, satz)
    except sz.SatzFehler as e:
        return _zurueck(f"/admin/saetze/{art}", fehler=str(e))
    return _zurueck(f"/admin/saetze/{art}", ok=f"„{name}“ gelöscht.")
