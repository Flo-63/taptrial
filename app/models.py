"""ORM-Modelle (siehe KONZEPT 3)."""

import calendar
import hashlib
import secrets
from datetime import date

from sqlalchemy import (
    DDL,
    BigInteger,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from app.db import Basis

ZUSTAENDE = ("laufend", "abgeschlossen", "anonymisiert")
ANONYMISIEREN_NACH_MONATEN = 6


def neues_token() -> str:
    return secrets.token_urlsafe(24)


def plus_monate(d: date, monate: int) -> date:
    jahr, monat = divmod(d.month - 1 + monate, 12)
    jahr, monat = d.year + jahr, monat + 1
    return date(jahr, monat, min(d.day, calendar.monthrange(jahr, monat)[1]))


def _stichtag_default(context) -> date:
    datum = context.get_current_parameters()["datum"]
    return date(datum.year, 12, 31)


def _anonymisieren_default(context) -> date:
    return plus_monate(context.get_current_parameters()["datum"], ANONYMISIEREN_NACH_MONATEN)


class Veranstaltung(Basis):
    __tablename__ = "veranstaltung"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    datum: Mapped[date] = mapped_column(Date)
    stichtag_alter: Mapped[date] = mapped_column(Date, default=_stichtag_default)
    aktiv: Mapped[bool] = mapped_column(Boolean, default=False)
    # Gerätetokens je Veranstaltung (KONZEPT 8), im Admin erneuerbar
    token_start: Mapped[str] = mapped_column(String(64), unique=True, default=neues_token)
    token_ziel: Mapped[str] = mapped_column(String(64), unique=True, default=neues_token)
    # Ziel und Rennleitung trennbar (Test 08.10.): False = auf einem Gerät umschaltbar (Standard);
    # True = eigener Rennleitungs-Link, der Ziel-Token darf nur noch Einläufe erfassen
    rennleitung_getrennt: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    token_rennleitung: Mapped[str] = mapped_column(String(64), unique=True, default=neues_token)
    # Lebenszyklus (KONZEPT 8a)
    zustand: Mapped[str] = mapped_column(String(20), default="laufend", server_default="laufend")
    anonymisieren_ab: Mapped[date] = mapped_column(Date, default=_anonymisieren_default)
    # Phase 7: Dateiname der Wertung (nur noch für die Migration zu Wertungssätzen gelesen)
    wertung: Mapped[str | None] = mapped_column(String(100), nullable=True)
    strecke_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Phase 12: Klassensatz (Pflicht, ohne Angabe der Standardsatz) und Wertungssatz (leer = aus)
    klassensatz_id: Mapped[int | None] = mapped_column(ForeignKey("klassensatz.id"), nullable=True)
    wertungssatz_id: Mapped[int | None] = mapped_column(
        ForeignKey("wertungssatz.id"), nullable=True
    )
    # eingefrorene Kopie der Regeln (JSON), sobald es Ereignisse gibt
    regeln_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    eingefroren_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # geführte Einrichtung: nächster offener Schritt (1–4), leer = abgeschlossen
    einrichtung_schritt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Segmente 49: Entscheidungen des Assistenten je Längen-Platz {"plaetze": {Platz: Strecken-ID}}
    einrichtung_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Zeitpunkt des letzten CSV-Imports (Warnung bei „ersetzen“, Phase 13)
    letzter_import_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Segmentzeiten auch in öffentlichen Ergebnissen, Druck und öffentlicher CSV zeigen
    zwischenzeiten_oeffentlich: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1"
    )
    # Segmentzeiten im Kasten „Auf der Strecke“ des Zielmonitors (Segmente 9, Standard aus)
    segmente_monitor: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # Zielmonitor-Konfiguration (Segmente 14, app/monitor_konfig.py); None = Standard
    monitor_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # veraltet (phase-14): Mindestrundenzeit, entfällt mit dem Segment-Modell
    mindestrundenzeit_s: Mapped[int] = mapped_column(Integer, default=60, server_default="60")

    @property
    def laufend(self) -> bool:
        return self.zustand == "laufend"

    @property
    def freigabe_fehlt(self) -> list[str]:
        """Segmente 46: was fehlt, damit Rennereignisse angenommen werden – „einrichtung“
        (Assistent nicht abgeschlossen) und/oder „aktiv“. Leer = freigegeben."""
        return [code for code, fehlt in (("einrichtung", bool(self.einrichtung_schritt)),
                                         ("aktiv", not self.aktiv)) if fehlt]  # fmt: skip

    @property
    def freigegeben(self) -> bool:
        return not self.freigabe_fehlt


FREIGABE_SCHRITTE = {
    "einrichtung": "Einrichtung abschließen",
    "aktiv": "Veranstaltung aktiv setzen",
}
FREIGABE_FEHLT = "Zeitnahme noch nicht freigegeben"


def freigabe_text(v: "Veranstaltung") -> str:
    """„Zeitnahme noch nicht freigegeben: Einrichtung abschließen, Veranstaltung aktiv setzen.“"""
    return f"{FREIGABE_FEHLT}: " + ", ".join(FREIGABE_SCHRITTE[c] for c in v.freigabe_fehlt) + "."


class Teilnehmer(Basis):
    __tablename__ = "teilnehmer"
    __table_args__ = (UniqueConstraint("veranstaltung_id", "startnummer"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    veranstaltung_id: Mapped[int] = mapped_column(ForeignKey("veranstaltung.id"), index=True)
    startnummer: Mapped[int] = mapped_column(Integer)
    vorname: Mapped[str] = mapped_column(String(100))
    nachname: Mapped[str] = mapped_column(String(100))
    geschlecht: Mapped[str] = mapped_column(String(1))
    geburtsjahr: Mapped[int] = mapped_column(Integer)
    verein: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Merkmale: möchte das Radsportabzeichen erwerben / startet außer Konkurrenz (kein Rang)
    abzeichen: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    ausser_konkurrenz: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # beim Anonymisieren fest gespeicherte Klasse (der Jahrgang wird dann entfernt)
    klasse_fest: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # zuletzt im Admin angelegt/geändert (nicht beim Import), für die Warnung bei „ersetzen“
    geaendert_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class Ereignis(Basis):
    """Append-only: UPDATE und DELETE werden per Trigger abgewiesen."""

    __tablename__ = "ereignis"

    uuid: Mapped[str] = mapped_column(String(64), primary_key=True)
    veranstaltung_id: Mapped[int] = mapped_column(ForeignKey("veranstaltung.id"), index=True)
    art: Mapped[str] = mapped_column(String(20))
    zeit_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    startnummer: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bezug_uuid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    wert: Mapped[str | None] = mapped_column(String(20), nullable=True)
    geraet: Mapped[str] = mapped_column(String(50))
    erstellt_ms: Mapped[int] = mapped_column(BigInteger)
    empfangen_ms: Mapped[int] = mapped_column(BigInteger)
    notiz: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # ZEITKORREKTUR (Phase 14a): ±200 ms auf den ZIEL-Stempel bezug_uuid
    delta_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Herkunft, wenn nicht per Sync: "datei" = im Admin aus einer Gerätedatei übernommen (AUSBAU C2)
    quelle: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Segmente 43: Messpunkt (Start/Ziel) des Geräts bei START/ZIEL bzw. einer manuellen Zeit;
    # None = Standard-Start/-Ziel. Vom Server gesetzt, nie vom Gerät (früher in „wert“, A144).
    ort_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


for _aktion in ("UPDATE", "DELETE"):
    event.listen(
        Ereignis.__table__,
        "after_create",
        DDL(
            f"CREATE TRIGGER IF NOT EXISTS ereignis_kein_{_aktion.lower()} "
            f"BEFORE {_aktion} ON ereignis "
            "BEGIN SELECT RAISE(ABORT, 'ereignis ist append-only'); END"
        ),
    )


class Klassensatz(Basis):
    """Satz von Altersklassen (KONZEPT 7, Phase 12)."""

    __tablename__ = "klassensatz"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    klassen: Mapped[list["SatzKlasse"]] = relationship(
        order_by="SatzKlasse.position", cascade="all, delete-orphan"
    )


class SatzKlasse(Basis):
    __tablename__ = "klassensatz_klasse"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    klassensatz_id: Mapped[int] = mapped_column(ForeignKey("klassensatz.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(100))
    geschlecht: Mapped[str] = mapped_column(String(1))
    alter_von: Mapped[int] = mapped_column(Integer)
    alter_bis: Mapped[int] = mapped_column(Integer)


class Wertungssatz(Basis):
    """Leistungswertung, z. B. Deutsches Radsportabzeichen A.1 (KONZEPT 7a, Phase 12)."""

    __tablename__ = "wertungssatz"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    beschreibung: Mapped[str | None] = mapped_column(String(200), nullable=True)
    art: Mapped[str] = mapped_column(String(20))
    strecke_km_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    strecke_km_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    klassensatz_id: Mapped[int] = mapped_column(ForeignKey("klassensatz.id"))
    stufen: Mapped[list["SatzStufe"]] = relationship(
        order_by="SatzStufe.position", cascade="all, delete-orphan"
    )


class SatzStufe(Basis):
    __tablename__ = "wertungssatz_stufe"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wertungssatz_id: Mapped[int] = mapped_column(ForeignKey("wertungssatz.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    klasse: Mapped[str] = mapped_column(String(100))
    strecke_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    gold_ms: Mapped[int] = mapped_column(Integer)
    silber_ms: Mapped[int] = mapped_column(Integer)
    bronze_ms: Mapped[int] = mapped_column(Integer)


class Strecke(Basis):
    """Strecke einer Veranstaltung (Phase 13); jede Klasse fährt genau eine Strecke."""

    __tablename__ = "strecke"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    veranstaltung_id: Mapped[int] = mapped_column(ForeignKey("veranstaltung.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(100))
    laenge_km: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )  # gemessen, 1 Nachkommastelle
    klassen: Mapped[list["StreckeKlasse"]] = relationship(
        order_by="StreckeKlasse.id", cascade="all, delete-orphan"
    )
    # Segment-Modell (Segmente 7): Teile als JSON (app/segmente.py); None = keine Segmente
    teile_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Auswertungsabschnitte (Segmente 13, app/segmente.py): Einstellungen der Segment-Abschnitte und eigene
    abschnitte_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Meta-Infos (Segmente 22, nur im Admin): Beschreibung und Komoot-Link (https)
    beschreibung: Mapped[str | None] = mapped_column(Text, nullable=True)
    komoot_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Start- und Zielort (Segmente 37, Messpunkt der Art „start“/„ziel“); None = Standardort
    startort_id: Mapped[int | None] = mapped_column(ForeignKey("messpunkt.id"), nullable=True)
    zielort_id: Mapped[int | None] = mapped_column(ForeignKey("messpunkt.id"), nullable=True)
    # veraltet (phase-14): nur noch Quelle der Migration nach teile_json
    runden: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    rundenlinie: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class Messpunkt(Basis):
    """Ort der Veranstaltung (Segmente 12, 37): Startort, Messpunkt oder Zielort mit eigenem Gerät.

    Messpunkt (art „punkt“): Linie mit eigenem Zeitnehmer (Posten-Gerät), mehrfach durchfahrbar;
    jede Strecke nutzt ihn in ihren Segmenten oder nicht. Startort/Zielort: erster bzw. letzter
    Punkt einer oder mehrerer Strecken. Je Veranstaltung gibt es einen Standard-Startort und einen
    Standard-Zielort; deren Geräte nutzen die Tokens der Veranstaltung (bisherige Links).
    """

    __tablename__ = "messpunkt"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    veranstaltung_id: Mapped[int] = mapped_column(ForeignKey("veranstaltung.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(100))
    # veraltet (phase-14): km-Position und Abschnitt, nur noch Quelle der Migration
    km: Mapped[float | None] = mapped_column(Float, nullable=True)
    abschnitt: Mapped[str] = mapped_column(String(10), default="")
    token: Mapped[str] = mapped_column(String(64), unique=True, default=neues_token)
    art: Mapped[str] = mapped_column(String(10), default="punkt", server_default="punkt")
    standard: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # Segmente 65: entfernter Standard-Start/-Ziel – bleibt als Zeile (Tokens der Veranstaltung,
    # alte Links), erscheint aber nirgends; die erste neue Strecke ohne Start/Ziel holt ihn zurück
    entfernt: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # nur Zielorte (Segmente 38): Ziel und Rennleitung getrennt, eigener Rennleitungs-Link
    rennleitung_getrennt: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    token_rennleitung: Mapped[str | None] = mapped_column(
        String(64), nullable=True, default=neues_token
    )
    # veraltet: Strecke des Messpunkts vor Segmente 12 (nur Migration)
    strecke_id: Mapped[int | None] = mapped_column(ForeignKey("strecke.id"), nullable=True)


class StreckeKlasse(Basis):
    __tablename__ = "strecke_klasse"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strecke_id: Mapped[int] = mapped_column(ForeignKey("strecke.id"), index=True)
    klasse: Mapped[str] = mapped_column(String(100))


class GeloeschterToken(Basis):
    """Fingerabdruck (SHA-256) eines Gerätetokens einer gelöschten Veranstaltung – sonst nichts.

    Geräte mit diesem Token bekommen 410 „Veranstaltung gelöscht“ und räumen auf.
    """

    __tablename__ = "geloeschter_token"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fingerabdruck: Mapped[str] = mapped_column(String(64), unique=True)


def token_fingerabdruck(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class ImportEntwurf(Basis):
    """Zwischengespeicherter CSV-Import einer Veranstaltung (Vorschau mit Bearbeitung)."""

    __tablename__ = "import_entwurf"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    veranstaltung_id: Mapped[int] = mapped_column(ForeignKey("veranstaltung.id"), unique=True)
    zeilen_json: Mapped[str] = mapped_column(Text)
    dateiname: Mapped[str | None] = mapped_column(String(200), nullable=True)
    geaendert_ms: Mapped[int] = mapped_column(BigInteger)


class AppEinstellung(Basis):
    """Globale Einstellung der Installation als Schlüssel/Wert (Release 1/3, app/installation.py)."""

    __tablename__ = "app_einstellung"

    schluessel: Mapped[str] = mapped_column(String(50), primary_key=True)
    wert: Mapped[str] = mapped_column(Text, default="")


def aktive_veranstaltung(session: Session) -> Veranstaltung | None:
    return session.scalars(
        select(Veranstaltung).where(Veranstaltung.aktiv.is_(True)).order_by(Veranstaltung.id.desc())
    ).first()


class GeraetUhr(Basis):
    """Zuletzt gemeldete Uhrabweichung je Gerätekennung (AUSBAU A2, nur Anzeige, keine Korrektur).

    abweichung_ms = Serverzeit − Geräteuhr; gemessen_ms = Empfang der Meldung (Serveruhr).
    """

    __tablename__ = "geraet_uhr"
    __table_args__ = (UniqueConstraint("veranstaltung_id", "geraet"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    veranstaltung_id: Mapped[int] = mapped_column(ForeignKey("veranstaltung.id"), index=True)
    geraet: Mapped[str] = mapped_column(String(50))
    rolle: Mapped[str] = mapped_column(String(20))
    abweichung_ms: Mapped[int] = mapped_column(Integer)
    gemessen_ms: Mapped[int] = mapped_column(BigInteger)
