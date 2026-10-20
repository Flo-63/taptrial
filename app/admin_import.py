"""Admin: CSV-Import mit Vorschau und Bearbeitung.

Hochladen legt einen Entwurf an (serverseitig gespeichert). Die Vorschau zeigt alle Zeilen mit
Fehlern/Warnungen je Zelle; Zellen sind bearbeitbar, jede Änderung wird sofort neu geprüft und
gespeichert. Importiert wird erst, wenn alles fehlerfrei ist (ergänzen oder ersetzen).
"""

import json
import time
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy import select

from app import admin_auth
from app import korrekturen as k
from app.admin import AdminDep, _seite, _veranstaltung, _zurueck
from app.auth import SessionDep
from app.models import ImportEntwurf, Veranstaltung
from app.teilnehmer_import import (
    SPALTEN,
    TRENNZEICHEN,
    ImportFehler,
    csv_lesen,
    pruefe_zeilen,
    vergebene_startnummern,
    zeilen_als_csv,
    zeilen_importieren,
)

router = APIRouter(prefix="/admin/v/{vid}/import", include_in_schema=False)
MODI = ("ergaenzen", "ersetzen")


def _entwurf(session, v: Veranstaltung) -> ImportEntwurf | None:
    return session.scalars(
        select(ImportEntwurf).where(ImportEntwurf.veranstaltung_id == v.id)
    ).first()


def _speichern(session, v: Veranstaltung, zeilen: list[dict], dateiname: str | None = None) -> None:
    e = _entwurf(session, v)
    if e is None:
        e = ImportEntwurf(veranstaltung_id=v.id, zeilen_json="[]", geaendert_ms=0)
        session.add(e)
    e.zeilen_json = json.dumps(zeilen, ensure_ascii=False)
    if dateiname is not None:
        e.dateiname = dateiname
    e.geaendert_ms = time.time_ns() // 1_000_000
    session.commit()


def _bereinigt(zeilen) -> list[dict]:
    return [{sp.name: str((z or {}).get(sp.name) or "") for sp in SPALTEN} for z in zeilen or []]


def pruefung(session, v: Veranstaltung, zeilen: list[dict], modus: str) -> dict:
    vergeben = vergebene_startnummern(session, v) if modus == "ergaenzen" else set()
    p = pruefe_zeilen(zeilen, v.stichtag_alter, k.klassen_von(session, v), vergeben)
    return {
        "fehler": [[m.index, m.feld, m.text] for m in p.fehler],
        "warnungen": [[m.index, m.feld, m.text] for m in p.warnungen],
        "anzahl_fehler": len(p.fehler),
        "anzahl_warnungen": len(p.warnungen),
        "zeilen": len(zeilen),
    }


def _ziel(v, assistent: bool) -> str:
    return f"/admin/einrichtung/{v.id}/3" if assistent else f"/admin/v/{v.id}/teilnehmer"


@router.get("", response_class=HTMLResponse)
def importseite(request: Request, vid: int, session: SessionDep, _a: AdminDep):
    """CSV importieren: Formathilfe aus der Spaltendefinition, Hochladen, offener Entwurf."""
    v = _veranstaltung(session, vid)
    return _seite(request, "admin_import_start.html", v=v, reiter="teilnehmer", hilfe="import",
                  csv_spalten=SPALTEN, csv_trennzeichen=TRENNZEICHEN,
                  entwurf_vorhanden=_entwurf(session, v) is not None)  # fmt: skip


@router.post("")
async def hochladen(
    request: Request, vid: int, session: SessionDep, _a: AdminDep,
    csrf: Annotated[str, Form()] = "", assistent: Annotated[str, Form()] = "",
    datei: Annotated[UploadFile | None, File()] = None,
):  # fmt: skip
    admin_auth.csrf_pruefen(request, csrf)
    v = _veranstaltung(session, vid)
    zurueck = _ziel(v, True) if assistent else f"/admin/v/{v.id}/import"
    if not v.laufend:
        return _zurueck(zurueck, fehler=f"Veranstaltung ist {v.zustand} und schreibgeschützt.")
    if datei is None or not datei.filename:
        return _zurueck(zurueck, fehler="Bitte eine CSV-Datei wählen.")
    zeilen, _, kopffehler = csv_lesen(await datei.read())
    if kopffehler:
        return _zurueck(zurueck, fehler="Datei nicht lesbar: " + "; ".join(kopffehler))
    _speichern(session, v, zeilen, datei.filename)
    return _zurueck(f"/admin/v/{v.id}/import/vorschau" + ("?assistent=1" if assistent else ""))


@router.get("/vorschau", response_class=HTMLResponse)
def vorschau(
    request: Request, vid: int, session: SessionDep, _a: AdminDep, modus: str = "ergaenzen"
):
    v = _veranstaltung(session, vid)
    e = _entwurf(session, v)
    if e is None:
        return _zurueck(f"/admin/v/{v.id}/teilnehmer", fehler="Kein Import-Entwurf vorhanden.")
    zeilen = json.loads(e.zeilen_json)
    modus = modus if modus in MODI else "ergaenzen"
    p = pruefung(session, v, zeilen, modus)
    meldungen = {}
    for art in ("fehler", "warnungen"):
        for i, feld, text in p[art]:
            meldungen.setdefault((i, feld), []).append((art, text))
    return _seite(
        request, "admin_import.html", v=v, reiter="teilnehmer", hilfe="import", entwurf=e, zeilen=zeilen,
        spalten=SPALTEN, pruefung=p, meldungen=meldungen, modus=modus,
        vorhandene=len(vergebene_startnummern(session, v)),
        geaendert=k.seit_import_geaendert(session, v),
        assistent=bool(request.query_params.get("assistent")),
    )  # fmt: skip


@router.post("/entwurf")
async def entwurf_pruefen(request: Request, vid: int, session: SessionDep, _a: AdminDep):
    """Live-Prüfung aus der Vorschau: Zeilen speichern und Meldungen je Zelle zurückgeben."""
    admin_auth.csrf_pruefen(request, request.headers.get("x-csrf-token"))
    v = _veranstaltung(session, vid)
    daten = await request.json()
    zeilen = _bereinigt(daten.get("zeilen"))
    _speichern(session, v, zeilen)
    return JSONResponse(pruefung(session, v, zeilen, daten.get("modus", "ergaenzen")))


@router.get("/entwurf.csv")
def entwurf_csv(vid: int, session: SessionDep, _a: AdminDep):
    v = _veranstaltung(session, vid)
    e = _entwurf(session, v)
    if e is None:
        raise HTTPException(404, "Kein Entwurf")
    name = (e.dateiname or "teilnehmer.csv").rsplit(".", 1)[0] + "-korrigiert.csv"
    return Response(zeilen_als_csv(json.loads(e.zeilen_json)), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})  # fmt: skip


@router.post("/uebernehmen")
def uebernehmen(
    request: Request, vid: int, session: SessionDep, _a: AdminDep,
    csrf: Annotated[str, Form()] = "", modus: Annotated[str, Form()] = "ergaenzen",
    bestaetigung: Annotated[str, Form()] = "", assistent: Annotated[str, Form()] = "",
):  # fmt: skip
    admin_auth.csrf_pruefen(request, csrf)
    v = _veranstaltung(session, vid)
    e = _entwurf(session, v)
    vorschau_url = f"/admin/v/{v.id}/import/vorschau?modus={modus}" + (
        "&assistent=1" if assistent else ""
    )
    if e is None:
        return _zurueck(_ziel(v, bool(assistent)), fehler="Kein Import-Entwurf vorhanden.")
    if not v.laufend:
        return _zurueck(vorschau_url, fehler=f"Veranstaltung ist {v.zustand} und schreibgeschützt.")
    ersetzen = modus == "ersetzen"
    if ersetzen and vergebene_startnummern(session, v) and not bestaetigung:
        geaendert = k.seit_import_geaendert(session, v)
        mehr = (f" {geaendert} Teilnehmer wurden seit dem letzten Import im Admin angelegt oder geändert."
                if geaendert else "")  # fmt: skip
        return _zurueck(
            vorschau_url,
            fehler=f"Ersetzen löscht alle vorhandenen Teilnehmer – bitte bestätigen.{mehr}",
        )
    try:
        n = zeilen_importieren(
            session, v, json.loads(e.zeilen_json), k.klassen_von(session, v), ersetzen
        )
    except ImportFehler as fehler:
        return _zurueck(
            vorschau_url, fehler="Import nicht möglich: " + "; ".join(fehler.fehler[:5])
        )
    session.delete(e)
    session.commit()
    return _zurueck(_ziel(v, bool(assistent)), ok=f"{n} Teilnehmer importiert.")


@router.post("/verwerfen")
def verwerfen(request: Request, vid: int, session: SessionDep, _a: AdminDep,
              csrf: Annotated[str, Form()] = "", assistent: Annotated[str, Form()] = ""):  # fmt: skip
    admin_auth.csrf_pruefen(request, csrf)
    v = _veranstaltung(session, vid)
    e = _entwurf(session, v)
    if e is not None:
        session.delete(e)
        session.commit()
    return _zurueck(_ziel(v, bool(assistent)), ok="Import-Entwurf verworfen.")
