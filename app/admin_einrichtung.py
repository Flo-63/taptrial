"""Admin: geführte Einrichtung einer neuen Veranstaltung (Assistent, Feedback phase-12).

1 Name/Datum · 2 Klassen & Wertung · 3 Teilnehmer · 4 Zielmonitor (Segmente 66) · 5 Zusammenfassung,
Aktivieren, Gerätelinks.
Jeder Schritt speichert sofort (Zwischenstand); `einrichtung_schritt` merkt den nächsten offenen
Schritt, bis die Einrichtung abgeschlossen ist.
"""

import json
from datetime import date
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app import admin_auth, saetze, strecken
from app import korrekturen as k
from app.admin import (
    AdminDep,
    _seite,
    _veranstaltung,
    _wertung_an,
    _zurueck,
    geraetelinks,
    regeln_aus_formular,
    strecken_kontext,
)
from app.auth import SessionDep
from app.models import ANONYMISIEREN_NACH_MONATEN, ImportEntwurf, plus_monate

router = APIRouter(prefix="/admin/einrichtung", include_in_schema=False)
SCHRITTE = ("Name und Datum", "Klassen & Wertung", "Teilnehmer", "Zielmonitor", "Zusammenfassung")


def _datum(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError) as e:
        raise k.KorrekturFehler("Datum ungültig.") from e


def _weiter(session, v, schritt: int) -> None:
    if v.einrichtung_schritt is not None:
        v.einrichtung_schritt = max(v.einrichtung_schritt, schritt)
        session.commit()


def _seite_schritt(request, session, v, schritt: int, **mehr) -> HTMLResponse:
    kontext = {
        "v": v,
        "schritt": schritt,
        "schritte": SCHRITTE,
        "erreicht": (v.einrichtung_schritt or len(SCHRITTE) + 1) if v else 1,
    }
    if v is not None and schritt == 2:
        kontext.update(regeln=saetze.regeln(session, v), klassensaetze=saetze.klassensaetze(session),
                       wertungssaetze=saetze.wertungssaetze(session), **strecken_kontext(session, v))  # fmt: skip
        if v.einrichtung_schritt is not None:  # Segmente 49: je Längen-Platz entscheiden
            plaetze = strecken.plaetze(session, v)
            kontext.update(plaetze=plaetze, plaetze_offen=[p for p in plaetze if p.offen])
    if (
        v is not None and schritt == 4
    ):  # Segmente 66: gleiche Bausteine wie der Reiter, mit Vorgaben
        from app import monitor_konfig as mk

        liste = strecken.strecken_von(session, v)
        konf = mk.laden(v, liste) if v.monitor_json else mk.vorschlag(liste)
        kontext.update(mk.kontext(v, liste, k.ergebnis(session, v).wertung is not None,
                                  f"/admin/einrichtung/{v.id}/4", konf))  # fmt: skip
    if v is not None and schritt in (3, 5):
        kontext.update(
            teilnehmer=k.teilnehmer_von(session, v), wertung_an=_wertung_an(session, v),
            entwurf=session.scalars(select(ImportEntwurf).where(ImportEntwurf.veranstaltung_id == v.id)).first(),
        )  # fmt: skip
    if v is not None and schritt == 5:
        kontext.update(regeln=saetze.regeln(session, v), links=geraetelinks(request, v),
                       **strecken_kontext(session, v))  # fmt: skip
    return _seite(request, "admin_einrichtung.html", **kontext, **mehr)


@router.get("/neu", response_class=HTMLResponse)
def neu(request: Request, session: SessionDep, _a: AdminDep):
    return _seite_schritt(request, session, None, 1, heute=k.heute().isoformat())


@router.post("/neu")
def neu_anlegen(request: Request, session: SessionDep, _a: AdminDep, csrf: Annotated[str, Form()] = "",
                name: Annotated[str, Form()] = "", datum: Annotated[str, Form()] = ""):  # fmt: skip
    admin_auth.csrf_pruefen(request, csrf)
    try:
        v = k.veranstaltung_anlegen(session, name, _datum(datum), aktivieren=False)
    except k.KorrekturFehler as e:
        return _zurueck("/admin/einrichtung/neu", fehler=str(e))
    v.einrichtung_schritt = 2
    # Segmente 49/59: Strecken entstehen erst durch die Wahl je Platz; die vorläufige wird gemerkt
    vorlaeufig = strecken.strecken_von(session, v)[0].id
    v.einrichtung_json = json.dumps({"plaetze": {}, "vorlaeufig": vorlaeufig})
    session.commit()
    return _zurueck(f"/admin/einrichtung/{v.id}/2", ok=f"„{v.name}“ angelegt (noch nicht aktiv).")


@router.get("/{vid}/{schritt}", response_class=HTMLResponse)
def schritt_anzeigen(request: Request, vid: int, schritt: int, session: SessionDep, _a: AdminDep):
    if schritt not in range(1, len(SCHRITTE) + 1):
        raise HTTPException(404)
    return _seite_schritt(request, session, _veranstaltung(session, vid), schritt)


@router.post("/{vid}/1")
def schritt1(request: Request, vid: int, session: SessionDep, _a: AdminDep, csrf: Annotated[str, Form()] = "",
             name: Annotated[str, Form()] = "", datum: Annotated[str, Form()] = ""):  # fmt: skip
    admin_auth.csrf_pruefen(request, csrf)
    v = _veranstaltung(session, vid)
    try:
        neues_datum = _datum(datum)
        k.stammdaten_speichern(session, v, name, neues_datum,
                               plus_monate(neues_datum, ANONYMISIEREN_NACH_MONATEN))  # fmt: skip
    except k.KorrekturFehler as e:
        return _zurueck(f"/admin/einrichtung/{vid}/1", fehler=str(e))
    _weiter(session, v, 2)
    return _zurueck(f"/admin/einrichtung/{vid}/2")


@router.post("/{vid}/2")
async def schritt2(request: Request, vid: int, session: SessionDep, _a: AdminDep):
    form = await request.form()
    admin_auth.csrf_pruefen(request, str(form.get("csrf") or ""))
    v = _veranstaltung(session, vid)
    try:
        hinweise, vorgeschlagen = regeln_aus_formular(session, v, form)
    except saetze.SatzFehler as e:
        session.rollback()
        return _zurueck(f"/admin/einrichtung/{vid}/2", fehler=str(e))
    feld = lambda name: str(form.get(name) or "")
    ziel2 = f"/admin/einrichtung/{vid}/2"
    if feld(
        "platz_anlegen"
    ):  # Segmente 49: „Platzhalter-Strecke anlegen“ (Schritt ist gespeichert)
        schluessel = feld("platz_anlegen")
        try:
            s, mehr = strecken.platzhalter_anlegen(session, v, schluessel, feld(f"platz_name:{schluessel}"),
                                                   feld(f"platz_laenge:{schluessel}"))  # fmt: skip
            hinweise += mehr
        except saetze.SatzFehler as e:
            session.rollback()
            return _zurueck(ziel2, fehler=str(e))
        session.commit()
        return _zurueck(
            ziel2,
            ok=f"Platzhalter-Strecke „{s.name}“ angelegt.",
            warnung="; ".join(hinweise) or None,
        )
    if feld(
        "platz_laden"
    ):  # „Aus Datei laden“: erst speichern, dann laden, danach zurück (Segmente 50)
        session.commit()
        return _zurueck(
            f"/admin/v/{vid}/strecken/laden?platz={quote(feld('platz_laden'))}&zurueck={ziel2}"
        )
    # Segmente 23: „einfach“ gewählt → Segmente dieser Strecke entfernen; „Messpunkte einrichten“ → Strecken-Seite
    feld = lambda name: str(form.get(name) or "")
    for i in range(int(feld("strecken_anzahl") or 0)):
        s = next(
            (x for x in strecken.strecken_von(session, v) if str(x.id) == feld(f"strecke_id_{i}")),
            None,
        )
        if (
            s is not None
            and feld(f"strecke_art_{i}") == "einfach"
            and s.teile_json
            and not v.regeln_json
        ):
            if not feld(f"strecke_art_bestaetigt_{i}"):  # Segmente 30: nur nach Bestätigungsdialog
                hinweise.append(f"Strecke „{s.name}“ behält Verlauf und Abschnitte – Wechsel auf "
                                "„einfach“ nicht bestätigt.")  # fmt: skip
                continue
            s.teile_json = s.abschnitte_json = None
            hinweise.append(
                f"Strecke „{s.name}“: einfach (nur Start und Ziel) – Segmente entfernt."
            )
    session.commit()
    if feld("laden"):  # Segmente 50: „aus Datei laden“ – Schritt ist gespeichert, danach zurück
        ersetzen = f"&ersetzen={feld('laden')}" if feld("laden").isdigit() else ""
        return _zurueck(f"/admin/v/{vid}/strecken/laden?zurueck=/admin/einrichtung/{vid}/2{ersetzen}",
                        ok="Schritt 2 gespeichert – jetzt die Streckendatei wählen.")  # fmt: skip
    einrichten = feld("einrichten")
    if (
        einrichten.isdigit()
        and any(x.id == int(einrichten) for x in strecken.strecken_von(session, v))
        and not vorgeschlagen
    ):
        antwort = _zurueck(f"/admin/v/{vid}/strecken/{einrichten}?zurueck=/admin/einrichtung/{vid}/2",
                           ok="Schritt 2 gespeichert – jetzt Zwischenzeiten und Streckenverlauf einrichten.")  # fmt: skip
        antwort.headers["location"] += "#verlauf"  # Strecken-Editor öffnet den Reiter „Verlauf“
        return antwort
    offen = strecken.plaetze_offen(session, v) if v.einrichtung_schritt is not None else []
    if not offen and not vorgeschlagen and v.einrichtung_schritt is not None:
        try:  # Segmente 59: Längen erst beim Weitergehen prüfen (Wechsel der Wertung bleibt möglich)
            strecken.wertung_laengen_pruefen(session, v, weiter=True)
        except saetze.SatzFehler as e:
            return _zurueck(f"/admin/einrichtung/{vid}/2", fehler=str(e))
    if vorgeschlagen or offen:
        # Segmente 49: ohne Entscheidung für jeden Längen-Platz kein „Weiter“
        bitte = "Bitte für jeden Platz „Aus Datei laden“ oder „Platzhalter-Strecke anlegen“ wählen"
        return _zurueck(f"/admin/einrichtung/{vid}/2", warnung="; ".join(
            hinweise + ([f"{bitte}: noch offen {', '.join(p.name for p in offen)}."] if offen else
                        ["Klassen- oder Wertungssatz geändert – bitte die Strecken prüfen und mit „Weiter“ bestätigen."])))  # fmt: skip
    _weiter(session, v, 3)
    return _zurueck(f"/admin/einrichtung/{vid}/3", warnung="; ".join(hinweise) or None)


@router.post("/{vid}/3")
def schritt3(
    request: Request, vid: int, session: SessionDep, _a: AdminDep, csrf: Annotated[str, Form()] = ""
):
    admin_auth.csrf_pruefen(request, csrf)
    v = _veranstaltung(session, vid)
    _weiter(session, v, 4)
    warnung = (
        None
        if k.teilnehmer_von(session, v)
        else "Noch keine Teilnehmer – sie lassen sich später unter „Teilnehmer“ ergänzen."
    )
    return _zurueck(f"/admin/einrichtung/{vid}/4", warnung=warnung)


@router.post("/{vid}/5")
def abschliessen(request: Request, vid: int, session: SessionDep, _a: AdminDep,
                 csrf: Annotated[str, Form()] = "", aktivieren: Annotated[str, Form()] = ""):  # fmt: skip
    admin_auth.csrf_pruefen(request, csrf)
    v = _veranstaltung(session, vid)
    v.einrichtung_schritt = None
    session.commit()
    if aktivieren:
        try:
            k.aktiv_setzen(session, v)
        except k.KorrekturFehler as e:  # Segmente 55: laufende Veranstaltung nicht verdrängen
            return _zurueck(f"/admin/v/{vid}", ok=f"Einrichtung abgeschlossen: „{v.name}“ ist eingerichtet.",
                            warnung=f"Nicht aktiv gesetzt: {e}")  # fmt: skip
    text = (
        "ist jetzt die aktive Veranstaltung." if aktivieren else "ist eingerichtet (nicht aktiv)."
    )
    return _zurueck(f"/admin/v/{vid}", ok=f"Einrichtung abgeschlossen: „{v.name}“ {text}")
