"""Klassen- und Wertungssätze in der Datenbank (Phase 12).

- Beim ersten Start werden die Dateien aus config/ als Sätze angelegt (Standardsätze).
- Jede Veranstaltung nutzt einen Klassensatz (ohne Angabe: Standardsatz) und optional einen
  Wertungssatz. Sobald sie Ereignisse hat, werden beide als Kopie (JSON) eingefroren; spätere
  Änderungen an den Sätzen wirken dann nicht mehr auf sie.
- Import/Export als TOML (bisheriges Dateiformat) und CSV, Groß-/Kleinschreibung beim Import egal.
"""

import csv
import io
import json
import logging
import time
import tomllib
from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.einstellungen import STANDARD_KLASSEN_DATEI, STANDARD_WERTUNGEN
from app.klassen import (
    Klasse,
    KlassenConfigFehler,
    lade_klassen,
    luecken,
    pruefe_klassen,
)
from app.models import Ereignis, Klassensatz, SatzKlasse, SatzStufe, Veranstaltung, Wertungssatz
from app.wertung import (
    Stufe,
    Wertung,
    WertungsConfigFehler,
    lade_wertungen,
    pruefe_wertung,
    zehntel_km,
)

log = logging.getLogger(__name__)

STANDARD_KLASSENSATZ = "DRA 2026"
# Mitgelieferte Wertungsdateien → Name des Wertungssatzes (andere Dateien: Name aus der Datei)
WERTUNG_NAMEN = {
    "radsportabzeichen-a1-flach.toml": "DRA A.1 flach",
    "radsportabzeichen-a2-berg-strasse.toml": "DRA A.2 Berg Straße",
    "radsportabzeichen-a3-berg-gelaende.toml": "DRA A.3 Berg Gelände",
}


class SatzFehler(ValueError):
    pass


def jetzt_ms() -> int:
    return time.time_ns() // 1_000_000


# --- Umwandlung Satz ↔ Klasse/Wertung ----------------------------------------------


def klassen_von(satz: Klassensatz) -> list[Klasse]:
    return [Klasse(k.name, k.geschlecht, k.alter_von, k.alter_bis) for k in satz.klassen]


def wertung_von(satz: Wertungssatz) -> Wertung:
    stufen = {
        s.klasse: Stufe(s.klasse, s.gold_ms, s.silber_ms, s.bronze_ms,
                        zehntel_km(s.strecke_km) if s.strecke_km is not None else None)
        for s in satz.stufen
    }  # fmt: skip
    return Wertung(
        datei=satz.name,
        name=satz.beschreibung or satz.name,
        art=satz.art,
        stufen=stufen,
        strecke_min_zehntel_km=zehntel_km(satz.strecke_km_min) if satz.strecke_km_min else None,
        strecke_max_zehntel_km=zehntel_km(satz.strecke_km_max) if satz.strecke_km_max else None,
    )


def zeit_text(ms: int, art: str) -> str:
    """Schwellenwert als Text: Gesamtzeit H:MM:SS, Zeit pro km M:SS."""
    s = ms // 1000
    if art == "gesamtzeit":
        return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"
    return f"{s // 60}:{s % 60:02d}"


def _km_text(wert: float | None) -> str:
    return "" if wert is None else f"{wert:g}"


# --- Lesen ----------------------------------------------------------------------------


def klassensaetze(session: Session) -> list[Klassensatz]:
    return list(session.scalars(select(Klassensatz).order_by(Klassensatz.id)))


def wertungssaetze(session: Session) -> list[Wertungssatz]:
    return list(session.scalars(select(Wertungssatz).order_by(Wertungssatz.id)))


def nach_name(session: Session, modell, name: str):
    return session.scalars(
        select(modell).where(func.lower(modell.name) == name.strip().lower())
    ).first()


def standard_klassensatz(session: Session) -> Klassensatz:
    satz = session.scalars(select(Klassensatz).order_by(Klassensatz.id)).first()
    if satz is None:  # z. B. Datenbank ohne Startvorgang (Tests, Skripte): Standardsätze anlegen
        standardsaetze_anlegen(session)
        satz = session.scalars(select(Klassensatz).order_by(Klassensatz.id)).first()
    return satz


def benutzt_von(session: Session, satz) -> list[str]:
    if isinstance(satz, Klassensatz):
        veranstaltungen = session.scalars(
            select(Veranstaltung).where(Veranstaltung.klassensatz_id == satz.id)
        ).all()
        wertungen = session.scalars(
            select(Wertungssatz).where(Wertungssatz.klassensatz_id == satz.id)
        ).all()
        return [f"Veranstaltung „{v.name}“" for v in veranstaltungen] + [
            f"Wertungssatz „{w.name}“" for w in wertungen
        ]
    veranstaltungen = session.scalars(
        select(Veranstaltung).where(Veranstaltung.wertungssatz_id == satz.id)
    ).all()
    return [f"Veranstaltung „{v.name}“" for v in veranstaltungen]


# --- Validieren und Speichern -------------------------------------------------------


def _name_pruefen(session, modell, name: str, satz_id: int | None) -> str:
    name = (name or "").strip()
    if not name:
        raise SatzFehler("Name fehlt.")
    vorhanden = nach_name(session, modell, name)
    if vorhanden is not None and vorhanden.id != satz_id:
        raise SatzFehler(f"Name „{name}“ ist bereits vergeben.")
    return name[:100]


def _zahl(text, feld: str, wo: str) -> int:
    t = str(text if text is not None else "").strip()
    if not t.lstrip("-").isdigit():
        raise SatzFehler(f"{wo}: {feld} „{t}“ ist keine ganze Zahl.")
    return int(t)


def klassen_pruefen(zeilen: list[dict]) -> tuple[list[Klasse], list[str]]:
    """Zeilen (name, geschlecht, alter_von, alter_bis; Texte oder Zahlen) → Klassen, Warnungen."""
    roh = []
    for i, z in enumerate(zeilen, start=1):
        if not any(
            str(z.get(f, "") or "").strip()
            for f in ("name", "geschlecht", "alter_von", "alter_bis")
        ):
            continue  # leere Tabellenzeile
        wo = f"Zeile {i}"
        roh.append({
            "name": str(z.get("name") or "").strip(),
            "geschlecht": str(z.get("geschlecht") or "").strip().lower(),
            "alter_von": _zahl(z.get("alter_von"), "Alter von", wo),
            "alter_bis": _zahl(z.get("alter_bis"), "Alter bis", wo),
        })  # fmt: skip
        if not roh[-1]["name"]:
            raise SatzFehler(f"{wo}: Name der Klasse fehlt.")
    try:
        klassen = pruefe_klassen(roh)
    except KlassenConfigFehler as e:
        raise SatzFehler(str(e)) from e
    return klassen, luecken(klassen)


def klassensatz_speichern(session, satz: Klassensatz | None, name: str, zeilen: list[dict]):
    """Legt an oder ersetzt Name und Klassen. Gibt (Satz, Warnungen) zurück."""
    name = _name_pruefen(session, Klassensatz, name, satz.id if satz else None)
    klassen, warnungen = klassen_pruefen(zeilen)
    if satz is not None:
        fehlend = _fehlende_wertungsklassen(session, satz, {k.name for k in klassen})
        if fehlend:
            raise SatzFehler("Klassen werden von Wertungssätzen verwendet: " + "; ".join(fehlend))
    if satz is None:
        satz = Klassensatz(name=name)
        session.add(satz)
    satz.name = name
    satz.klassen = [
        SatzKlasse(position=i, name=k.name, geschlecht=k.geschlecht, alter_von=k.alter_von,
                   alter_bis=k.alter_bis)
        for i, k in enumerate(klassen)
    ]  # fmt: skip
    session.commit()
    return satz, warnungen


def _fehlende_wertungsklassen(session, satz: Klassensatz, namen: set[str]) -> list[str]:
    fehlend = []
    for w in session.scalars(select(Wertungssatz).where(Wertungssatz.klassensatz_id == satz.id)):
        rest = sorted({s.klasse for s in w.stufen} - namen)
        if rest:
            fehlend.append(f"„{w.name}“ braucht {', '.join(rest)}")
    return fehlend


def wertung_pruefen(name: str, art: str, smin, smax, zeilen: list[dict], klassensatz: Klassensatz):
    """Prüft einen Wertungssatz gegen seinen Klassensatz; Klassennamen ohne Groß-/Kleinschreibung."""
    art = (art or "").strip().lower()
    schreibweise = {k.name.lower(): k.name for k in klassensatz.klassen}
    stufen = []
    for z in zeilen:
        if not any(str(z.get(f, "") or "").strip() for f in ("klasse", "gold", "silber", "bronze")):
            continue
        klasse = str(z.get("klasse") or "").strip()
        stufe = {"klasse": schreibweise.get(klasse.lower(), klasse)}
        for f in ("gold", "silber", "bronze"):
            stufe[f] = str(z.get(f) or "").strip()
        km = str(z.get("strecke_km") or "").strip().replace(",", ".")
        if km:
            try:
                stufe["strecke_km"] = float(km)
            except ValueError as e:
                raise SatzFehler(f"Klasse {klasse}: Streckenlänge „{km}“ ungültig.") from e
        stufen.append(stufe)
    roh = {"name": name or "Wertung", "art": art, "stufe": stufen}
    for feld, wert in (("strecke_km_min", smin), ("strecke_km_max", smax)):
        t = str(wert if wert is not None else "").strip().replace(",", ".")
        if t:
            try:
                roh[feld] = float(t)
            except ValueError as e:
                raise SatzFehler(f"{feld} „{t}“ ungültig.") from e
    try:
        return pruefe_wertung(name or "Wertung", roh, klassen_von(klassensatz))
    except WertungsConfigFehler as e:
        raise SatzFehler(str(e).split(": ", 1)[-1]) from e


def wertungssatz_speichern(session, satz: Wertungssatz | None, name: str, art: str, smin, smax,
                           klassensatz_id: int, zeilen: list[dict], beschreibung: str | None = None):  # fmt: skip
    name = _name_pruefen(session, Wertungssatz, name, satz.id if satz else None)
    klassensatz = session.get(Klassensatz, int(klassensatz_id)) if klassensatz_id else None
    if klassensatz is None:
        raise SatzFehler("Klassensatz fehlt.")
    w = wertung_pruefen(name, art, smin, smax, zeilen, klassensatz)
    if satz is not None and satz.klassensatz_id != klassensatz.id:
        betroffen = session.scalars(select(Veranstaltung).where(
            Veranstaltung.wertungssatz_id == satz.id, Veranstaltung.regeln_json.is_(None),
            Veranstaltung.klassensatz_id != klassensatz.id)).all()  # fmt: skip
        if betroffen:
            raise SatzFehler("Klassensatz kann nicht gewechselt werden, Veranstaltungen nutzen diese Wertung "
                             "mit dem bisherigen Klassensatz: " + ", ".join(f"„{x.name}“" for x in betroffen))  # fmt: skip
    warnungen = []
    ohne = sorted({k.name for k in klassensatz.klassen} - set(w.stufen))
    if ohne:
        warnungen.append(f"Ohne Stufen (keine Wertung möglich): {', '.join(ohne)}")
    if satz is None:
        satz = Wertungssatz(name=name, art=w.art, klassensatz_id=klassensatz.id)
        session.add(satz)
    satz.name, satz.art, satz.klassensatz_id = name, w.art, klassensatz.id
    if beschreibung is not None:
        satz.beschreibung = beschreibung.strip()[:200] or None
    satz.strecke_km_min = w.strecke_min_zehntel_km / 10 if w.strecke_min_zehntel_km else None
    satz.strecke_km_max = w.strecke_max_zehntel_km / 10 if w.strecke_max_zehntel_km else None
    satz.stufen = [
        SatzStufe(position=i, klasse=s.klasse, gold_ms=s.gold_ms, silber_ms=s.silber_ms,
                  bronze_ms=s.bronze_ms,
                  strecke_km=s.strecke_zehntel_km / 10 if s.strecke_zehntel_km else None)
        for i, s in enumerate(w.stufen.values())
    ]  # fmt: skip
    session.commit()
    return satz, warnungen


def _freier_name(session, modell, basis: str) -> str:
    name, n = f"Kopie von {basis}", 2
    while nach_name(session, modell, name) is not None:
        name, n = f"Kopie {n} von {basis}", n + 1
    return name[:100]


def kopieren(session: Session, satz):
    if isinstance(satz, Klassensatz):
        neu, _ = klassensatz_speichern(session, None, _freier_name(session, Klassensatz, satz.name),
                                       klassen_zeilen(satz))  # fmt: skip
        return neu
    neu, _ = wertungssatz_speichern(
        session, None, _freier_name(session, Wertungssatz, satz.name), satz.art,
        satz.strecke_km_min, satz.strecke_km_max, satz.klassensatz_id, wertung_zeilen(satz),
        beschreibung=satz.beschreibung,
    )  # fmt: skip
    return neu


def loeschen(session: Session, satz) -> None:
    benutzt = benutzt_von(session, satz)
    if benutzt:
        raise SatzFehler(f"„{satz.name}“ wird noch verwendet: " + ", ".join(benutzt))
    session.delete(satz)
    session.commit()


def klassen_zeilen(satz: Klassensatz) -> list[dict]:
    return [{"name": k.name, "geschlecht": k.geschlecht, "alter_von": k.alter_von,
             "alter_bis": k.alter_bis} for k in satz.klassen]  # fmt: skip


def wertung_zeilen(satz: Wertungssatz) -> list[dict]:
    return [{"klasse": s.klasse, "strecke_km": _km_text(s.strecke_km),
             "gold": zeit_text(s.gold_ms, satz.art), "silber": zeit_text(s.silber_ms, satz.art),
             "bronze": zeit_text(s.bronze_ms, satz.art)} for s in satz.stufen]  # fmt: skip


# --- Import/Export --------------------------------------------------------------------


def _toml_text(wert: str) -> str:
    return json.dumps(wert, ensure_ascii=False)  # JSON-Strings sind gültige TOML-Strings


def _klein(daten):
    """Schlüssel (rekursiv) kleinschreiben: Groß-/Kleinschreibung beim Import egal."""
    if isinstance(daten, dict):
        return {str(k).strip().lower(): _klein(v) for k, v in daten.items()}
    if isinstance(daten, list):
        return [_klein(v) for v in daten]
    return daten


def _dekodieren(inhalt: bytes) -> str:
    try:
        return inhalt.decode("utf-8-sig")
    except UnicodeDecodeError:
        return inhalt.decode("cp1252")


def _csv_zeilen(inhalt: bytes) -> list[dict]:
    text = _dekodieren(inhalt)
    kopf = text.splitlines()[0] if text.strip() else ""
    trenner = ";" if kopf.count(";") >= kopf.count(",") else ","
    leser = csv.DictReader(io.StringIO(text), delimiter=trenner)
    return [{str(k or "").strip().lower(): (v or "").strip() for k, v in z.items()} for z in leser]


def _csv(zeilen: list[list]) -> bytes:
    puffer = io.StringIO()
    csv.writer(puffer, delimiter=";", lineterminator="\r\n").writerows(zeilen)
    return ("﻿" + puffer.getvalue()).encode("utf-8")


def klassen_toml(satz: Klassensatz) -> str:
    teile = [f"# Klassensatz „{satz.name}“ – TapTrial", "# Alter = Wettkampfjahr - Geburtsjahr. Grenzen inklusive.",
             f"name = {_toml_text(satz.name)}", ""]  # fmt: skip
    for k in satz.klassen:
        teile += ["[[klasse]]", f"name = {_toml_text(k.name)}", f'geschlecht = "{k.geschlecht}"',
                  f"alter_von = {k.alter_von}", f"alter_bis = {k.alter_bis}", ""]  # fmt: skip
    return "\n".join(teile)


def klassen_csv(satz: Klassensatz) -> bytes:
    return _csv([["name", "geschlecht", "alter_von", "alter_bis"]]
                + [[k.name, k.geschlecht, k.alter_von, k.alter_bis] for k in satz.klassen])  # fmt: skip


def klassen_import(inhalt: bytes, dateiname: str) -> tuple[str | None, list[dict]]:
    """TOML (bisheriges Format) oder CSV → (Name aus der Datei oder None, Zeilen)."""
    if dateiname.lower().endswith(".csv"):
        return None, _csv_zeilen(inhalt)
    try:
        daten = _klein(tomllib.loads(_dekodieren(inhalt)))
    except tomllib.TOMLDecodeError as e:
        raise SatzFehler(f"TOML-Datei nicht lesbar: {e}") from e
    return daten.get("name"), list(daten.get("klasse") or [])


def wertung_toml(satz: Wertungssatz) -> str:
    teile = [f"# Wertungssatz „{satz.name}“ – TapTrial",
             "# Zeiten: art = gesamtzeit als H:MM:SS, art = pro_km als M:SS je km.",
             f"name = {_toml_text(satz.beschreibung or satz.name)}", f'art = "{satz.art}"']  # fmt: skip
    if satz.strecke_km_min is not None:
        teile += [
            f"strecke_km_min = {satz.strecke_km_min:g}",
            f"strecke_km_max = {satz.strecke_km_max:g}",
        ]
    teile.append("")
    for z in wertung_zeilen(satz):
        teile += ["[[stufe]]", f"klasse = {_toml_text(z['klasse'])}"]
        if z["strecke_km"]:
            teile.append(f"strecke_km = {z['strecke_km']}")
        teile += [
            f'bronze = "{z["bronze"]}"',
            f'silber = "{z["silber"]}"',
            f'gold   = "{z["gold"]}"',
            "",
        ]
    return "\n".join(teile)


def wertung_csv(satz: Wertungssatz) -> bytes:
    zeilen = [["klasse", "strecke_km", "gold", "silber", "bronze"]]
    zeilen += [[z["klasse"], z["strecke_km"], z["gold"], z["silber"], z["bronze"]]
               for z in wertung_zeilen(satz)]  # fmt: skip
    return _csv(zeilen)


def wertung_import(inhalt: bytes, dateiname: str) -> dict:
    """TOML (bisheriges Format) oder CSV (nur Stufen) → Felder für wertungssatz_speichern."""
    if dateiname.lower().endswith(".csv"):
        return {"zeilen": _csv_zeilen(inhalt)}
    try:
        daten = _klein(tomllib.loads(_dekodieren(inhalt)))
    except tomllib.TOMLDecodeError as e:
        raise SatzFehler(f"TOML-Datei nicht lesbar: {e}") from e
    zeilen = [
        {k: (str(v) if v is not None else "") for k, v in s.items()}
        for s in daten.get("stufe") or []
    ]
    return {
        "beschreibung": daten.get("name"),
        "art": str(daten.get("art") or "").lower(),
        "strecke_km_min": daten.get("strecke_km_min"),
        "strecke_km_max": daten.get("strecke_km_max"),
        "zeilen": zeilen,
    }


# --- Standardsätze und Migration -------------------------------------------------------


def standardsaetze_anlegen(session: Session, klassen_datei: str = STANDARD_KLASSEN_DATEI,
                           wertungen_verzeichnis: str = STANDARD_WERTUNGEN) -> None:  # fmt: skip
    """Beim ersten Start: Dateien aus config/ als Sätze anlegen (ungültige Datei → Fehler)."""
    if session.scalars(select(Klassensatz)).first() is not None:
        return
    klassen = lade_klassen(klassen_datei)
    satz = Klassensatz(name=STANDARD_KLASSENSATZ, klassen=[
        SatzKlasse(position=i, name=k.name, geschlecht=k.geschlecht, alter_von=k.alter_von,
                   alter_bis=k.alter_bis) for i, k in enumerate(klassen)])  # fmt: skip
    session.add(satz)
    session.flush()
    for datei, w in lade_wertungen(wertungen_verzeichnis, klassen).items():
        session.add(Wertungssatz(
            name=WERTUNG_NAMEN.get(datei, w.name[:100]), beschreibung=w.name, art=w.art,
            strecke_km_min=w.strecke_min_zehntel_km / 10 if w.strecke_min_zehntel_km else None,
            strecke_km_max=w.strecke_max_zehntel_km / 10 if w.strecke_max_zehntel_km else None,
            klassensatz_id=satz.id,
            stufen=[SatzStufe(position=i, klasse=s.klasse, gold_ms=s.gold_ms, silber_ms=s.silber_ms,
                              bronze_ms=s.bronze_ms,
                              strecke_km=s.strecke_zehntel_km / 10 if s.strecke_zehntel_km else None)
                    for i, s in enumerate(w.stufen.values())],
        ))  # fmt: skip
    session.commit()


def veranstaltungen_zuordnen(session: Session) -> None:
    """Migration: Veranstaltungen ohne Klassensatz bekommen den Standardsatz, eine bisherige
    Wertungsdatei den passenden Wertungssatz; Veranstaltungen mit Ereignissen werden eingefroren."""
    standard = standard_klassensatz(session)
    for v in session.scalars(select(Veranstaltung).where(or_(
            Veranstaltung.klassensatz_id.is_(None),
            Veranstaltung.wertungssatz_id.is_(None) & Veranstaltung.wertung.is_not(None),
            Veranstaltung.regeln_json.is_(None)))):  # fmt: skip
        if v.klassensatz_id is None:
            v.klassensatz_id = standard.id
        if v.wertung and v.wertungssatz_id is None:
            w = nach_name(session, Wertungssatz, WERTUNG_NAMEN.get(v.wertung, v.wertung))
            if w is not None:
                v.wertungssatz_id = w.id
        session.flush()
        einfrieren_wenn_ereignisse(session, v)
    session.commit()


# --- Regeln je Veranstaltung, Einfrieren -------------------------------------------


@dataclass
class Regeln:
    klassensatz: str
    klassen: list[Klasse]
    wertungssatz: str | None
    wertung: Wertung | None
    eingefroren_ms: int | None = None


def _wertung_dict(w: Wertung | None) -> dict | None:
    if w is None:
        return None
    return {"datei": w.datei, "name": w.name, "art": w.art, "min": w.strecke_min_zehntel_km,
            "max": w.strecke_max_zehntel_km,
            "stufen": [[s.klasse, s.gold_ms, s.silber_ms, s.bronze_ms, s.strecke_zehntel_km]
                       for s in w.stufen.values()]}  # fmt: skip


def _wertung_aus(d: dict | None) -> Wertung | None:
    if not d:
        return None
    stufen = {s[0]: Stufe(s[0], s[1], s[2], s[3], s[4]) for s in d["stufen"]}
    return Wertung(d["datei"], d["name"], d["art"], stufen, d["min"], d["max"])


def _live(session: Session, v: Veranstaltung) -> Regeln:
    ks = session.get(Klassensatz, v.klassensatz_id) if v.klassensatz_id else None
    ks = ks or standard_klassensatz(session)
    ws = session.get(Wertungssatz, v.wertungssatz_id) if v.wertungssatz_id else None
    if ws is not None and (
        ws.klassensatz_id != ks.id or wertung_pruefen_gegen(ws, klassen_von(ks))[0]
    ):
        # Prüfung beim Einfrieren: eine unpassende Wertung wird nicht übernommen (Sync darf nicht scheitern)
        log.warning("Wertung „%s“ passt nicht zum Klassensatz „%s“ – wird nicht angewendet",
                    ws.name, ks.name)  # fmt: skip
        ws = None
    return Regeln(
        ks.name, klassen_von(ks), ws.name if ws else None, wertung_von(ws) if ws else None
    )


def regeln(session: Session, v: Veranstaltung) -> Regeln:
    """Gültige Klassen und Wertung einer Veranstaltung: eingefrorene Kopie oder aktuelle Sätze."""
    if v.regeln_json:
        d = json.loads(v.regeln_json)
        return Regeln(d["klassensatz"], [Klasse(*k) for k in d["klassen"]], d["wertungssatz"],
                      _wertung_aus(d["wertung"]), v.eingefroren_ms)  # fmt: skip
    return _live(session, v)


def _speichern_json(v: Veranstaltung, r: Regeln) -> None:
    v.regeln_json = json.dumps({
        "klassensatz": r.klassensatz,
        "klassen": [[k.name, k.geschlecht, k.alter_von, k.alter_bis] for k in r.klassen],
        "wertungssatz": r.wertungssatz, "wertung": _wertung_dict(r.wertung),
    }, ensure_ascii=False)  # fmt: skip


def einfrieren(session: Session, v: Veranstaltung) -> None:
    """Kopie der aktuellen Sätze in der Veranstaltung ablegen (ohne commit)."""
    if v.regeln_json:
        return
    _speichern_json(v, _live(session, v))
    v.eingefroren_ms = jetzt_ms()


def einfrieren_wenn_ereignisse(session: Session, v: Veranstaltung) -> None:
    if v.regeln_json:
        return
    hat = session.scalar(
        select(func.count()).select_from(Ereignis).where(Ereignis.veranstaltung_id == v.id)
    )
    if hat:
        einfrieren(session, v)


def wertung_pruefen_gegen(ws: Wertungssatz, klassen: list[Klasse]) -> tuple[list[str], list[str]]:
    """(Fehler, Hinweise): Wertungsklasse fehlt im Klassensatz = Fehler; Klasse ohne Eintrag = Hinweis."""
    namen = {k.name for k in klassen}
    stufen = {s.klasse for s in ws.stufen}
    fehlend = sorted(stufen - namen)
    fehler = (
        [f"Wertung „{ws.name}“ enthält Klassen, die im Klassensatz fehlen: {', '.join(fehlend)}"]
        if fehlend
        else []
    )
    ohne = [k.name for k in klassen if k.name not in stufen]
    hinweise = [f"Ohne Wertungseintrag (keine Stufe möglich): {', '.join(ohne)}"] if ohne else []
    return fehler, hinweise


def passende_wertungssaetze(session: Session, klassensatz_id: int) -> list[Wertungssatz]:
    return list(session.scalars(
        select(Wertungssatz).where(Wertungssatz.klassensatz_id == klassensatz_id).order_by(Wertungssatz.id)
    ))  # fmt: skip


def regeln_setzen(session: Session, v: Veranstaltung, klassensatz_id: int | None,
                  wertungssatz_id: int | None, strecke_km: float | None,
                  abschliessen: bool = True, laengen_verwerfen: bool = False) -> list[str]:  # fmt: skip
    """Klassensatz (Pflicht), Wertungssatz (None = aus) und Streckenlänge. Gibt Hinweise zurück.

    abschliessen=False: nur flush, ohne Prüfung der Streckenlängen – der Aufrufer speichert danach
    die Strecken aus dem Formular (prüft dabei die Längen) und committet. laengen_verwerfen ist ohne
    Wirkung (Segmente 49: Strecken werden nicht mehr neu vorgeschlagen, nichts geht verloren).

    Ein Wertungssatz gehört fest zu einem Klassensatz. Wird der Klassensatz gewechselt (nicht
    eingefroren), setzt das eine nicht mehr passende Wertung mit Hinweis zurück; eine ausdrücklich
    gewählte unpassende Wertung ist ein Fehler.
    """
    if v.zustand == "anonymisiert":
        raise SatzFehler("Veranstaltung ist anonymisiert.")
    ks_id = klassensatz_id or v.klassensatz_id or standard_klassensatz(session).id
    ks = session.get(Klassensatz, ks_id)
    if ks is None:
        raise SatzFehler("Bitte einen Klassensatz wählen.")
    ws = session.get(Wertungssatz, wertungssatz_id) if wertungssatz_id else None
    if wertungssatz_id and ws is None:
        raise SatzFehler("Wertungssatz nicht gefunden.")
    if v.regeln_json and v.klassensatz_id is not None and ks.id != v.klassensatz_id:
        raise SatzFehler(
            "Klassensatz ist eingefroren (es gibt bereits Zeiten) und kann nicht mehr geändert werden."
        )
    hinweise: list[str] = []
    klassensatz_gewechselt = v.klassensatz_id is not None and ks.id != v.klassensatz_id
    if ws is not None and ws.klassensatz_id != ks.id:
        if klassensatz_gewechselt and ws.id == v.wertungssatz_id:
            hinweise.append(
                f"Wertung „{ws.name}“ gehört nicht zum Klassensatz „{ks.name}“ und wurde zurückgesetzt (keine Wertung)."
            )
            ws = None
        else:
            raise SatzFehler(f"Wertungssatz „{ws.name}“ passt nicht zum Klassensatz „{ks.name}“.")
    klassen = regeln(session, v).klassen if v.regeln_json else klassen_von(ks)
    if ws is not None:
        fehler, mehr = wertung_pruefen_gegen(ws, klassen)
        if fehler:
            raise SatzFehler(fehler[0])
        hinweise += mehr
    from app import strecken  # spät importiert: strecken nutzt dieses Modul

    wertung_neu = (ws.id if ws else None) != v.wertungssatz_id
    try:
        strecken.sicherstellen(session, v)
        v.klassensatz_id, v.wertungssatz_id = ks.id, ws.id if ws else None
        if v.regeln_json and wertung_neu:
            # bewusste Neuwahl der Wertung durch den Admin: neue Kopie einfrieren, Klassen bleiben
            r = regeln(session, v)
            r.wertungssatz, r.wertung = (ws.name, wertung_von(ws)) if ws else (None, None)
            _speichern_json(v, r)
        elif not v.regeln_json and (klassensatz_gewechselt or wertung_neu):
            # Segmente 49: keine Strecken still neu anlegen – Strecken bleiben, die Zuordnung passt
            # sich an; im Assistenten entscheidet der Admin je Längen-Platz
            hinweise += strecken.zuordnung_anpassen(session, v)
        if strecke_km is not None:
            strecken.laenge_setzen(session, v, strecke_km)
        if ws is not None and abschliessen:
            strecken.wertung_laengen_pruefen(session, v)
    except SatzFehler:
        session.rollback()
        raise
    v.strecke_km = None  # seit Phase 13 gelten die Längen der Strecken
    if abschliessen:
        session.commit()
    else:
        session.flush()
    return hinweise


def strecke_parsen(text: str | None) -> float | None:
    text = (text or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        wert = round(float(text), 1)
    except ValueError as e:
        raise SatzFehler(f"Streckenlänge „{text}“ ungültig.") from e
    if not 0 < wert < 1000:
        raise SatzFehler("Streckenlänge muss größer als 0 sein.")
    return wert
