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

from app import admin_auth, rechte, saetze, strecken
from app.admin import AdminDep, _seite, _veranstaltung, _zurueck
from app.anmeldung import ausloeser
from app.api import EreignisEin, jetzt_ms
from app.auth import SessionDep
from app.korrekturen import uhrzeit_text
from app.models import Ereignis, Veranstaltung, freigabe_text
from app.rechte import ERLAUBTE_ARTEN, ERLAUBTE_STATUS

router = APIRouter(prefix="/admin/v/{vid}/geraetedatei", include_in_schema=False)

MAX_GROESSE = 2_000_000
MAX_FORMULARFELD = 2 * MAX_GROESSE  # die Datei als verstecktes Feld der Vorschau
# Alle Ereignisarten der Geräte: Start, Ziel mit Rennleitung, Posten
GERAETE_ARTEN = ERLAUBTE_ARTEN["start"] | ERLAUBTE_ARTEN["ziel"] | ERLAUBTE_ARTEN["posten"]
ZEILEN_JE_INSERT = 500  # SQLite begrenzt die Variablen je Anweisung
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
    gruende: list[str] = field(default_factory=list)  # abgewiesene Einträge: warum (ohne Doppelte)


def lesen(inhalt: bytes | str) -> dict:
    try:
        daten = json.loads(inhalt.lstrip("\ufeff") if isinstance(inhalt, str) else inhalt)
    except (ValueError, UnicodeDecodeError, RecursionError) as e:
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
    messpunkte = {str(m.id) for m in strecken.orte_von(session, v)}
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
        if e.art == "DURCHFAHRT" and e.wert not in messpunkte:
            p.ungueltig += 1
            continue
        if all(e.uuid != x.uuid for x in gueltig):
            gueltig.append(e)
    bekannt = set(
        session.scalars(select(Ereignis.uuid).where(Ereignis.uuid.in_([e.uuid for e in gueltig])))
    )
    p.vorhanden = sum(e.uuid in bekannt for e in gueltig)
    rechte_pruefung = rechte.Pruefung(session, v, rechte.DATEI)
    for e in gueltig:
        if e.uuid in bekannt:
            continue
        try:  # dieselbe Prüfung wie beim Sync: Bezug bekannt, endgültiger DSQ, Admin-Einträge
            rechte_pruefung.pruefen(e)
        except rechte.Abgelehnt as a:
            p.ungueltig += 1
            if a.text not in p.gruende:
                p.gruende.append(a.text)
            continue
        p.neu.append(e)
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
        inhalt.decode("utf-8-sig")  # UTF-16 u. Ä. versteht json, das Formular unten aber nicht
    except (DateiFehler, UnicodeDecodeError) as e:
        return _zurueck(_ziel(v), fehler=f"Datei nicht lesbar: {e}")
    return _seite(request, "admin_geraetedatei.html", v=v, reiter="protokoll", p=p,
                  dateiname=datei.filename, inhalt=inhalt.decode("utf-8-sig"),
                  uhrzeit=uhrzeit_text)  # fmt: skip


@router.post("/uebernehmen")
async def uebernehmen(request: Request, vid: int, session: SessionDep, _a: AdminDep):
    # Selbst lesen: Der Dateiinhalt ist ein Formularfeld, Starlette begrenzt Felder sonst auf 1 MB
    formular = await request.form(max_part_size=MAX_FORMULARFELD)
    csrf, inhalt = str(formular.get("csrf", "")), str(formular.get("inhalt", ""))
    trotzdem = str(formular.get("trotzdem", ""))
    admin_auth.csrf_pruefen(request, csrf)
    v = _veranstaltung(session, vid)
    if not v.laufend:
        return _zurueck(_ziel(v), fehler=f"Veranstaltung ist {v.zustand} und schreibgeschützt.")
    if v.freigabe_fehlt:
        return _zurueck(_ziel(v), fehler=freigabe_text(v))
    if rechte.ist_offiziell(session, v):
        return _zurueck(_ziel(v), fehler=rechte.OFFIZIELL_TEXT)
    try:
        p = pruefen(session, v, lesen(inhalt))
    except DateiFehler as e:
        return _zurueck(_ziel(v), fehler=f"Datei nicht lesbar: {e}")
    if p.andere_veranstaltung and not trotzdem:
        return _zurueck(_ziel(v), fehler="Die Datei gehört zu einer anderen Veranstaltung. "
                        "Nichts übernommen (zum Übernehmen bestätigen).")  # fmt: skip
    empfangen = jetzt_ms()
    zeilen = [{**e.model_dump(), "veranstaltung_id": v.id, "empfangen_ms": empfangen, "quelle": "datei",
               "zugang": rechte.DATEI.kennung, "benutzer": ausloeser(),
               "ort_id": p.ort_id if e.art.lower() == p.ort_art else None} for e in p.neu]  # fmt: skip
    if zeilen:
        for i in range(0, len(zeilen), ZEILEN_JE_INSERT):
            block = zeilen[i : i + ZEILEN_JE_INSERT]
            session.execute(sqlite_insert(Ereignis).values(block).on_conflict_do_nothing())
        saetze.einfrieren(session, v)
    session.commit()
    n = len(zeilen)
    return _zurueck(f"/admin/v/{v.id}/protokoll", ok=f"{n} {'Eintrag' if n == 1 else 'Einträge'} aus der Datei "
                    f"übernommen ({p.vorhanden} schon vorhanden, {p.ungueltig} ungültig).")  # fmt: skip
