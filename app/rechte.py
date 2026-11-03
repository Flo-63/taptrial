"""Zentrale Rechteprüfung für Ereignisse: Wer darf was an welchem Eintrag?

Die eine Stelle für POST /api/ereignisse und die Gerätedatei; eine Sperre in der Oberfläche
ersetzt diese Prüfung nie. Spätere Benutzerrollen docken an `Akteur` an.

- Ein Gerät ändert (Storno, ±1 s) nur, was es selbst erzeugt hat. Fremde Einträge ändert nur die
  Rennleitung (Rennleitungs-Link, umschaltbares Zielgerät) und der Admin.
- Einträge des Admins und einen endgültigen DSQ ändert nur der Admin.
- Ist das Ergebnis offiziell, nimmt der Server von Geräten keine neuen Ereignisse an (423).
- Schon gespeicherte Ereignisse bestätigt der Server immer (idempotent), auch nach einer Sperre.

Ablehnung einzelner Ereignisse als 422: Das Gerät verwirft dann nur dieses Ereignis, die übrigen
Einträge der Warteschlange gehen weiter durch.
"""

import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auswertung import STATUS_WERTE, Art, gueltige_ereignisse
from app.models import Ereignis, Veranstaltung

ERLAUBTE_ARTEN = {
    "start": {Art.START, Art.STORNO, Art.STATUS},
    # STATUS: Rennleitung
    "ziel": {
        Art.ZIEL,
        Art.ZUORDNUNG,
        Art.STORNO,
        Art.STATUS,
        Art.ZEITKORREKTUR,
        Art.START_ANNULLIERT,
    },
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

GERAET_ADMIN = "admin"
# Was ein Gerät ohne Rennleitungsfunktion an eigenen Einträgen stornieren darf
EIGENE_ARTEN = {
    "start": {Art.START, Art.STATUS},
    "ziel": {Art.ZIEL},
    "posten": {Art.DURCHFAHRT},
}
STATUS_DER_RENNLEITUNG = {"DNF", "DSQ_VORLAEUFIG", "DSQ"}
NUR_ADMIN_ARTEN = {Art.ERGEBNIS, Art.MERKMAL, Art.RUNDE, Art.MANUELLE_ZEIT}

OFFIZIELL_TEXT = (
    "Das Ergebnis ist offiziell. Einträge von Geräten werden erst wieder angenommen, wenn der "
    "Admin das Ergebnis auf „vorläufig“ zurücksetzt."
)
DSQ_TEXT = "Nr. {nr} ist endgültig disqualifiziert. Das kann nur der Admin ändern."
# Plausible Zeitpunkte: feste Grenzen, dazu höchstens ein Tag über der Serverzeit
ZEIT_MIN_MS = 946_684_800_000  # 01.01.2000
ZEIT_MAX_MS = 4_102_444_800_000  # 01.01.2100
ZUKUNFT_MAX_MS = 86_400_000
# Korrekturen und Status sortieren nach erstellt_ms: Weit in der Zukunft überstimmten sie alles Spätere
ZUKUNFT_MAX_FOLGE_MS = 300_000
ZEITSTEMPEL_ARTEN = {Art.START, Art.ZIEL, Art.DURCHFAHRT}
ZEIT_UNPLAUSIBEL = (
    "Die Zeit dieses Eintrags ist unplausibel. Bitte Datum und Uhr des Geräts prüfen."
)
NICHT_EIGEN = "Dieser Eintrag stammt von einem anderen Gerät. Ändern kann ihn nur die Rennleitung."
NUR_ADMIN = "Dieser Eintrag stammt vom Admin und lässt sich nur dort ändern."


class Abgelehnt(Exception):
    """Ein Ereignis (422) oder der ganze Stapel (423) wird nicht angenommen."""

    def __init__(self, text: str, status: int = 422, grund: str = ""):
        super().__init__(text)
        self.text = text
        self.status = status
        self.grund = grund

    @property
    def detail(self) -> Any:
        return {"grund": self.grund, "text": self.text} if self.grund else self.text


@dataclass(frozen=True)
class Akteur:
    rolle: str  # start, ziel, rennleitung, posten, admin, datei
    kennung: str  # steht in ereignis.zugang, z. B. „ziel“, „ziel-4“, „posten-5“
    rennleitung: bool = False  # darf Einträge anderer Geräte ändern
    nur_erfassen: bool = False  # Ziel-Link bei getrennter Rennleitung
    messpunkt: str | None = None  # Posten: eigener Messpunkt

    @property
    def admin(self) -> bool:
        return self.rolle == "admin"

    @property
    def vertraut(self) -> bool:
        """Admin; auch eine Gerätedatei: Der Admin prüft sie in der Vorschau, die Rolle des
        Geräts steht nicht in der Datei."""
        return self.rolle in ("admin", "datei")


ADMIN = Akteur("admin", "admin", rennleitung=True)
DATEI = Akteur("datei", "datei", rennleitung=True)


def rennleitung_getrennt(zugang: Any, v: Veranstaltung) -> bool:
    """„Ziel und Rennleitung getrennt“ gilt je Zielort (Standard-Zielort: an der Veranstaltung)."""
    return bool(
        zugang.ort.rennleitung_getrennt if zugang.ort is not None else v.rennleitung_getrennt
    )


def akteur_von(zugang: Any, v: Veranstaltung) -> Akteur:
    """Akteur aus dem Zugang des Tokens; Ort und Messpunkt setzt nur der Server."""
    rolle = zugang.rolle
    if rolle == "admin":
        return ADMIN
    if rolle == "posten":
        mid = str(zugang.messpunkt.id)
        return Akteur(rolle, f"posten-{mid}", messpunkt=mid)
    kennung = f"{rolle}-{zugang.ort.id}" if zugang.ort is not None else rolle
    getrennt = rolle == "ziel" and rennleitung_getrennt(zugang, v)
    return Akteur(rolle, kennung, rennleitung=rolle == "rennleitung" or (rolle == "ziel" and not getrennt),
                  nur_erfassen=getrennt)  # fmt: skip


class Pruefung:
    """Prüft Ereignisse der Reihe nach; geprüfte Ereignisse zählen als Bezug für die folgenden."""

    def __init__(self, session: Session, v: Veranstaltung, akteur: Akteur):
        self.session, self.v, self.akteur = session, v, akteur
        self.im_stapel: dict[str, Any] = {}
        self._status: dict[int, str] | None = None

    def bekannt(self, uuids: list[str]) -> dict[str, Ereignis]:
        return {
            e.uuid: e
            for e in self.session.scalars(select(Ereignis).where(Ereignis.uuid.in_(uuids)))
        }

    def schon_gespeichert(self, e: Any, vorhanden: Ereignis | None) -> bool:
        """Gleiche UUID und gleicher Inhalt: Wiederholung. Sonst keine stille Überschreibung."""
        if vorhanden is None:
            return False
        if (vorhanden.veranstaltung_id, vorhanden.art, vorhanden.geraet) != (
            self.v.id,
            e.art,
            e.geraet,
        ):
            raise Abgelehnt("Diese Kennung des Eintrags ist schon vergeben.")
        return True

    def _bezug(self, e: Any) -> Any:
        bezug = self.im_stapel.get(e.bezug_uuid) or self.session.get(Ereignis, e.bezug_uuid)
        if bezug is None or (isinstance(bezug, Ereignis) and bezug.veranstaltung_id != self.v.id):
            raise Abgelehnt("Der Eintrag, auf den sich diese Änderung bezieht, ist nicht bekannt.")
        return bezug

    def status(self) -> dict[int, str]:
        """Aktueller Status je Startnummer (STATUS und Annullierung, der letzte gültige gilt)."""
        if self._status is None:
            self._status = {}
            alle = self.session.scalars(
                select(Ereignis).where(Ereignis.veranstaltung_id == self.v.id)
            )
            for x in gueltige_ereignisse(alle):
                self._merken(x)
        return self._status

    def _merken(self, e: Any) -> None:
        if e.startnummer is None:
            return
        if e.art == Art.STATUS:
            self.status_setzen(e.startnummer, e.wert)
        elif e.art == Art.START_ANNULLIERT:
            self.status_setzen(e.startnummer, "DNS")

    def status_setzen(self, nr: int, wert: str) -> None:
        self._status[nr] = wert

    def _eigen(self, e: Any, bezug: Any) -> bool:
        """Gleicher Link und gleiche Gerätekennung. Die Kennung wählt das Gerät selbst: Sie trennt
        Geräte im normalen Betrieb, schützt aber nicht vor Fälschung innerhalb desselben Links."""
        zugang = getattr(bezug, "zugang", self.akteur.kennung)  # im Stapel: derselbe Zugang
        # Einträge von vor dieser Spalte: Die Gerätekennung allein, damit „Rückgängig“ im laufenden
        # Rennen ein Update übersteht. Aus einer Datei übernommene ändert die Rennleitung.
        return zugang in (None, self.akteur.kennung) and bezug.geraet == e.geraet

    def _bezug_pruefen(self, e: Any) -> None:
        bezug = self._bezug(e)
        if e.art == Art.ZUORDNUNG and bezug.art not in (Art.START, Art.ZIEL):
            raise Abgelehnt("Zuordnen geht nur bei Start- und Zielstempeln.")
        if e.art == Art.ZEITKORREKTUR and bezug.art not in (Art.ZIEL, Art.DURCHFAHRT):
            raise Abgelehnt("±1 s geht nur bei Ziel- und Durchfahrtstempeln.")
        if self.akteur.admin:
            return
        if (
            bezug.geraet == GERAET_ADMIN
            or getattr(bezug, "zugang", None) == GERAET_ADMIN
            or bezug.art in NUR_ADMIN_ARTEN
            or (bezug.art == Art.STATUS and bezug.wert == "DSQ")
        ):
            raise Abgelehnt(NUR_ADMIN)
        if self.akteur.rennleitung:
            return
        if self.akteur.messpunkt is not None and (
            bezug.art != Art.DURCHFAHRT or str(bezug.wert) != self.akteur.messpunkt
        ):
            raise Abgelehnt("Posten dürfen nur eigene Durchfahrten ändern.")
        erlaubt = EIGENE_ARTEN.get(self.akteur.rolle, set())
        if not self._eigen(e, bezug) or (e.art == Art.STORNO and bezug.art not in erlaubt):
            raise Abgelehnt(NICHT_EIGEN)

    def _status_pruefen(self, e: Any) -> None:
        if self.akteur.admin or e.startnummer is None:
            return
        bisher = self.status().get(e.startnummer)
        if bisher == "DSQ":
            raise Abgelehnt(DSQ_TEXT.format(nr=e.startnummer))
        if self.akteur.rolle == "start" and bisher in STATUS_DER_RENNLEITUNG:
            raise Abgelehnt(f"Nr. {e.startnummer} hat einen Status der Rennleitung. "
                            "Den ändert das Startgerät nicht.")  # fmt: skip

    def pruefen(self, e: Any) -> None:
        """Ein neues Ereignis prüfen; wirft Abgelehnt. Danach gilt es als Bezug für die folgenden."""
        if not self.akteur.admin and e.geraet == GERAET_ADMIN:
            raise Abgelehnt("Diese Gerätekennung ist dem Admin vorbehalten.")
        grenze = time.time_ns() // 1_000_000 + ZUKUNFT_MAX_MS
        if (e.zeit_ms or 0) > grenze or e.erstellt_ms > grenze:
            raise Abgelehnt(ZEIT_UNPLAUSIBEL)
        if (
            not self.akteur.admin
            and e.art not in ZEITSTEMPEL_ARTEN
            and e.erstellt_ms > grenze - ZUKUNFT_MAX_MS + ZUKUNFT_MAX_FOLGE_MS
        ):
            raise Abgelehnt(ZEIT_UNPLAUSIBEL)
        if (
            self.akteur.messpunkt is not None
            and e.art == Art.DURCHFAHRT
            and e.wert != self.akteur.messpunkt
        ):
            raise Abgelehnt("Dieses Posten-Gerät misst an einem anderen Messpunkt.")
        if e.art in (Art.ZUORDNUNG, Art.STORNO, Art.ZEITKORREKTUR, Art.RUNDE):
            self._bezug_pruefen(e)
        if e.art in (Art.STATUS, Art.START_ANNULLIERT):
            self._status_pruefen(e)
            if self._status is not None:
                self._merken(e)
        self.im_stapel[e.uuid] = e


def _nur_erfassen_pruefen(session: Session, v: Veranstaltung, ereignisse: list) -> None:
    """Ziel-Link bei getrennter Rennleitung: ±1 s/Rückgängig nur am eigenen letzten Einlauf.

    „Letzter“ gilt zum Zeitpunkt der Korrektur (erstellt_ms) – so bleiben offline gesammelte
    Batches gültig.
    """
    for e in ereignisse:
        if e.art not in NUR_ERFASSEN_ARTEN:
            raise Abgelehnt("Zuordnen, Umbuchen und Status nur über den Rennleitungs-Link")
    ziele = {z.uuid: z for z in ereignisse if z.art == Art.ZIEL}
    for e in ereignisse:
        if e.art == Art.ZIEL:
            continue
        ziel = ziele.get(e.bezug_uuid) or session.get(Ereignis, e.bezug_uuid)
        if ziel is None or ziel.art != Art.ZIEL or ziel.geraet != e.geraet:
            raise Abgelehnt("Nur der eigene letzte Einlauf ist am Ziel korrigierbar")
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
            raise Abgelehnt("Nur der eigene letzte Einlauf ist am Ziel korrigierbar")


def ist_offiziell(session: Session, v: Veranstaltung) -> bool:
    """Die letzte gültige Erklärung gilt (wie in der Auswertung)."""
    alle = session.scalars(
        select(Ereignis).where(
            Ereignis.veranstaltung_id == v.id, Ereignis.art.in_((Art.ERGEBNIS, Art.STORNO))
        )
    )
    wert = None
    for e in gueltige_ereignisse(alle):
        if e.art == Art.ERGEBNIS:
            wert = e.wert
    return wert == "OFFIZIELL"


def stapel_pruefen(session: Session, v: Veranstaltung, akteur: Akteur, ereignisse: list) -> list:
    """Prüft einen Stapel eines Geräts; gibt die noch nicht gespeicherten Ereignisse zurück."""
    pruefung = Pruefung(session, v, akteur)
    vorhanden = pruefung.bekannt([e.uuid for e in ereignisse])
    neu, gesehen = [], {}
    for e in ereignisse:
        if pruefung.schon_gespeichert(e, vorhanden.get(e.uuid)):
            continue
        if e.uuid in gesehen:
            if (gesehen[e.uuid].art, gesehen[e.uuid].geraet) != (e.art, e.geraet):
                raise Abgelehnt("Diese Kennung des Eintrags ist schon vergeben.")
            continue
        gesehen[e.uuid] = e
        neu.append(e)
    if not neu:
        return []
    if not akteur.vertraut and ist_offiziell(session, v):
        raise Abgelehnt(OFFIZIELL_TEXT, status=423, grund="offiziell")
    if akteur.nur_erfassen:
        _nur_erfassen_pruefen(session, v, neu)
    for e in neu:
        pruefung.pruefen(e)
    return neu
