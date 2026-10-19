"""CSV-Import der Teilnehmer: alles oder nichts, mit verständlicher Fehlerliste."""

import csv
import io
import time
from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.einstellungen import PROJEKT_DIR
from app.klassen import GESCHLECHTER, KeineKlasseFehler, Klasse, klassifizieren
from app.models import Teilnehmer, Veranstaltung

JA = ("ja", "1", "x")
NEIN = ("nein", "0", "")
JA_NEIN_TEXT = "ja/nein, 1/0, x/leer"
TRENNZEICHEN = (";", ",")
BEISPIEL_CSV = PROJEKT_DIR / "config" / "teilnehmer_beispiel.csv"


@dataclass(frozen=True)
class Spalte:
    name: str
    pflicht: bool
    beschreibung: str
    werte: str = ""
    standard: str = ""


# Einzige Definition der CSV-Spalten: Import und Hinweistext im Admin nutzen sie beide.
SPALTEN = (
    Spalte("startnummer", True, "Startnummer", "ganze Zahl größer 0, eindeutig"),
    Spalte("vorname", True, "Vorname"),
    Spalte("nachname", True, "Nachname"),
    Spalte("geschlecht", True, "Geschlecht", "m, w, d"),
    Spalte("geburtsjahr", True, "Geburtsjahr", "vierstellig, z. B. 1985"),
    Spalte("verein", False, "Verein", "beliebiger Text", "leer"),
    Spalte("abzeichen", False, "möchte das Radsportabzeichen erwerben", JA_NEIN_TEXT, "nein"),
    Spalte("ausser_konkurrenz", False, "fährt außer Konkurrenz (Zeit ohne Rang)", JA_NEIN_TEXT,
           "nein"),
)  # fmt: skip
PFLICHT = tuple(s.name for s in SPALTEN if s.pflicht)
MERKMALE = tuple(s.name for s in SPALTEN if s.werte == JA_NEIN_TEXT)  # optionale Ja/Nein-Spalten


def ja_nein(wert: str | None) -> bool | None:
    """ja/nein, 1/0, x/leer – Groß-/Kleinschreibung und Leerzeichen egal; None = unbekannt."""
    w = (wert or "").strip().lower()
    if w in JA:
        return True
    if w in NEIN:
        return False
    return None


BEZEICHNUNG = {"vorname": "Vorname", "nachname": "Nachname"}


class ImportFehler(ValueError):
    def __init__(self, fehler: list[str]):
        super().__init__("; ".join(fehler))
        self.fehler = fehler


def _dekodieren(inhalt: bytes) -> str:
    try:
        return inhalt.decode("utf-8-sig")
    except UnicodeDecodeError:
        return inhalt.decode("cp1252")


@dataclass(frozen=True)
class Zellmeldung:
    """Fehler oder Warnung zu einer Zelle (Zeilenindex ab 0, Spaltenname aus SPALTEN)."""

    index: int
    feld: str
    text: str


@dataclass
class Pruefung:
    gueltig: list[dict]
    fehler: list[Zellmeldung]
    warnungen: list[Zellmeldung]


def csv_lesen(inhalt: bytes) -> tuple[list[dict], list[int], list[str]]:
    """CSV → (Zeilen mit allen Spalten als Text, Zeilennummern in der Datei, Fehler im Kopf).

    Spaltennamen ohne Groß-/Kleinschreibung; ganz leere Zeilen werden übersprungen.
    """
    text = _dekodieren(inhalt)
    if not text.strip():
        return [], [], ["Datei ist leer"]
    kopfzeile = text.splitlines()[0]
    trenner = TRENNZEICHEN[0] if kopfzeile.count(";") >= kopfzeile.count(",") else TRENNZEICHEN[1]
    leser = csv.DictReader(io.StringIO(text), delimiter=trenner)
    spalten = {(s or "").strip().lower(): s for s in leser.fieldnames or []}
    fehlend = [s for s in PFLICHT if s not in spalten]
    if fehlend:
        return [], [], [f"Spalte „{s}“ fehlt" for s in fehlend]
    zeilen, nummern = [], []
    for nr, roh in enumerate(leser, start=2):
        zeile = {sp.name: (roh.get(spalten[sp.name]) or "") if sp.name in spalten else ""
                 for sp in SPALTEN}  # fmt: skip
        if not any(w.strip() for w in zeile.values()):
            continue
        zeilen.append(zeile)
        nummern.append(nr)
    return zeilen, nummern, []


def pruefe_zeilen(
    zeilen: list[dict],
    stichtag: date,
    klassen: list[Klasse],
    vergeben: set[int] | None = None,
    nummern: list[int] | None = None,
) -> Pruefung:
    """Prüft Teilnehmerzeilen (Texte je Spalte) und meldet Fehler/Warnungen je Zelle."""
    vergeben = vergeben or set()
    nummern = nummern or [i + 1 for i in range(len(zeilen))]
    fehler: list[Zellmeldung] = []
    warnungen: list[Zellmeldung] = []
    gueltig: list[dict] = []
    gesehen: dict[int, int] = {}
    namen: dict[tuple[str, str], int] = {}
    for i, roh in enumerate(zeilen):
        werte = {k: str(roh.get(k) or "").strip() for k in (*PFLICHT, "verein")}
        vorher = len(fehler)

        def melden(feld: str, text: str, i: int = i) -> None:
            fehler.append(Zellmeldung(i, feld, text))

        startnummer = None
        if werte["startnummer"].isdigit() and int(werte["startnummer"]) > 0:
            startnummer = int(werte["startnummer"])
            if startnummer in gesehen:
                melden("startnummer",
                       f"Startnummer {startnummer} doppelt (bereits in Zeile {gesehen[startnummer]})")  # fmt: skip
            elif startnummer in vergeben:
                melden("startnummer", f"Startnummer {startnummer} ist bereits vergeben")
            gesehen.setdefault(startnummer, nummern[i])
        else:
            melden("startnummer", f"ungültige Startnummer „{werte['startnummer']}“")

        for feld in ("vorname", "nachname"):
            if not werte[feld]:
                melden(feld, f"{BEZEICHNUNG[feld]} fehlt")

        geschlecht = werte["geschlecht"].lower()
        if geschlecht not in GESCHLECHTER:
            melden(
                "geschlecht", f"ungültiges Geschlecht „{werte['geschlecht']}“ (erlaubt: m, w, d)"
            )

        geburtsjahr = None
        if werte["geburtsjahr"].isdigit() and 1900 <= int(werte["geburtsjahr"]) <= stichtag.year:
            geburtsjahr = int(werte["geburtsjahr"])
        else:
            melden("geburtsjahr", f"ungültiges Geburtsjahr „{werte['geburtsjahr']}“")

        if geschlecht in GESCHLECHTER and geburtsjahr is not None:
            try:
                klassifizieren(SimpleNamespace(geschlecht=geschlecht, geburtsjahr=geburtsjahr),
                               stichtag, klassen)  # fmt: skip
            except KeineKlasseFehler as e:
                melden("geburtsjahr", str(e))

        flags = {}
        for m in MERKMALE:
            roher_wert = str(roh.get(m) or "")
            flags[m] = ja_nein(roher_wert)
            if flags[m] is None:
                melden(
                    m,
                    f"ungültiger Wert „{roher_wert.strip()}“ in Spalte {m} (erlaubt: {JA_NEIN_TEXT})",
                )

        name = (werte["vorname"].lower(), werte["nachname"].lower())
        if all(name):
            if name in namen:
                warnungen.append(
                    Zellmeldung(i, "nachname", f"gleicher Name wie in Zeile {namen[name]}")
                )
            namen.setdefault(name, nummern[i])

        if len(fehler) == vorher:
            gueltig.append({
                "startnummer": startnummer, "vorname": werte["vorname"],
                "nachname": werte["nachname"], "geschlecht": geschlecht,
                "geburtsjahr": geburtsjahr, "verein": werte["verein"] or None, **flags,
            })  # fmt: skip
    return Pruefung(gueltig, fehler, warnungen)


def pruefe_csv(
    inhalt: bytes,
    stichtag: date,
    klassen: list[Klasse],
    vergeben: set[int] | None = None,
) -> tuple[list[dict], list[str]]:
    """Prüft eine CSV-Datei. Liefert (gültige Zeilen, Fehlerliste mit Zeilennummern der Datei)."""
    zeilen, nummern, kopffehler = csv_lesen(inhalt)
    if kopffehler:
        return [], kopffehler
    p = pruefe_zeilen(zeilen, stichtag, klassen, vergeben, nummern)
    fehler = [f"Zeile {nummern[m.index]}: {m.text}" for m in p.fehler]
    if not p.gueltig and not fehler:
        fehler.append("Datei enthält keine Teilnehmer")
    return p.gueltig, fehler


def vergebene_startnummern(session: Session, veranstaltung: Veranstaltung) -> set[int]:
    return set(session.scalars(
        select(Teilnehmer.startnummer).where(Teilnehmer.veranstaltung_id == veranstaltung.id)
    ))  # fmt: skip


def zeilen_importieren(session: Session, veranstaltung: Veranstaltung, zeilen: list[dict],
                       klassen: list[Klasse], ersetzen: bool = False) -> int:  # fmt: skip
    """Importiert geprüfte Textzeilen (z. B. aus der Vorschau) – alles oder nichts."""
    vergeben = set() if ersetzen else vergebene_startnummern(session, veranstaltung)
    p = pruefe_zeilen(zeilen, veranstaltung.stichtag_alter, klassen, vergeben)
    if p.fehler or not p.gueltig:
        raise ImportFehler(
            [f"Zeile {m.index + 1}: {m.text}" for m in p.fehler] or ["keine Teilnehmer"]
        )
    return _speichern(session, veranstaltung, p.gueltig, ersetzen)


def _speichern(
    session: Session, veranstaltung: Veranstaltung, zeilen: list[dict], ersetzen: bool
) -> int:
    try:
        if ersetzen:
            session.execute(
                delete(Teilnehmer).where(Teilnehmer.veranstaltung_id == veranstaltung.id)
            )
        session.add_all(Teilnehmer(veranstaltung_id=veranstaltung.id, **z) for z in zeilen)
        veranstaltung.letzter_import_ms = time.time_ns() // 1_000_000
        session.commit()
    except Exception:
        session.rollback()
        raise
    return len(zeilen)


def importiere_teilnehmer(
    session: Session,
    veranstaltung: Veranstaltung,
    inhalt: bytes,
    klassen: list[Klasse],
    ersetzen: bool = False,
) -> int:
    """Importiert alle Zeilen in einer Transaktion oder wirft ImportFehler (nichts importiert)."""
    vergeben = set() if ersetzen else vergebene_startnummern(session, veranstaltung)
    zeilen, fehler = pruefe_csv(inhalt, veranstaltung.stichtag_alter, klassen, vergeben)
    if fehler:
        raise ImportFehler(fehler)
    return _speichern(session, veranstaltung, zeilen, ersetzen)


def zeilen_als_csv(zeilen: list[dict]) -> bytes:
    """Entwurf als CSV (Spalten wie SPALTEN, Semikolon, UTF-8 mit BOM für Excel)."""
    puffer = io.StringIO()
    schreiber = csv.writer(puffer, delimiter=";", lineterminator="\r\n")
    schreiber.writerow([sp.name for sp in SPALTEN])
    for z in zeilen:
        schreiber.writerow([str(z.get(sp.name) or "") for sp in SPALTEN])
    return ("\ufeff" + puffer.getvalue()).encode("utf-8")
