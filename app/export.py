"""CSV-Exporte: Ergebnisliste und Protokoll (Excel-tauglich: UTF-8 mit BOM, Semikolon)."""

import csv
import io

from app.auswertung import (
    Ergebnis,
    ErgebnisZeile,
    Status,
    format_zehntel,
    gueltige_ereignisse,
    korrektur_text,
    zehntel,
)
from app.korrekturen import datum_text, uhrzeit_text
from app.models import Ereignis, Veranstaltung
from app.wertung import STUFE_TEXT

STATUS_TEXT = {
    Status.OK: "",
    Status.DNS: "DNS",
    Status.DNF: "DNF",
    Status.DSQ: "DSQ",
    Status.UNVOLLSTAENDIG: "unvollständig",
    Status.KONFLIKT: "Konflikt",
    Status.OFFEN: "offen",
}


def ergebnis_kopf(v: Veranstaltung, erg: Ergebnis) -> str:
    if erg.offiziell:
        return f"Offizielles Ergebnis, Stand {uhrzeit_text(erg.offiziell_seit_ms)[:5]}"
    return "Vorläufiges Ergebnis"


def segment_spalten(erg: Ergebnis) -> list[tuple[int, int, str]]:
    """Spalten je sichtbarem Auswertungsabschnitt und Durchfahrt (Segmente sind
    automatisch Abschnitte): (Strecke, Index in abschnittszeiten, Überschrift). Nutzen mehrere
    Strecken Segmente, steht der Streckenname davor."""
    from app.segmente import abschnitt_labels

    mit = [i for i, s in enumerate(erg.strecken) if s.mit_durchfahrten]
    return [(i, j, label if len(mit) == 1 else f"{erg.strecken[i].name}: {label}")
            for i in mit
            for j, label in enumerate(abschnitt_labels(erg.strecken[i].sichtbare_abschnitte, erg.strecken[i].teile))]  # fmt: skip


def segment_text(z: ErgebnisZeile, strecke: int, j: int) -> str:
    """Abschnittszeit des Fahrers in Spalte (strecke, j); leer, wenn nicht vorhanden."""
    if (
        z.strecke != strecke
        or j >= len(z.abschnittszeiten)
        or z.abschnittszeiten[j].zeit_ms is None
    ):
        return ""
    return format_zehntel(zehntel(z.abschnittszeiten[j].zeit_ms))


def _km(zehntel: int | None) -> str:
    return f"{zehntel / 10:.1f} km".replace(".", ",") if zehntel else ""


def _csv(zeilen: list[list]) -> bytes:
    puffer = io.StringIO()
    schreiber = csv.writer(puffer, delimiter=";", lineterminator="\r\n")
    schreiber.writerows(zeilen)
    return ("﻿" + puffer.getvalue()).encode("utf-8")


def status_text(z: ErgebnisZeile) -> str:
    """Statusspalte: „a. K.“ für außer Konkurrenz, sonst wie STATUS_TEXT."""
    if z.ausser_konkurrenz and z.status == Status.OK:
        return "a. K."
    return STATUS_TEXT[z.status]


def stufe_text(z: ErgebnisZeile) -> str:
    """Abzeichen mit Kennzeichnung „unter Vorbehalt“ (vorläufiger DSQ) bzw. „manuell“."""
    if not z.stufe:
        return "–"  # kein Abzeichen gewünscht oder nicht erreicht
    text = STUFE_TEXT[z.stufe]
    if z.dsq_vorlaeufig:
        return f"{text} (unter Vorbehalt)"
    if z.manuell:
        return f"{text} (manuell)"
    return text


def ergebnis_csv(v: Veranstaltung, erg: Ergebnis, oeffentlich: bool = False) -> bytes:
    """Ergebnisliste. Öffentlich ohne Geschlecht, nur Fahrer mit Ergebnis oder Status."""
    mehrere = erg.mehrere_strecken  # Spalte „Strecke“, Ränge je Strecke
    vorne = ["Strecke"] if mehrere else []
    if oeffentlich:
        kopf = vorne + ["Rang gesamt", "Rang Klasse", "Startnummer", "Name", "Verein", "Klasse",
                        "Fahrzeit", "Status"]  # fmt: skip
    else:
        kopf = vorne + ["Rang gesamt", "Rang Klasse", "Startnummer", "Vorname", "Nachname",
                        "Verein", "Geschlecht", "Klasse", "Fahrzeit", "Status", "Manuell"]  # fmt: skip
    if erg.wertung:
        kopf.append("Abzeichen")
    zwischen = erg.mit_durchfahrten and (not oeffentlich or bool(v.zwischenzeiten_oeffentlich))
    spalten = segment_spalten(erg) if zwischen else []
    kopf += [titel for _, _, titel in spalten]
    kopf.append("Bemerkung")
    titel = [f"{v.name} – {v.datum:%d.%m.%Y}", ergebnis_kopf(v, erg)]
    if erg.wertung:
        laengen = [_km(s.zehntel_km) for s in erg.strecken]
        if mehrere:
            text = "; ".join(
                f"{s.name}: {km or '–'}" for s, km in zip(erg.strecken, laengen, strict=True)
            )
        else:
            text = laengen[0]
        titel.append(f"Leistungswertung: {erg.wertung.name}, {text}")
    zeilen = [titel, [], kopf]
    for z in sorted(erg.zeilen, key=lambda z: z.strecke):  # stabil: Reihenfolge je Strecke bleibt
        if oeffentlich and z.status in (Status.OFFEN, Status.UNVOLLSTAENDIG):
            continue
        if oeffentlich:
            bemerkung = "DSQ vorläufig, unter Vorbehalt" if z.dsq_vorlaeufig else ""
            zeile = [z.rang_gesamt or "", z.rang_klasse or "", z.startnummer,
                     f"{z.vorname} {z.nachname}".strip(), z.verein or "", z.klasse or "",
                     z.fahrzeit_text, status_text(z)]  # fmt: skip
        else:
            bemerkung = "; ".join(
                (
                    [f"DSQ vorläufig, unter Vorbehalt: {z.dsq_vorlaeufig}"]
                    if z.dsq_vorlaeufig
                    else []
                )
                + (
                    [f"Zielzeit korrigiert ({korrektur_text(z.korrektur_ms)})"]
                    if z.korrigiert
                    else []
                )
            )
            zeile = [z.rang_gesamt or "", z.rang_klasse or "", z.startnummer, z.vorname,
                     z.nachname, z.verein or "", z.geschlecht, z.klasse or "", z.fahrzeit_text,
                     status_text(z), "ja" if z.manuell else ""]  # fmt: skip
        if mehrere:
            zeile.insert(0, erg.strecken[z.strecke].name)
        if erg.wertung:
            zeile.append(stufe_text(z))
        zeile += [segment_text(z, i, j) for i, j, _ in spalten]
        zeile.append(bemerkung)
        zeilen.append(zeile)
    return _csv(zeilen)


def rangliste_csv(v: Veranstaltung, erg: Ergebnis, r) -> bytes:
    """Rangliste eines Abschnitts als CSV."""
    titel = [f"{v.name} – {v.datum:%d.%m.%Y}", ergebnis_kopf(v, erg), f"Rangliste {r.titel}"]
    kopf = ["Rang", "Startnummer", "Name", "Verein", "Klasse", "Zeit", "Durchfahrt", "Bemerkung"]
    zeilen = [titel, [], kopf]
    for z in r.zeilen:
        bemerkung = "; ".join((["außer Konkurrenz"] if z.ausser_konkurrenz else [])
                              + ([z.status] if z.status not in ("OK",) else []))  # fmt: skip
        zeilen.append(
            [
                z.rang or "",
                z.startnummer,
                z.name,
                z.verein,
                z.klasse,
                z.zeit,
                z.durchfahrt,
                bemerkung,
            ]
        )
    return _csv(zeilen)


def quelle_text(e: Ereignis) -> str:
    """Herkunft für das Protokoll, z. B. „aus Datei übernommen 10.10.2026 12:30:00,0“."""
    if getattr(e, "quelle", None) != "datei":
        return ""
    return f"aus Datei übernommen {datum_text(e.empfangen_ms)} {uhrzeit_text(e.empfangen_ms)}"


def protokoll_csv(v: Veranstaltung, ereignisse: list[Ereignis]) -> bytes:
    gueltig = {e.uuid for e in gueltige_ereignisse(ereignisse)}
    kopf = [
        "Erfasst (Gerät)", "Empfangen (Server)", "Art", "Startnummer", "Zeit", "Wert",
        "Bezug", "Gerät", "Notiz", "Storniert", "UUID", "Quelle",
    ]  # fmt: skip
    zeilen = [kopf]
    for e in sorted(ereignisse, key=lambda e: (e.erstellt_ms, e.empfangen_ms, e.uuid)):
        zeilen.append(
            [
                uhrzeit_text(e.erstellt_ms), uhrzeit_text(e.empfangen_ms), e.art,
                e.startnummer if e.startnummer is not None else "",
                uhrzeit_text(e.zeit_ms) if e.zeit_ms is not None else "",
                korrektur_text(e.delta_ms) if getattr(e, "delta_ms", None) else e.wert or "",
                e.bezug_uuid or "", e.geraet, e.notiz or "",
                "" if e.uuid in gueltig else "ja", e.uuid, quelle_text(e),
            ]
        )  # fmt: skip
    return _csv(zeilen)
