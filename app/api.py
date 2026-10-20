"""JSON-API für Start- und Zielgerät: Synchronisation und Stand."""

import re
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app import saetze
from app.auswertung import (
    ERGEBNIS_WERTE,
    KORREKTUR_SCHRITT_MS,
    KORREKTUR_SCHRITTE_MS,
    MERKMAL_WERTE,
    STATUS_WERTE,
    Art,
    Ergebnis,
)
from app.auth import SessionDep, Zugang, erfordere
from app.models import (
    FREIGABE_SCHRITTE,
    Ereignis,
    GeraetUhr,
    Veranstaltung,
    aktive_veranstaltung,
    freigabe_text,
)

router = APIRouter(prefix="/api")

LETZTE_ZIELE = 10

ERLAUBTE_ARTEN = {
    "start": {Art.START, Art.STORNO, Art.STATUS},
    # STATUS: Rennleitung
    "ziel": {Art.ZIEL, Art.ZUORDNUNG, Art.STORNO, Art.STATUS, Art.ZEITKORREKTUR},
    # Posten: nur Durchfahrten am eigenen Messpunkt und deren Korrektur/Rückgängig
    "posten": {Art.DURCHFAHRT, Art.ZEITKORREKTUR, Art.STORNO},
    "admin": set(Art),
}
ERLAUBTE_ARTEN["rennleitung"] = ERLAUBTE_ARTEN["ziel"]  # eigener Rennleitungs-Link (getrennt)
# Ziel-Link bei getrennter Rennleitung: nur Einläufe, ±1 s und Rückgängig am eigenen letzten
NUR_ERFASSEN_ARTEN = {Art.ZIEL, Art.ZEITKORREKTUR, Art.STORNO}


# Erlaubte STATUS-Werte je Rolle: endgültiger DSQ nur durch den Admin
ERLAUBTE_STATUS = {
    "start": {"DNS", "OK"},
    "ziel": {"DNF", "DSQ_VORLAEUFIG", "OK"},
    "rennleitung": {"DNF", "DSQ_VORLAEUFIG", "OK"},
    "admin": set(STATUS_WERTE),
}


def jetzt_ms() -> int:
    return time.time_ns() // 1_000_000


class EreignisEin(BaseModel):
    uuid: str = Field(min_length=1, max_length=64)
    art: Art
    zeit_ms: int | None = None
    startnummer: int | None = Field(default=None, gt=0)
    bezug_uuid: str | None = Field(default=None, max_length=64)
    wert: str | None = Field(default=None, max_length=20)
    geraet: str = Field(min_length=1, max_length=50)
    erstellt_ms: int
    notiz: str | None = Field(default=None, max_length=500)
    veranstaltung_id: int | None = None
    delta_ms: int | None = None  # nur ZEITKORREKTUR: ±200

    @model_validator(mode="after")
    def _pflichtfelder(self):
        a = self.art
        if a in (Art.START, Art.ZIEL, Art.MANUELLE_ZEIT) and self.zeit_ms is None:
            raise ValueError(f"{a}: zeit_ms fehlt")
        if a in (Art.START, Art.STATUS, Art.MANUELLE_ZEIT) and self.startnummer is None:
            raise ValueError(f"{a}: startnummer fehlt")
        if a == Art.DURCHFAHRT and (
            self.zeit_ms is None or self.startnummer is None or not (self.wert or "").isdigit()
        ):
            raise ValueError("DURCHFAHRT: zeit_ms, startnummer und wert (Messpunkt) nötig")
        if a == Art.RUNDE and not re.fullmatch(r"GILT|VERWORFEN", self.wert or ""):
            raise ValueError("RUNDE: wert muss GILT oder VERWORFEN sein")
        if a in (Art.ZUORDNUNG, Art.STORNO, Art.ZEITKORREKTUR, Art.RUNDE) and not self.bezug_uuid:
            raise ValueError(f"{a}: bezug_uuid fehlt")
        if a == Art.ZEITKORREKTUR and abs(self.delta_ms or 0) not in KORREKTUR_SCHRITTE_MS:
            raise ValueError(f"ZEITKORREKTUR: delta_ms muss ±{KORREKTUR_SCHRITT_MS} sein")
        if a != Art.ZEITKORREKTUR and self.delta_ms is not None:
            raise ValueError("delta_ms nur bei ZEITKORREKTUR")
        if a == Art.STATUS and self.wert not in STATUS_WERTE:
            raise ValueError(f"STATUS: wert muss einer von {', '.join(STATUS_WERTE)} sein")
        if a == Art.STATUS and self.wert == "DSQ_VORLAEUFIG" and not (self.notiz or "").strip():
            raise ValueError("DSQ vorläufig: Grund (notiz) ist Pflicht")
        if a == Art.MERKMAL and (self.wert not in MERKMAL_WERTE or self.startnummer is None):
            raise ValueError(f"MERKMAL: startnummer und wert ({', '.join(MERKMAL_WERTE)}) nötig")
        if a == Art.ERGEBNIS and self.wert not in ERGEBNIS_WERTE:
            raise ValueError("ERGEBNIS: wert muss OFFIZIELL oder VORLAEUFIG sein")
        if a == Art.MANUELLE_ZEIT and self.wert not in (None, "START", "ZIEL"):
            raise ValueError("MANUELLE_ZEIT: wert muss START oder ZIEL sein")
        return self


class Batch(BaseModel):
    ereignisse: list[EreignisEin] = Field(max_length=1000)


class Bestaetigung(BaseModel):
    bestaetigt: list[str]
    server_ms: int  # Uhrzeit des Servers für die Messung der Uhrabweichung


MAX_UHR_ABWEICHUNG_MS = 86_400_000  # unplausible Meldungen werden ignoriert


def uhr_meldung_merken(request: Request, session: Session, zugang: Zugang) -> None:
    """Merkt die vom Gerät gemeldete letzte Uhrabweichung (Kopfzeilen X-Geraet, X-Uhr-Abweichung).

    Nur zur Anzeige im Admin-Reiter „Geräte“; gemessene Zeiten werden nie verändert.
    """
    geraet = (request.headers.get("x-geraet") or "").strip()[:50]
    try:
        abweichung = int(request.headers.get("x-uhr-abweichung", ""))
    except ValueError:
        return
    v = zugang.veranstaltung
    if v is None or not geraet or abs(abweichung) > MAX_UHR_ABWEICHUNG_MS:
        return
    zeile = session.scalars(
        select(GeraetUhr).where(GeraetUhr.veranstaltung_id == v.id, GeraetUhr.geraet == geraet)
    ).first()
    if zeile is None:
        zeile = GeraetUhr(veranstaltung_id=v.id, geraet=geraet)
        session.add(zeile)
    rolle = f"posten-{zugang.messpunkt.id}" if zugang.messpunkt is not None else zugang.rolle
    if zugang.ort is not None:  # Gerät eines weiteren Start-/Zielorts
        rolle = f"{zugang.rolle}-{zugang.ort.id}"
    zeile.rolle, zeile.abweichung_ms, zeile.gemessen_ms = rolle, abweichung, jetzt_ms()
    session.commit()


def _veranstaltung_oder_409(session: Session) -> Veranstaltung:
    v = aktive_veranstaltung(session)
    if v is None:
        raise HTTPException(409, "Keine aktive Veranstaltung")
    return v


def veranstaltung_fuer(zugang: Zugang, session: Session, veranstaltung_id: int | None = None):
    """Geräte: Veranstaltung ihres Tokens. Admin: angegebene oder aktive Veranstaltung."""
    if zugang.veranstaltung is not None:
        return zugang.veranstaltung
    if veranstaltung_id is not None:
        v = session.get(Veranstaltung, veranstaltung_id)
        if v is None:
            raise HTTPException(422, f"Unbekannte Veranstaltung {veranstaltung_id}")
        return v
    return _veranstaltung_oder_409(session)


def ort_von(session: Session, zugang: Zugang, v: Veranstaltung):
    """Start-/Zielort eines Start-, Ziel- oder Rennleitungsgeräts, sonst None."""
    if zugang.rolle not in ("start", "ziel", "rennleitung"):
        return None
    if zugang.ort is not None:
        return zugang.ort
    from app import strecken

    return strecken.standard_ort(session, v, "start" if zugang.rolle == "start" else "ziel")


def rennleitung_getrennt(zugang: Zugang, v: Veranstaltung) -> bool:
    """„Ziel und Rennleitung getrennt“ gilt je Zielort (Standard-Zielort: an der Veranstaltung)."""
    return bool(
        zugang.ort.rennleitung_getrennt if zugang.ort is not None else v.rennleitung_getrennt
    )


def ergebnis_berechnen(request: Request, session: Session, v: Veranstaltung) -> Ergebnis:
    from app.korrekturen import ergebnis

    return ergebnis(session, v)


def _nur_erfassen_pruefen(session: Session, v: Veranstaltung, batch: "Batch") -> None:
    """Ziel-Link bei getrennter Rennleitung: ±1 s/Rückgängig nur am eigenen letzten Einlauf.

    „Letzter“ gilt zum Zeitpunkt der Korrektur (erstellt_ms) – so bleiben offline gesammelte
    Batches gültig. 422 statt 403, damit das Gerät nur das eine Ereignis ablehnt.
    """
    for e in batch.ereignisse:
        if e.art not in NUR_ERFASSEN_ARTEN:
            raise HTTPException(422, "Zuordnen, Umbuchen und Status nur über den Rennleitungs-Link")
    ziele = {z.uuid: z for z in batch.ereignisse if z.art == Art.ZIEL}
    for e in batch.ereignisse:
        if e.art == Art.ZIEL:
            continue
        ziel = ziele.get(e.bezug_uuid) or session.get(Ereignis, e.bezug_uuid)
        if ziel is None or ziel.art != Art.ZIEL or ziel.geraet != e.geraet:
            raise HTTPException(422, "Nur der eigene letzte Einlauf ist am Ziel korrigierbar")
        eigene = [
            *session.scalars(
                select(Ereignis.erstellt_ms).where(
                    Ereignis.veranstaltung_id == v.id, Ereignis.art == Art.ZIEL,
                    Ereignis.geraet == e.geraet, Ereignis.uuid != ziel.uuid,
                )
            ),
            *(z.erstellt_ms for z in ziele.values() if z.geraet == e.geraet and z.uuid != ziel.uuid),
        ]  # fmt: skip
        if any(ziel.erstellt_ms <= t <= e.erstellt_ms for t in eigene):
            raise HTTPException(422, "Nur der eigene letzte Einlauf ist am Ziel korrigierbar")


@router.post("/ereignisse", response_model=Bestaetigung)
def ereignisse_empfangen(
    request: Request,
    batch: Batch,
    session: SessionDep,
    zugang: Annotated[
        Zugang, Depends(erfordere("start", "ziel", "rennleitung", "posten", "admin"))
    ],
) -> Bestaetigung:
    bestaetigt = ereignisse_annehmen(session, zugang, batch)
    uhr_meldung_merken(request, session, zugang)
    return Bestaetigung(bestaetigt=bestaetigt, server_ms=jetzt_ms())


def ereignisse_annehmen(session: Session, zugang: Zugang, batch: Batch) -> list[str]:
    """Prüft und speichert einen Stapel wie vom Gerät gesendet; gibt die bestätigten UUIDs zurück
    (HTTPException bei Ablehnung). Auch für die Demo-Veranstaltung."""
    rolle = zugang.rolle
    for e in batch.ereignisse:
        if e.art not in ERLAUBTE_ARTEN[rolle]:
            raise HTTPException(403, f"Rolle {rolle} darf keine {e.art}-Ereignisse senden")
        if e.art == Art.STATUS and e.wert not in ERLAUBTE_STATUS[rolle]:
            raise HTTPException(403, f"Rolle {rolle} darf Status {e.wert} nicht setzen")
        if rolle == "admin" and not (e.notiz or "").strip():
            raise HTTPException(422, "Admin-Korrekturen brauchen eine Notiz")
    if rolle == "posten":
        _posten_pruefen(zugang, session, batch)
    ziel_ids: dict[int | None, int] = {}
    for vid in {e.veranstaltung_id for e in batch.ereignisse}:
        v = veranstaltung_fuer(zugang, session, vid)
        if zugang.veranstaltung is not None and vid not in (None, v.id):
            raise HTTPException(422, "Ereignis gehört zu einer anderen Veranstaltung als der Token")
        if not v.laufend:
            raise HTTPException(409, f"Veranstaltung ist {v.zustand} und schreibgeschützt")
        if v.freigabe_fehlt:  # Rennereignisse erst nach Freigabe
            raise HTTPException(
                423, freigabe_daten(v) | {"grund": "nicht_freigegeben", "text": freigabe_text(v)}
            )
        if rolle == "ziel" and rennleitung_getrennt(zugang, v):
            _nur_erfassen_pruefen(session, v, batch)
        ziel_ids[vid] = v.id
    empfangen = jetzt_ms()
    bestaetigt: list[str] = []
    zeilen: list[dict] = []
    for e in batch.ereignisse:
        if e.uuid in bestaetigt:
            continue
        daten = e.model_dump()
        daten["veranstaltung_id"] = ziel_ids[e.veranstaltung_id]
        # Messpunkt des Geräts in eigener Spalte (leer = Standard-Start/-Ziel)
        ort = zugang.ort.id if zugang.ort is not None and e.art in (Art.START, Art.ZIEL) else None
        daten["ort_id"] = ort
        zeilen.append({**daten, "empfangen_ms": empfangen})
        bestaetigt.append(e.uuid)
    if zeilen:
        # idempotent: bereits vorhandene UUIDs werden ignoriert, nie überschrieben
        session.execute(sqlite_insert(Ereignis).values(zeilen).on_conflict_do_nothing())
        for vid in set(ziel_ids.values()):  # ab dem ersten Ereignis: Klassen/Wertung einfrieren
            saetze.einfrieren(session, session.get(Veranstaltung, vid))
    session.commit()
    return bestaetigt


def _posten_pruefen(zugang: Zugang, session: Session, batch: Batch) -> None:
    """Posten: nur Durchfahrten am eigenen Messpunkt, Korrektur/Rückgängig nur für diese."""
    eigen = str(zugang.messpunkt.id)
    im_stapel = {e.uuid: e for e in batch.ereignisse}
    for e in batch.ereignisse:
        if e.art == Art.DURCHFAHRT and e.wert != eigen:
            raise HTTPException(403, "Posten darf nur Durchfahrten für seinen Messpunkt senden")
        if e.art in (Art.ZEITKORREKTUR, Art.STORNO):
            bezug = im_stapel.get(e.bezug_uuid) or session.get(Ereignis, e.bezug_uuid)
            if bezug is None or bezug.art != Art.DURCHFAHRT or bezug.wert != eigen:
                raise HTTPException(403, "Posten darf nur eigene Durchfahrten korrigieren")


def freigabe_daten(v: Veranstaltung) -> dict:
    """{"frei": bool, "fehlt": [{"code", "text"}]} – maschinenlesbar für Geräte."""
    fehlt = v.freigabe_fehlt
    return {"frei": not fehlt, "fehlt": [{"code": c, "text": FREIGABE_SCHRITTE[c]} for c in fehlt]}


@router.get("/stand")
def stand(
    request: Request,
    session: SessionDep,
    zugang: Annotated[
        Zugang, Depends(erfordere("start", "ziel", "rennleitung", "posten", "admin"))
    ],
    veranstaltung_id: int | None = None,
) -> dict:
    v = veranstaltung_fuer(zugang, session, veranstaltung_id)
    uhr_meldung_merken(request, session, zugang)
    erg = ergebnis_berechnen(request, session, v)
    zeilen = sorted(erg.zeilen, key=lambda z: z.startnummer)
    daten = _stand(erg, v, zeilen)
    daten["rolle"] = zugang.rolle
    daten["freigabe"] = freigabe_daten(v)  # Geräte sperren sich bis zur Freigabe
    ort = ort_von(session, zugang, v)
    if ort is not None:  # Ort des Geräts und die Strecken, die dort beginnen bzw. enden
        _ort_daten(session, v, erg, daten, ort)
        daten["rennleitung_getrennt"] = rennleitung_getrennt(zugang, v)
    if zugang.rolle == "posten":  # Segmente: eigener Messpunkt, letzte Durchfahrten dort
        from app import segmente

        m = zugang.messpunkt
        # Gemeinsamer Messpunkt – Strecken, die vorbeiführen, und Durchfahrten je Strecke
        erwartet = {
            i: segmente.erwartete_anzahl(sd.teile).get(str(m.id), 0)
            for i, sd in enumerate(erg.strecken)
        }
        erwartet = {i: n for i, n in erwartet.items() if n}
        daten["messpunkt"] = {"id": m.id, "name": m.name, "strecken": sorted(erwartet),
                              "erwartet": {str(i): n for i, n in erwartet.items()}}  # fmt: skip
        hier = [d for d in erg.durchfahrtstempel if d.ort == str(m.id)]
        daten["durchfahrten"] = [
            {"uuid": d.uuid, "zeit_ms": d.zeit_ms, "startnummer": d.startnummer,
             "korrektur_ms": d.korrektur_ms}
            for d in sorted(hier, key=lambda d: d.zeit_ms, reverse=True)[:LETZTE_ZIELE]
        ]  # fmt: skip
    return daten


def _ort_daten(session: Session, v: Veranstaltung, erg: Ergebnis, daten: dict, ort) -> None:
    """Startgerät: nur Fahrer der Strecken, die an seinem Startort beginnen (eigene Startfolge).
    Zielgerät/Rennleitung: alle Fahrer, dazu je Zielort dessen Strecken (bevorzugt angeboten)."""
    from app import strecken

    standard = {art: str(i) for art, i in strecken.standard_ids(session, v).items()}

    def am_ort(oid: str, art: str) -> list[int]:
        return [i for i, sd in enumerate(erg.strecken)
                if (getattr(sd, f"{art}ort").id if getattr(sd, f"{art}ort") else standard[art]) == oid]  # fmt: skip

    hier = am_ort(str(ort.id), ort.art)
    daten["ort"] = {"id": ort.id, "name": ort.name, "art": ort.art, "strecken": hier}
    if ort.art == "start":
        daten["teilnehmer"] = [t for t in daten["teilnehmer"] if t["strecke"] in hier]
        nummern = {t["startnummer"] for t in daten["teilnehmer"]}
        daten["gestartet"] = [nr for nr in daten["gestartet"] if nr in nummern]
        return
    zielorte = [m for m in strecken.orte_von(session, v, "ziel")]
    daten["ziel_orte"] = {str(m.id): am_ort(str(m.id), "ziel") for m in zielorte}
    for o in daten["offene_ziele"]:
        o["ort"] = o["ort"] or standard["ziel"]


def _stand(erg: Ergebnis, v: Veranstaltung, zeilen) -> dict:
    return {
        "veranstaltung": {"id": v.id, "name": v.name, "datum": v.datum.isoformat()},
        "teilnehmer": [
            {
                "startnummer": z.startnummer,
                "vorname": z.vorname,
                "nachname": z.nachname,
                "verein": z.verein,
                "klasse": z.klasse,
                "status": z.status.value,
                "start_ms": z.start_ms,
                "ziel_ms": z.ziel_ms,
                "fahrzeit_ms": z.fahrzeit_ms,
                "dsq_vorlaeufig": z.dsq_vorlaeufig,
                "stufe": z.stufe,
                "ausser_konkurrenz": z.ausser_konkurrenz,
                "strecke": z.strecke,  # Index in „strecken“
                # Segmente: nächste Durchfahrt je Messpunkt, Segmentzeiten
                "naechste_runde": z.naechste_runde,
                "zwischenzeiten": [[x.label, x.zeit_ms] for x in z.segmentzeiten],
            }
            for z in zeilen
        ],
        "strecken": [
            {
                "name": s.name,
                "laenge_km": s.zehntel_km / 10 if s.zehntel_km else None,
            }
            for s in erg.strecken
        ],
        "gestartet": [z.startnummer for z in zeilen if z.start_ms is not None],
        "im_ziel": [z.startnummer for z in zeilen if z.ziel_ms is not None],
        "offene_ziele": [
            {
                "uuid": o.uuid,
                "zeit_ms": o.zeit_ms,
                "geraet": o.geraet,
                "korrektur_ms": o.korrektur_ms,
                "ort": o.ort,
            }
            for o in erg.offene_ziele
        ],
        "letzte_ziele": [
            {
                "uuid": z.uuid,
                "zeit_ms": z.zeit_ms,
                "geraet": z.geraet,
                "startnummer": z.startnummer,
                "korrektur_ms": z.korrektur_ms,
            }
            for z in sorted(erg.zielstempel, key=lambda z: z.zeit_ms, reverse=True)[:LETZTE_ZIELE]
        ],
        "konflikte": len(erg.konflikte),
        "rennleitung_getrennt": bool(v.rennleitung_getrennt),
        "server_ms": jetzt_ms(),
    }


@router.get("/ereignisse")
def protokoll(
    session: SessionDep,
    zugang: Annotated[Zugang, Depends(erfordere("admin"))],
    startnummer: int | None = None,
    veranstaltung_id: int | None = None,
) -> dict:
    v = veranstaltung_fuer(zugang, session, veranstaltung_id)
    abfrage = select(Ereignis).where(Ereignis.veranstaltung_id == v.id)
    if startnummer is not None:
        abfrage = abfrage.where(Ereignis.startnummer == startnummer)
    abfrage = abfrage.order_by(Ereignis.erstellt_ms, Ereignis.empfangen_ms)
    return {
        "ereignisse": [
            {c.name: getattr(e, c.name) for c in Ereignis.__table__.columns}
            for e in session.scalars(abfrage)
        ]
    }
