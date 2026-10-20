"""Admin: Exportdatei eines Geräts mit nicht übertragenen Einträgen einlesen.

Hochladen zeigt eine Vorschau (Anzahl, schon vorhanden, neu, ungültig, Warnung bei anderer
Veranstaltung). „Übernehmen“ legt nur neue Einträge an – idempotent wie beim Sync (gleiche UUID
wird ignoriert), mit der Gerätekennung aus der Datei und der Herkunft „datei“ im Protokoll.
Der Inhalt wird zustandslos über ein verstecktes Formularfeld weitergereicht.
"""

import json
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app import admin_auth, saetze, strecken
from app.admin import AdminDep, _seite, _veranstaltung, _zurueck
from app.api import ERLAUBTE_ARTEN, ERLAUBTE_STATUS, EreignisEin, jetzt_ms
from app.auth import SessionDep
from app.korrekturen import uhrzeit_text
from app.models import Ereignis, Veranstaltung, freigabe_text

router = APIRouter(prefix="/admin/v/{vid}/geraetedatei", include_in_schema=False)

MAX_GROESSE = 2_000_000
# Geräte erzeugen nur Start-/Ziel-Ereignisse (die Rennleitung läuft im Zielgerät)
GERAETE_ARTEN = ERLAUBTE_ARTEN["start"] | ERLAUBTE_ARTEN["ziel"]
GERAETE_STATUS = ERLAUBTE_STATUS["start"] | ERLAUBTE_STATUS["ziel"]


class DateiFehler(Exception):
    pass


@dataclass
class Pruefung:
    geraet: str
    anzahl: int = 0
    vorhanden: int = 0
    neu: list[EreignisEin] = field(default_factory=list)
    ungueltig: int = 0
    andere_veranstaltung: bool = False
    ort_id: int | None = None  # Start/Ziel des Geräts (weiterer Messpunkt), sonst None
    ort_art: str = ""


def lesen(inhalt: bytes | str) -> dict:
    try:
        daten = json.loads(inhalt)
    except (ValueError, UnicodeDecodeError) as e:
        raise DateiFehler("kein gültiges JSON") from e
    if not isinstance(daten, dict) or not isinstance(daten.get("eintraege"), list):
        raise DateiFehler("„eintraege“ fehlt – ist das eine Exportdatei eines Geräts?")
    return daten


def pruefen(session, v: Veranstaltung, daten: dict) -> Pruefung:
    geraet = str(daten.get("geraet") or "")[:50] or "datei"
    p = Pruefung(geraet=geraet, anzahl=len(daten["eintraege"]))
    datei_v = daten.get("veranstaltung")
    if isinstance(datei_v, dict) and datei_v.get("id") not in (None, v.id):
        p.andere_veranstaltung = True
    ort = daten.get("ort")  # Messpunkt des Geräts (ab dieser Version in der Datei)
    if isinstance(ort, dict):
        m = next((m for m in strecken.orte_von(session, v) if m.id == ort.get("id")), None)
        if m is not None and not m.standard and m.art in ("start", "ziel"):
            p.ort_id, p.ort_art = m.id, m.art
    gueltig: list[EreignisEin] = []
    for roh in daten["eintraege"]:
        if not isinstance(roh, dict):
            p.ungueltig += 1
            continue
        if roh.get("veranstaltung_id") not in (None, v.id):
            p.andere_veranstaltung = True
        felder = {f: roh[f] for f in EreignisEin.model_fields if roh.get(f) is not None}
        felder.setdefault("geraet", geraet)
        felder.pop("veranstaltung_id", None)
        try:
            e = EreignisEin(**felder)
        except (ValidationError, TypeError):
            p.ungueltig += 1
            continue
        if e.art not in GERAETE_ARTEN or (e.art == "STATUS" and e.wert not in GERAETE_STATUS):
            p.ungueltig += 1
            continue
        if all(e.uuid != x.uuid for x in gueltig):
            gueltig.append(e)
    bekannt = set(
        session.scalars(select(Ereignis.uuid).where(Ereignis.uuid.in_([e.uuid for e in gueltig])))
    )
    p.vorhanden = sum(e.uuid in bekannt for e in gueltig)
    p.neu = [e for e in gueltig if e.uuid not in bekannt]
    return p


def _ziel(v) -> str:
    return f"/admin/v/{v.id}/geraetedatei"


@router.get("", response_class=HTMLResponse)
def seite(request: Request, vid: int, session: SessionDep, _a: AdminDep):
    """Upload-Seite (erreichbar über „Protokoll“ und „Aufgaben“ → Sonderfälle)."""
    v = _veranstaltung(session, vid)
    return _seite(
        request, "admin_geraetedatei_start.html", v=v, reiter="protokoll", hilfe="protokoll"
    )


@router.post("")
async def hochladen(
    request: Request, vid: int, session: SessionDep, _a: AdminDep,
    csrf: Annotated[str, Form()] = "", datei: Annotated[UploadFile | None, File()] = None,
):  # fmt: skip
    admin_auth.csrf_pruefen(request, csrf)
    v = _veranstaltung(session, vid)
    if not v.laufend:
        return _zurueck(_ziel(v), fehler=f"Veranstaltung ist {v.zustand} und schreibgeschützt.")
    if datei is None or not datei.filename:
        return _zurueck(_ziel(v), fehler="Bitte die Exportdatei eines Geräts wählen.")
    inhalt = await datei.read(MAX_GROESSE + 1)
    try:
        if len(inhalt) > MAX_GROESSE:
            raise DateiFehler("Datei zu groß")
        p = pruefen(session, v, lesen(inhalt))
    except DateiFehler as e:
        return _zurueck(_ziel(v), fehler=f"Datei nicht lesbar: {e}")
    return _seite(request, "admin_geraetedatei.html", v=v, reiter="protokoll", p=p,
                  dateiname=datei.filename, inhalt=inhalt.decode("utf-8"),
                  uhrzeit=uhrzeit_text)  # fmt: skip


@router.post("/uebernehmen")
def uebernehmen(
    request: Request, vid: int, session: SessionDep, _a: AdminDep,
    csrf: Annotated[str, Form()] = "", inhalt: Annotated[str, Form()] = "",
    trotzdem: Annotated[str, Form()] = "",
):  # fmt: skip
    admin_auth.csrf_pruefen(request, csrf)
    v = _veranstaltung(session, vid)
    if not v.laufend:
        return _zurueck(_ziel(v), fehler=f"Veranstaltung ist {v.zustand} und schreibgeschützt.")
    if v.freigabe_fehlt:
        return _zurueck(_ziel(v), fehler=freigabe_text(v))
    try:
        p = pruefen(session, v, lesen(inhalt))
    except DateiFehler as e:
        return _zurueck(_ziel(v), fehler=f"Datei nicht lesbar: {e}")
    if p.andere_veranstaltung and not trotzdem:
        return _zurueck(_ziel(v), fehler="Die Datei gehört zu einer anderen Veranstaltung. "
                        "Nichts übernommen (zum Übernehmen bestätigen).")  # fmt: skip
    empfangen = jetzt_ms()
    zeilen = [{**e.model_dump(), "veranstaltung_id": v.id, "empfangen_ms": empfangen, "quelle": "datei",
               "ort_id": p.ort_id if e.art.lower() == p.ort_art else None} for e in p.neu]  # fmt: skip
    if zeilen:
        session.execute(sqlite_insert(Ereignis).values(zeilen).on_conflict_do_nothing())
        saetze.einfrieren(session, v)
    session.commit()
    n = len(zeilen)
    return _zurueck(f"/admin/v/{v.id}/protokoll", ok=f"{n} {'Eintrag' if n == 1 else 'Einträge'} aus der Datei "
                    f"übernommen ({p.vorhanden} schon vorhanden, {p.ungueltig} ungültig).")  # fmt: skip
