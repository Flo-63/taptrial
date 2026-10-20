"""Admin-Oberfläche: serverseitig gerendert, Formulare mit Redirect.

Anmeldung: Admin-Token im Formular (oder Alt-Link ?token=…) → signiertes Cookie (app/admin_auth.py).
Fachlogik liegt in app/korrekturen.py.
"""

from collections.abc import Callable
from datetime import date
from types import SimpleNamespace
from typing import Annotated
from urllib.parse import urlencode

import segno
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, object_session

from app import admin_auth, installation, saetze, strecken
from app import korrekturen as k
from app import segmente as sg
from app.auswertung import (
    Art,
    KonfliktArt,
    Status,
    format_zehntel,
    gueltige_ereignisse,
    korrektur_text,
    zehntel,
)
from app.auth import SessionDep
from app.export import (
    STATUS_TEXT,
    ergebnis_csv,
    ergebnis_kopf,
    protokoll_csv,
    quelle_text,
    segment_spalten,
    segment_text,
    status_text,
    stufe_text,
)
from app.models import (
    FREIGABE_FEHLT,
    FREIGABE_SCHRITTE,
    Ereignis,
    GeraetUhr,
    ImportEntwurf,
    Teilnehmer,
    Veranstaltung,
)
from app.proxy import basis_url
from app.seiten import templates
from app.teilnehmer_import import (
    BEISPIEL_CSV,
)
from app.wertung import STUFE_TEXT

router = APIRouter(prefix="/admin", include_in_schema=False)

ZUSTAND_TEXT = {
    "laufend": "laufend",
    "abgeschlossen": "abgeschlossen",
    "anonymisiert": "anonymisiert",
}
ART_TEXT = {
    Art.START: "Start",
    Art.ZIEL: "Ziel",
    Art.ZUORDNUNG: "Zuordnung",
    Art.STORNO: "Storno",
    Art.STATUS: "Status",
    Art.MANUELLE_ZEIT: "Manuelle Zeit",
    Art.ERGEBNIS: "Ergebnis",
    Art.MERKMAL: "Merkmal",
    Art.ZEITKORREKTUR: "Zeitkorrektur",
    Art.DURCHFAHRT: "Durchfahrt",
    Art.RUNDE: "Durchfahrt gewertet",
}

templates.env.filters["uhrzeit"] = k.uhrzeit_text
templates.env.globals["datum_text"] = k.datum_text
templates.env.globals["jetzt_uhrzeit"] = lambda: k.uhrzeit_text(k.jetzt_ms())[:8]


def abbruch_ziel(request: Request, standard: str) -> str:
    """Ziel von „Abbrechen“: die aufrufende Seite (?zurueck=, nur Admin-Adressen) oder der Standard."""
    ziel = request.query_params.get("zurueck", "")
    return ziel if ziel.startswith("/admin/") and "//" not in ziel else standard


templates.env.globals["abbruch_ziel"] = abbruch_ziel
templates.env.filters["fahrzeit"] = lambda ms: "" if ms is None else format_zehntel(zehntel(ms))
templates.env.globals["STATUS_TEXT"] = STATUS_TEXT
templates.env.globals["ZUSTAND_TEXT"] = ZUSTAND_TEXT
templates.env.globals["ART_TEXT"] = ART_TEXT
templates.env.globals["korrektur_text"] = korrektur_text
templates.env.globals["km_text"] = strecken.km_text
templates.env.globals["segment_spalten"] = segment_spalten
templates.env.globals["segment_text"] = segment_text
templates.env.globals["STUFE_TEXT"] = STUFE_TEXT
templates.env.globals["status_text"] = status_text
templates.env.globals["stufe_text"] = stufe_text
templates.env.globals["quelle_text"] = quelle_text


AdminDep = Annotated[admin_auth.Sitzung, Depends(admin_auth.erfordere_admin)]


def aufgaben_anzahl(erg) -> int:
    """Offene Aufgaben vor dem offiziellen Ergebnis: blockierende Konflikte und vorläufige DSQ
    (die Segment-Zeitnahme erzeugt keine Aufgaben)."""
    return len(erg.blockierende_konflikte) + len(erg.vorlaeufige_dsq)


def _nav_zahlen(request: Request, v: Veranstaltung) -> dict:
    """Zahlen für die Seitenleiste (Teilnehmer, offene Aufgaben); eigene kurze Sitzung."""
    with request.app.state.sessionmaker() as session:
        x = session.get(Veranstaltung, v.id)
        erg = k.ergebnis(session, x)
        return {"teilnehmer": len(erg.zeilen), "aufgaben": aufgaben_anzahl(erg)}


def _seite(request: Request, vorlage: str, status_code: int = 200, **kontext) -> HTMLResponse:
    kontext.setdefault("v", None)
    kontext.setdefault("nav_zahlen", lambda v: _nav_zahlen(request, v))
    return templates.TemplateResponse(
        request,
        vorlage,
        {
            "csrf": admin_auth.csrf_token(request),
            "meldung": request.query_params.get("ok"),
            "fehler": request.query_params.get("fehler"),
            "warnung": request.query_params.get("warnung"),
            **kontext,
        },
        status_code=status_code,
    )


def _zurueck(
    ziel: str, ok: str | None = None, fehler: str | None = None, warnung: str | None = None
) -> RedirectResponse:
    parameter = {k: w for k, w in (("ok", ok), ("fehler", fehler), ("warnung", warnung)) if w}
    trenner = "&" if "?" in ziel else "?"
    return RedirectResponse(
        ziel + (trenner + urlencode(parameter) if parameter else ""), status_code=303
    )


def _veranstaltung(session: Session, vid: int) -> Veranstaltung:
    v = session.get(Veranstaltung, vid)
    if v is None:
        raise HTTPException(404, "Veranstaltung nicht gefunden")
    return v


def _erg(request: Request, session: Session, v: Veranstaltung):
    return k.ergebnis(session, v)


def _hat_zeiten(session: Session, v: Veranstaltung) -> bool:
    q = select(func.count()).select_from(Ereignis).where(Ereignis.veranstaltung_id == v.id)
    return bool(session.scalar(q))


def _wertung_an(session: Session, v: Veranstaltung) -> bool:
    return saetze.regeln(session, v).wertung is not None


def _ausfuehren(request: Request, csrf: str, ziel: str, aktion: Callable[[], str]) -> Response:
    """Gemeinsamer Ablauf aller Formular-POSTs: CSRF prüfen, ausführen, Ergebnis melden."""
    admin_auth.csrf_pruefen(request, csrf)
    try:
        meldung = aktion()
    except (k.KorrekturFehler, saetze.SatzFehler) as e:
        return _zurueck(ziel, fehler=str(e))
    return _zurueck(ziel, ok=meldung)


def _rueckweg(ziel: str, standard: str) -> str:
    """Rücksprung nach einem Formular: nur Admin-Seiten (kein offener Redirect)."""
    return ziel if ziel.startswith("/admin/") and "//" not in ziel else standard


def _int(text: str | None, name: str) -> int:
    if text is None or not str(text).strip().isdigit():
        raise k.KorrekturFehler(f"{name} fehlt oder ist ungültig.")
    return int(text)


def _datum(text: str, name: str = "Datum") -> date:
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError) as e:
        raise k.KorrekturFehler(f"{name} ungültig.") from e


# --- Anmeldung ---------------------------------------------------------------


def _anmeldeseite(request: Request, fehler: str | None = None, status_code: int = 401):
    return templates.TemplateResponse(
        request, "admin_anmelden.html", {"fehler": fehler, "v": None}, status_code=status_code
    )


def nicht_angemeldet(request: Request, _exc) -> Response:
    """Ohne Sitzung: Anmeldeformular. Alt-Link /admin?token=… meldet an und entfernt den Token."""
    token = request.query_params.get("token")
    if token and request.method == "GET":
        if admin_auth.token_pruefen(request, token):
            antwort = RedirectResponse(request.url.path, status_code=303)
            admin_auth.sitzung_setzen(request, antwort)
            return antwort
        return _anmeldeseite(request, "Admin-Token falsch.")
    return _anmeldeseite(request)


@router.post("/anmelden")
def anmelden(request: Request, token: Annotated[str, Form()] = ""):
    if not admin_auth.token_pruefen(request, token.strip()):
        return _anmeldeseite(request, "Admin-Token falsch.")
    antwort = RedirectResponse("/admin", status_code=303)
    admin_auth.sitzung_setzen(request, antwort)
    return antwort


@router.post("/abmelden")
def abmelden(request: Request, _a: AdminDep, csrf: Annotated[str, Form()] = ""):
    admin_auth.csrf_pruefen(request, csrf)
    antwort = RedirectResponse("/admin", status_code=303)
    antwort.delete_cookie(admin_auth.COOKIE, path="/admin")
    return antwort


# --- Übersicht ---------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
def uebersicht(request: Request, session: SessionDep, _a: AdminDep, filter: str = ""):
    if "token" in request.query_params:  # Alt-Link bei bestehender Sitzung: Token aus der URL
        return RedirectResponse("/admin", status_code=303)
    veranstaltungen = list(
        session.scalars(select(Veranstaltung).order_by(Veranstaltung.datum.desc()))
    )
    anzahl = dict(
        session.execute(
            select(Teilnehmer.veranstaltung_id, func.count()).group_by(Teilnehmer.veranstaltung_id)
        ).all()
    )
    offen = {v.id: aufgaben_anzahl(_erg(request, session, v)) for v in veranstaltungen if v.laufend}
    faellige = k.faellige(session, k.heute())
    faellig_ids = {f.id for f in faellige}
    filter_fn = {
        "alle": lambda v: True,
        "laufend": lambda v: v.laufend and not v.einrichtung_schritt,
        "einrichtung": lambda v: bool(v.einrichtung_schritt),
        "abgeschlossen": lambda v: v.zustand in ("abgeschlossen", "anonymisiert"),
        "faellig": lambda v: v.id in faellig_ids,
    }  # fmt: skip
    filter = filter if filter in filter_fn else "alle"
    texte = {"alle": "Alle", "laufend": "Aktiv/laufend", "einrichtung": "In Einrichtung",
             "abgeschlossen": "Abgeschlossen", "faellig": "Anonymisierung fällig"}  # fmt: skip
    leiste = {"titel": "Veranstaltungen",
              "knopf": ("Neue Veranstaltung einrichten", "/admin/einrichtung/neu", "neue-veranstaltung"),
              "gruppen": [("Filter", [(f, texte[f], "/admin" + ("" if f == "alle" else f"?filter={f}"),
                                      sum(1 for x in veranstaltungen if fn(x)))
                                     for f, fn in filter_fn.items()])]}  # fmt: skip
    return _seite(
        request,
        "admin_uebersicht.html",
        veranstaltungen=[x for x in veranstaltungen if filter_fn[filter](x)],
        alle_anzahl=len(veranstaltungen),
        anzahl=anzahl,
        offen=offen,
        faellige=faellige,
        heute=k.heute().isoformat(),
        leiste=leiste,
        reiter=filter,
        filter_text=texte[filter],
    )


@router.post("/veranstaltungen")
def veranstaltung_anlegen(
    request: Request,
    session: SessionDep,
    _a: AdminDep,
    csrf: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
    datum: Annotated[str, Form()] = "",
    aktivieren: Annotated[str, Form()] = "",
):
    admin_auth.csrf_pruefen(request, csrf)
    try:
        v = k.veranstaltung_anlegen(session, name, _datum(datum), aktivieren=bool(aktivieren))
    except k.KorrekturFehler as e:
        return _zurueck("/admin", fehler=str(e))
    return _zurueck(f"/admin/v/{v.id}/teilnehmer", ok=f"Veranstaltung „{v.name}“ angelegt.")


# --- Veranstaltung: Überblick, Ergebnis, DSQ, Ergebnisstatus -----------------


def _kennzahlen(erg) -> dict:
    zeilen = erg.zeilen
    unterwegs = k.auf_der_strecke(erg)
    return {
        "teilnehmer": len(zeilen),
        "gestartet": sum(1 for z in zeilen if z.start_ms is not None),
        "strecke": len(unterwegs),
        "ziel": sum(1 for z in zeilen if z.ziel_ms is not None),
        "aufgaben": aufgaben_anzahl(erg),
    }


def freigabe_schritte(v: Veranstaltung) -> list[dict]:
    """Fehlende Schritte bis zur Freigabe mit der Stelle, an der sie erledigt werden."""
    url = {"einrichtung": f"/admin/einrichtung/{v.id}/{v.einrichtung_schritt}",
           "aktiv": f"/admin/v/{v.id}/status#aktiv"}  # fmt: skip
    return [{"code": c, "text": FREIGABE_SCHRITTE[c], "url": url[c]} for c in v.freigabe_fehlt]


def als_naechstes(session: Session, v: Veranstaltung, erg, heute: date) -> list[dict]:
    """„Als Nächstes“ der Übersicht, aus dem Zustand abgeleitet.

    Jede Aufgabe: schluessel, titel, hinweis, knopf (Beschriftung), url.
    """
    basis = f"/admin/v/{v.id}"
    liste: list[dict] = []

    def neu(schluessel, titel, hinweis, knopf, url):
        liste.append({"schluessel": schluessel, "titel": titel, "hinweis": hinweis, "knopf": knopf,
                      "url": url})  # fmt: skip

    if v.laufend and v.freigabe_fehlt:  # Geräte gesperrt, Grund und Wege dorthin
        schritte = freigabe_schritte(v)
        neu("freigabe", FREIGABE_FEHLT, "Die Geräte sind gesperrt, bis das erledigt ist: "
            + ", ".join(s["text"] for s in schritte), schritte[0]["text"], schritte[0]["url"])  # fmt: skip
    if v.einrichtung_schritt:
        neu("einrichtung", "Einrichtung abschließen", f"Schritt {v.einrichtung_schritt} von 5 ist offen",
            "Einrichtung fortsetzen", f"/admin/einrichtung/{v.id}/{v.einrichtung_schritt}")  # fmt: skip
    if v.laufend:
        if not erg.zeilen:
            neu("teilnehmer", "Noch keine Teilnehmer", "CSV importieren oder einzeln anlegen",
                "Teilnehmer anlegen", f"{basis}/teilnehmer")  # fmt: skip
        for s in strecken.strecken_von(session, v):
            if not s.laenge_km:
                neu("laenge", f"Strecke „{s.name}“: Länge fehlt",
                    "Gemessene Länge eintragen (für Wertung und Urkunden)", "Strecke öffnen",
                    f"{basis}/strecken/{s.id}")  # fmt: skip
        if not v.aktiv:
            neu("aktiv", "Veranstaltung ist nicht aktiv",
                "Erst dann erscheint sie auf Startseite, Monitor und Ergebnissen", "Aktiv setzen",
                f"{basis}/status")  # fmt: skip
        if not erg.offiziell:
            uhren = session.scalars(
                select(GeraetUhr).where(GeraetUhr.veranstaltung_id == v.id)
            ).all()
            rollen = {u.rolle for u in uhren}
            geraete = []  # Je genutztem Start-/Zielort ein Gerät, dazu die Posten
            for m in strecken.orte_von(session, v):
                if m.art == "punkt":  # Nur genutzte Zwischenzeiten
                    if strecken.genutzt(session, v, m):
                        geraete.append((f"posten-{m.id}", f"Posten {m.name}"))
                elif strecken.strecken_am_ort(session, v, m) or (m.standard and m.id is None):
                    wort = "Startgerät" if m.art == "start" else "Zielgerät"
                    geraete.append((geraete_rolle(m), wort if m.standard else f"{wort} {m.name}"))
            for rolle, name in geraete:
                if rolle not in rollen:
                    neu(f"geraet-{rolle}", f"{name} noch nicht verbunden",
                        "Gerätelink auf dem Handy öffnen; es meldet sich beim ersten Sync",
                        "Geräte", f"{basis}/geraete")  # fmt: skip
            for u in uhren:
                if abs(u.abweichung_ms) > UHR_GRENZE_MS:
                    neu("uhr", f"Gerät {u.geraet}: Uhr weicht {abweichung_text(u.abweichung_ms)} ab",
                        "Vor dem Start müssen alle Geräte unter 0,5 s liegen", "Geräte prüfen",
                        f"{basis}/geraete")  # fmt: skip
        n = aufgaben_anzahl(erg)
        if n:
            neu("aufgaben", f"{n} offene Aufgabe{'n' if n != 1 else ''}",
                "Konflikte und vorläufige DSQ klären", "Zu den Aufgaben", f"{basis}/aufgaben")  # fmt: skip
        elif (
            not erg.offiziell
            and any(z.fahrzeit_ms is not None for z in erg.zeilen)
            and not k.auf_der_strecke(erg)
        ):  # Alle Gestarteten durch
            neu("offiziell", "Ergebnis offiziell erklären", "Keine Aufgaben mehr offen",
                "Zum Ergebnis", f"{basis}/ergebnis")  # fmt: skip
        if erg.offiziell:
            neu("abschliessen", "Veranstaltung abschließen",
                "Nach dem Backup: danach schreibgeschützt, Gerätelinks ungültig",
                "Status & Datenschutz", f"{basis}/status")  # fmt: skip
        if v.aktiv and not installation.lesen(session)["vereinsname"].strip():
            # v0.1.1: dezent, die Installation läuft auch ohne Vereinsnamen (dann neutral)
            neu("vereinsname", "Vereinsname noch nicht eingetragen",
                "Steht dann vorn auf Startseite, Ergebnissen, Monitor und Ausdrucken", "Eintragen",
                "/admin/saetze/branding")  # fmt: skip
            liste[-1]["dezent"] = True
        if v.aktiv and (fehlt := installation.rechtliches_fehlt(installation.lesen(session))):
            # Dezent, nur solange die Veranstaltung öffentlich zu sehen ist
            titel = (
                f"{fehlt[0]} fehlt"
                if len(fehlt) == 1
                else "Impressum und Datenschutzerklärung fehlen"
            )
            neu("rechtliches", titel,
                "Für die öffentlichen Seiten; einmal für die Installation eintragen", "Eintragen",
                "/admin/saetze/rechtliches")  # fmt: skip
            liste[-1]["dezent"] = True
    elif v.zustand == "abgeschlossen" and v.anonymisieren_ab <= heute:
        neu("anonymisieren", "Anonymisierung fällig",
            f"Frist seit {v.anonymisieren_ab.strftime('%d.%m.%Y')} erreicht; es wird nichts automatisch gelöscht",
            "Status & Datenschutz", f"{basis}/status")  # fmt: skip
    return liste


@router.get("/v/{vid}", response_class=HTMLResponse)
def veranstaltung(request: Request, vid: int, session: SessionDep, _a: AdminDep):
    v = _veranstaltung(session, vid)
    erg = _erg(request, session, v)
    return _seite(request, "admin_v_uebersicht.html", v=v, reiter="uebersicht", erg=erg,
                  kopf=ergebnis_kopf(v, erg), zahlen=_kennzahlen(erg),
                  aufgaben=als_naechstes(session, v, erg, k.heute()),
                  regeln=saetze.regeln(session, v), strecken=strecken.strecken_von(session, v),
                  km_text=strecken.km_text, FARBEN=strecken.FARBEN)  # fmt: skip


VORSCHLAEGE = 3


def vorschlaege(erg, zeit_ms: int) -> list[dict]:
    """Passende Fahrer für eine offene Zielzeit: unterwegs, sortiert nach Nähe zur erwarteten
    Ankunft (Start + Median der Fahrzeiten ihrer Strecke; ohne Median nach Startzeit)."""
    fahrzeiten: dict[int, list[int]] = {}
    for z in erg.zeilen:
        if z.fahrzeit_ms is not None:
            fahrzeiten.setdefault(z.strecke, []).append(z.fahrzeit_ms)
    median = {i: sorted(f)[len(f) // 2] for i, f in fahrzeiten.items()}
    kandidaten = []
    for z in erg.zeilen:
        if z.start_ms is None or z.ziel_ms is not None or z.start_ms >= zeit_ms:
            continue
        if z.status in (Status.DNS, Status.DNF, Status.DSQ):
            continue
        erwartet = z.start_ms + median.get(z.strecke, 0)
        kandidaten.append((abs(zeit_ms - erwartet) if z.strecke in median else -z.start_ms, z))
    kandidaten.sort(key=lambda x: x[0])
    return [{"nr": z.startnummer, "name": f"{z.vorname} {z.nachname}",
             "unterwegs": format_zehntel(zehntel(zeit_ms - z.start_ms))}
            for _, z in kandidaten[:VORSCHLAEGE]]  # fmt: skip


@router.get("/v/{vid}/aufgaben", response_class=HTMLResponse)
def aufgaben(request: Request, vid: int, session: SessionDep, _a: AdminDep):
    """Aufgaben vor dem offiziellen Ergebnis, jeder Fall direkt lösbar."""
    v = _veranstaltung(session, vid)
    erg = _erg(request, session, v)
    alle = {s.uuid: s for s in k.stempel(session, v)}
    offene = [{"stempel": alle[o.uuid], "vorschlaege": vorschlaege(erg, o.zeit_ms)}
              for o in erg.offene_ziele if o.uuid in alle]  # fmt: skip
    faelle = [{"k": x, "stempel": [alle[u] for u in x.uuids if u in alle],
               "fahrer": _tid_von(session, v, x.startnummer)}
              for x in erg.blockierende_konflikte
              if x.art != KonfliktArt.OHNE_ZUORDNUNG]  # fmt: skip
    hinweise = [{"k": x, "fahrer": _tid_von(session, v, x.startnummer)}
                for x in erg.konflikte if x.art == KonfliktArt.UNPLAUSIBEL]  # fmt: skip
    return _seite(request, "admin_aufgaben.html", v=v, reiter="aufgaben", erg=erg, offene=offene,
                  faelle=faelle, hinweise=hinweise, anzahl=aufgaben_anzahl(erg),
                  tid=lambda nr: _tid_von(session, v, nr))  # fmt: skip


def _tid_von(session: Session, v: Veranstaltung, nr: int | None) -> int | None:
    if nr is None:
        return None
    return session.scalar(select(Teilnehmer.id).where(Teilnehmer.veranstaltung_id == v.id,
                                                      Teilnehmer.startnummer == nr))  # fmt: skip


@router.get("/v/{vid}/ergebnis", response_class=HTMLResponse)
def ergebnis_seite(
    request: Request, vid: int, session: SessionDep, _a: AdminDep, rangliste: str = ""
):
    from app import ranglisten

    v = _veranstaltung(session, vid)
    erg = _erg(request, session, v)
    return _seite(request, "admin_ergebnis.html", v=v, reiter="ergebnis", erg=erg,
                  ranglisten=ranglisten.verfuegbar(erg),
                  rl=ranglisten.rangliste(erg, rangliste) if rangliste else None,
                  kopf=ergebnis_kopf(v, erg), sperrgruende=k.offiziell_sperrgruende(erg),
                  auf_strecke=len(k.auf_der_strecke(erg)),
                  urkunde_moeglich=urkunde_moeglich, tid=lambda nr: _tid_von(session, v, nr),
                  heute=k.heute(), Status=Status)  # fmt: skip


# Alte Adressen (vor der Neuordnung) leiten auf die neuen Seiten weiter
ALTE_SEITEN = {"korrekturen": "aufgaben", "rennen": "aufgaben", "stammdaten": "einstellungen",
               "klassen": "einstellungen/klassen", "zielmonitor": "einstellungen/zielmonitor"}  # fmt: skip


def _alte_seite(alt: str, neu: str):
    def weiter(vid: int, _a: AdminDep):
        return RedirectResponse(f"/admin/v/{vid}/{neu}", status_code=307)

    weiter.__name__ = f"{alt}_alt"
    return weiter


for _alt, _neu in ALTE_SEITEN.items():
    router.add_api_route(f"/v/{{vid}}/{_alt}", _alte_seite(_alt, _neu), methods=["GET"])


@router.post("/v/{vid}/dsq")
def dsq_entscheiden(
    request: Request,
    vid: int,
    session: SessionDep,
    _a: AdminDep,
    csrf: Annotated[str, Form()] = "",
    startnummer: Annotated[str, Form()] = "",
    entscheidung: Annotated[str, Form()] = "",
    notiz: Annotated[str, Form()] = "",
    zurueck: Annotated[str, Form()] = "",
):
    v = _veranstaltung(session, vid)

    def aktion():
        nr = _int(startnummer, "Startnummer")
        if entscheidung == "bestaetigen":
            k.dsq_bestaetigen(session, v, nr, notiz)
            return f"DSQ für Nr. {nr} bestätigt."
        if entscheidung == "verwerfen":
            k.dsq_verwerfen(session, v, nr, notiz)
            return f"Vorläufiger DSQ für Nr. {nr} verworfen."
        raise k.KorrekturFehler("Unbekannte Entscheidung.")

    return _ausfuehren(request, csrf, _rueckweg(zurueck, f"/admin/v/{vid}/aufgaben"), aktion)


@router.post("/v/{vid}/ergebnisstatus")
def ergebnisstatus(
    request: Request,
    vid: int,
    session: SessionDep,
    _a: AdminDep,
    csrf: Annotated[str, Form()] = "",
    wert: Annotated[str, Form()] = "",
    notiz: Annotated[str, Form()] = "",
    bestaetigung: Annotated[str, Form()] = "",
):
    v = _veranstaltung(session, vid)

    def aktion():
        if wert == "OFFIZIELL":
            if not bestaetigung:
                raise k.KorrekturFehler(
                    "Bitte bestätigen, dass das Ergebnis offiziell werden soll."
                )
            k.ergebnis_offiziell(session, v, notiz or "Ergebnis offiziell erklärt")
            return "Ergebnis ist offiziell."
        k.ergebnis_vorlaeufig(session, v, notiz)
        return "Ergebnis ist wieder vorläufig."

    return _ausfuehren(request, csrf, f"/admin/v/{vid}/ergebnis", aktion)


@router.get("/v/{vid}/drucken", response_class=HTMLResponse)
def drucken(request: Request, vid: int, session: SessionDep, _a: AdminDep):
    v = _veranstaltung(session, vid)
    from app import ranglisten

    return _seite(
        request,
        "admin_drucken.html",
        v=v,
        reiter="drucken",
        ranglisten=ranglisten.verfuegbar(_erg(request, session, v)),
    )


@router.post("/v/{vid}/zielmonitor")
async def zielmonitor_speichern(request: Request, vid: int, session: SessionDep, _a: AdminDep):
    """Speichern; „auf:i“/„ab:i“ verschieben, „weg:i“ entfernt, „neu“ fügt eine Seite hinzu."""
    from app import monitor_konfig as mk

    form = await request.form()
    admin_auth.csrf_pruefen(request, str(form.get("csrf") or ""))
    v = _veranstaltung(session, vid)
    liste = strecken.strecken_von(session, v)
    ziel = _rueckweg(str(form.get("ziel") or ""), f"/admin/v/{vid}/einstellungen/zielmonitor")
    try:
        konf = mk.aus_formular(form, mk.laden(v, liste), liste)
        aktion = str(form.get("aktion") or "speichern")
        seiten = konf["seiten"]
        art, _, nr = aktion.partition(":")
        i = int(nr) if nr.isdigit() and int(nr) < len(seiten) else None
        if art == "auf" and i:
            seiten[i - 1], seiten[i] = seiten[i], seiten[i - 1]
        elif art == "ab" and i is not None and i < len(seiten) - 1:
            seiten[i + 1], seiten[i] = seiten[i], seiten[i + 1]
        elif art == "weg" and i is not None:
            del seiten[i]
        elif art == "neu":
            seiten.append(mk.neue_seite(str(form.get("neuer_typ") or "ergebnis")))
    except mk.KonfigFehler as e:
        return _zurueck(ziel, fehler=str(e))
    import json

    v.monitor_json = json.dumps(konf, ensure_ascii=False)
    session.commit()
    if form.get("weiter") and v.einrichtung_schritt:  # Schritt „Zielmonitor“ im Assistenten
        v.einrichtung_schritt = max(v.einrichtung_schritt, 5)
        session.commit()
        return _zurueck(f"/admin/einrichtung/{vid}/5")
    return _zurueck(
        ziel, ok="Zielmonitor gespeichert – der Monitor übernimmt es beim nächsten Abruf."
    )


@router.get("/v/{vid}/status", response_class=HTMLResponse)
def status_seite(request: Request, vid: int, session: SessionDep, _a: AdminDep):
    v = _veranstaltung(session, vid)
    return _seite(request, "admin_status.html", v=v, reiter="status", heute=k.heute(),
                  startzeiten=k.startzeiten_vorhanden(session, v), laufende=k.laufende_andere(session, v),
                  verdraengen_text=k.verdraengen_text,
                  aktiv_sperrgrund=k.aktiv_sperrgrund(v) if v.einrichtung_schritt else "")  # fmt: skip


# --- Korrekturen ---------------------------------------------------------------


@router.post("/v/{vid}/korrektur")
def korrektur(
    request: Request,
    vid: int,
    session: SessionDep,
    _a: AdminDep,
    csrf: Annotated[str, Form()] = "",
    aktion: Annotated[str, Form()] = "",
    stempel: Annotated[str, Form()] = "",
    startnummer: Annotated[str, Form()] = "",
    startnummer_b: Annotated[str, Form()] = "",
    art: Annotated[str, Form()] = "",
    uhrzeit: Annotated[str, Form()] = "",
    fahrzeit: Annotated[str, Form()] = "",
    wert: Annotated[str, Form()] = "",
    notiz: Annotated[str, Form()] = "",
    zurueck: Annotated[str, Form()] = "",
    vorschlag: Annotated[str, Form()] = "",
    zuordnen: Annotated[str, Form()] = "",
):
    v = _veranstaltung(session, vid)
    if vorschlag.strip():  # Vorschlag-Button auf „Aufgaben“ ersetzt das freie Nummernfeld
        startnummer = vorschlag

    def ausfuehren():
        if zuordnen and not startnummer.strip():
            raise k.KorrekturFehler("Bitte einen Fahrer wählen oder eine Startnummer eintragen.")
        if aktion == "umbuchen":
            neu = _int(startnummer, "Neue Startnummer") if startnummer.strip() else None
            k.umbuchen(session, v, stempel, neu, notiz)
            return "Zeitstempel umgebucht." if neu else "Zuordnung aufgehoben."
        if aktion == "tauschen":
            a, b = _int(startnummer, "Startnummer A"), _int(startnummer_b, "Startnummer B")
            k.tauschen(session, v, a, b, art, notiz)
            return (
                f"{'Start' if art == 'START' else 'Ziel'}zeiten von Nr. {a} und Nr. {b} getauscht."
            )
        if aktion == "stornieren":
            k.stornieren(session, v, stempel, notiz)
            return "Zeitstempel storniert."
        if aktion == "manuell":
            nr = _int(startnummer, "Startnummer")
            k.manuelle_zeit(session, v, nr, art, uhrzeit, fahrzeit, notiz)
            return f"Manuelle Zeit für Nr. {nr} eingetragen."
        if aktion == "status":
            nr = _int(startnummer, "Startnummer")
            k.status_setzen(session, v, nr, wert, notiz)
            return f"Status {wert} für Nr. {nr} gesetzt."
        if (
            aktion == "doppeltipp_werten"
        ):  # Segmente: automatisch verworfenen Doppeltipp doch werten
            k.doppeltipp_werten(session, v, stempel, notiz)
            return "Doppeltipp zählt wieder als Durchfahrt."
        raise k.KorrekturFehler("Unbekannte Korrektur.")

    return _ausfuehren(request, csrf, _rueckweg(zurueck, f"/admin/v/{vid}/aufgaben"), ausfuehren)


# --- Teilnehmer -----------------------------------------------------------------


STAND_FILTER = {
    "alle": "alle", "nicht": "noch nicht gestartet", "gestartet": "gestartet",
    "strecke": "auf der Strecke", "ziel": "im Ziel", "status": "DNS / DNF / DSQ",
}  # fmt: skip


def stand_von(z) -> tuple[str, str, str]:
    """(Filter-Schlüssel, Text, Badge-Klasse) des Fahrers für Liste und Fahrer-Seite."""
    if z.status in (Status.DNS, Status.DNF, Status.DSQ):
        return "status", z.status.value, "b-danger" if z.status == Status.DSQ else ""
    if z.dsq_vorlaeufig:
        return "ziel" if z.ziel_ms else "strecke", "DSQ vorläufig", "b-warn"
    if z.ziel_ms is not None:
        return "ziel", "im Ziel", "b-ok"
    if z.start_ms is not None:
        return "strecke", "auf der Strecke", "b-brand"
    return "nicht", "noch nicht gestartet", ""


def _passt(z, t, q: str, strecke: str, stand: str) -> bool:
    if q:
        text = f"{t.startnummer} {t.vorname} {t.nachname} {t.verein or ''}".lower()
        if not all(teil in text for teil in q.lower().split()):
            return False
    if strecke.isdigit() and z.strecke != int(strecke):
        return False
    schluessel = stand_von(z)[0]
    if stand == "gestartet":
        return z.start_ms is not None
    return stand in ("", "alle") or schluessel == stand


@router.get("/v/{vid}/teilnehmer", response_class=HTMLResponse)
def teilnehmer_seite(request: Request, vid: int, session: SessionDep, _a: AdminDep,
                     q: str = "", strecke: str = "", stand: str = ""):  # fmt: skip
    v = _veranstaltung(session, vid)
    return _teilnehmerseite(request, session, v, q=q.strip(), strecke=strecke, stand=stand)


def _teilnehmerseite(request, session, v, status_code=200, vorlage="admin_teilnehmer.html",
                     q="", strecke="", stand="", **mehr):  # fmt: skip
    erg = _erg(request, session, v)
    teilnehmer = k.teilnehmer_von(session, v)
    zeilen = {z.startnummer: z for z in erg.zeilen}
    gefiltert = [t for t in teilnehmer
                 if t.startnummer in zeilen and _passt(zeilen[t.startnummer], t, q, strecke, stand)]  # fmt: skip
    return _seite(
        request, vorlage, status_code=status_code, v=v, reiter="teilnehmer",
        teilnehmer=teilnehmer, gefiltert=gefiltert, zeilen=zeilen, erg=erg, stand_von=stand_von,
        filter={"q": q, "strecke": strecke, "stand": stand or "alle"}, STAND_FILTER=STAND_FILTER,
        wertung_an=_wertung_an(session, v),
        anzahl_abzeichen=sum(t.abzeichen for t in teilnehmer),
        anzahl_ak=sum(t.ausser_konkurrenz for t in teilnehmer),
        entwurf_vorhanden=session.scalars(
            select(ImportEntwurf).where(ImportEntwurf.veranstaltung_id == v.id)
        ).first() is not None,
        **mehr,
    )  # fmt: skip


@router.get("/v/{vid}/teilnehmer/neu", response_class=HTMLResponse)
def teilnehmer_neu(request: Request, vid: int, session: SessionDep, _a: AdminDep):
    v = _veranstaltung(session, vid)
    return _seite(request, "admin_teilnehmer_neu.html", v=v, reiter="teilnehmer",
                  wertung_an=_wertung_an(session, v))  # fmt: skip


def _teilnehmer(session: Session, v: Veranstaltung, tid: int) -> Teilnehmer:
    t = session.get(Teilnehmer, tid)
    if t is None or t.veranstaltung_id != v.id:
        raise HTTPException(404, "Teilnehmer nicht gefunden")
    return t


def protokoll_von(session: Session, v: Veranstaltung, nr: int | None = None):
    """Ereignisse chronologisch, gültige UUIDs; mit nr nur die des Fahrers (inkl. Bezüge)."""
    ereignisse = sorted(
        k.ereignisse_von(session, v), key=lambda e: (e.erstellt_ms, e.empfangen_ms, e.uuid)
    )
    gueltig = {e.uuid for e in gueltige_ereignisse(ereignisse)}
    if nr is not None:
        eigene = {e.uuid for e in ereignisse if e.startnummer == nr}
        ereignisse = [e for e in ereignisse if e.uuid in eigene or e.bezug_uuid in eigene]
    return ereignisse, gueltig


def durchfahrten_von(erg, z) -> list[SimpleNamespace]:
    """Fahrer-Seite (Segmente): alle Durchfahrten des Fahrers mit Herkunft (Gerät) und Zuordnung zur
    erwarteten Abfolge, dazu fehlende Positionen; chronologisch bzw. an ihrer Stelle."""
    namen = {m.id: m.name for st in erg.strecken for m in st.messpunkte}
    platz = {d.uuid: d for d in z.durchfahrten if d.uuid}
    liste = []
    for s in sorted(
        (s for s in erg.durchfahrtstempel if s.startnummer == z.startnummer),
        key=lambda s: s.zeit_ms,
    ):
        d = platz.get(s.uuid)
        liste.append(SimpleNamespace(uuid=s.uuid, zeit_ms=s.zeit_ms, messpunkt=namen.get(s.ort, s.ort),
                                     seit_start=s.zeit_ms - z.start_ms if z.start_ms else None, geraet=s.geraet,
                                     korrektur_ms=s.korrektur_ms, zuordnung=d.label if d else None,
                                     position=d.position + 1 if d else None,
                                     grund=erg.nicht_gezaehlt.get(s.uuid)))  # fmt: skip
    for d in z.durchfahrten:  # erwartet, aber nicht erfasst
        if d.uuid is None:
            vor = next(
                (i for i, x in enumerate(liste) if x.position and x.position > d.position + 1),
                len(liste),
            )
            liste.insert(vor, SimpleNamespace(uuid=None, zeit_ms=None, messpunkt=namen.get(d.messpunkt, d.messpunkt),
                                              seit_start=None, geraet="", korrektur_ms=0, zuordnung=d.label,
                                              position=d.position + 1, grund=None))  # fmt: skip
    return liste


@router.get("/v/{vid}/teilnehmer/{tid}", response_class=HTMLResponse)
def fahrer(request: Request, vid: int, tid: int, session: SessionDep, _a: AdminDep):
    """Fahrer-Seite: Stammdaten, Zeiten mit Herkunft, Status, Merkmale, Verlauf, Aktionen."""
    v = _veranstaltung(session, vid)
    t = _teilnehmer(session, v, tid)
    erg = _erg(request, session, v)
    z = next((x for x in erg.zeilen if x.startnummer == t.startnummer), None)
    eigene = [s for s in k.stempel(session, v) if s.startnummer == t.startnummer]
    ereignisse, gueltig = protokoll_von(session, v, t.startnummer)
    return _seite(request, "admin_fahrer.html", v=v, reiter="teilnehmer", hilfe="fahrer", t=t, z=z, erg=erg,
                  durchfahrten=durchfahrten_von(erg, z) if z else [],
                  stempel=eigene, ereignisse=ereignisse, gueltig=gueltig, stand_von=stand_von,
                  wertung_an=_wertung_an(session, v), urkunde_moeglich=urkunde_moeglich,
                  hier=f"/admin/v/{v.id}/teilnehmer/{t.id}")  # fmt: skip


@router.get("/v/{vid}/teilnehmer/{tid}/bearbeiten", response_class=HTMLResponse)
def teilnehmer_bearbeiten(request: Request, vid: int, tid: int, session: SessionDep, _a: AdminDep):
    v = _veranstaltung(session, vid)
    t = _teilnehmer(session, v, tid)
    return _seite(request, "admin_teilnehmer_bearbeiten.html", v=v, reiter="teilnehmer", t=t,
                  wertung_an=_wertung_an(session, v),
                  ziel=f"/admin/v/{v.id}/teilnehmer/{t.id}")  # fmt: skip


@router.post("/v/{vid}/teilnehmer")
def teilnehmer_speichern(
    request: Request,
    vid: int,
    session: SessionDep,
    _a: AdminDep,
    csrf: Annotated[str, Form()] = "",
    tid: Annotated[str, Form()] = "",
    startnummer: Annotated[str, Form()] = "",
    vorname: Annotated[str, Form()] = "",
    nachname: Annotated[str, Form()] = "",
    geschlecht: Annotated[str, Form()] = "",
    geburtsjahr: Annotated[str, Form()] = "",
    verein: Annotated[str, Form()] = "",
    abzeichen: Annotated[str, Form()] = "",
    ausser_konkurrenz: Annotated[str, Form()] = "",
    ziel: Annotated[str, Form()] = "",
):
    v = _veranstaltung(session, vid)
    daten = {"startnummer": startnummer, "vorname": vorname, "nachname": nachname,
             "geschlecht": geschlecht, "geburtsjahr": geburtsjahr, "verein": verein,
             "ausser_konkurrenz": ausser_konkurrenz}  # fmt: skip
    if _wertung_an(session, v):  # bei ausgeschalteter Wertung gibt es kein Abzeichen-Feld
        daten["abzeichen"] = abzeichen

    def aktion():
        t = k.teilnehmer_speichern(session, v, int(tid) if tid.isdigit() else None, daten)
        return f"Nr. {t.startnummer} {t.vorname} {t.nachname} gespeichert."

    return _ausfuehren(request, csrf, _rueckweg(ziel, f"/admin/v/{vid}/teilnehmer"), aktion)


@router.post("/v/{vid}/teilnehmer/{tid}/merkmal")
def merkmal_umschalten(
    request: Request, vid: int, tid: int, session: SessionDep, _a: AdminDep,
    csrf: Annotated[str, Form()] = "", merkmal: Annotated[str, Form()] = "",
    wert: Annotated[str, Form()] = "", zurueck: Annotated[str, Form()] = "",
):  # fmt: skip
    v = _veranstaltung(session, vid)

    def aktion():
        if merkmal == "abzeichen" and not _wertung_an(session, v):
            raise k.KorrekturFehler("Wertung aus: Abzeichen spielt keine Rolle.")
        t = k.merkmal_setzen(session, v, tid, merkmal, wert == "ja")
        name = k.MERKMAL_NAMEN.get(merkmal, merkmal)
        return f"Nr. {t.startnummer}: {name} {'ja' if wert == 'ja' else 'nein'}."

    return _ausfuehren(request, csrf, _rueckweg(zurueck, f"/admin/v/{vid}/teilnehmer"), aktion)


@router.post("/v/{vid}/teilnehmer/{tid}/loeschen")
def teilnehmer_loeschen(
    request: Request, vid: int, tid: int, session: SessionDep, _a: AdminDep,
    csrf: Annotated[str, Form()] = "",
):  # fmt: skip
    v = _veranstaltung(session, vid)

    def aktion():
        k.teilnehmer_loeschen(session, v, tid)
        return "Teilnehmer gelöscht."

    return _ausfuehren(request, csrf, f"/admin/v/{vid}/teilnehmer", aktion)


@router.get("/beispiel-teilnehmer.csv")
def beispiel_csv(_a: AdminDep):
    return _download(BEISPIEL_CSV.read_bytes(), "teilnehmer_beispiel.csv")


# --- Geräte -----------------------------------------------------------------------


UHR_GRENZE_MS = 500  # ab mehr als 0,5 s Abweichung warnen


def abweichung_text(ms: int) -> str:
    """Abweichung Server − Gerät in Zehntelsekunden mit Vorzeichen, z. B. „+0,8 s“, „±0,0 s“."""
    zehntel = round(abs(ms) / 100)
    vorzeichen = "±" if zehntel == 0 else ("+" if ms > 0 else "−")
    return f"{vorzeichen}{zehntel // 10},{zehntel % 10} s"


ROLLEN_TEXT = {"start": "Start", "ziel": "Ziel", "rennleitung": "Rennleitung"}


def posten_namen(session: Session, v: Veranstaltung) -> dict[str, str]:
    """Rolle eines Geräts → Name: „posten-<ID>“ → „Posten <Name>“ (Segmente), weitere Start-/Zielorte
    „start-<ID>“ → „Start <Name>“, „ziel-<ID>“ → „Ziel <Name>“, „rennleitung-<ID>“."""
    namen = {}
    for m in strecken.orte_von(session, v):
        if m.art == "punkt":
            namen[f"posten-{m.id}"] = f"Posten {m.name}"
        elif not m.standard:
            namen[f"{m.art}-{m.id}"] = strecken.ort_bezeichnung(m)
            if m.art == "ziel":
                namen[f"rennleitung-{m.id}"] = f"Rennleitung {m.name}"
    return namen


def uhr_zeile(u: GeraetUhr, namen: dict[str, str] | None = None) -> dict:
    rolle = (namen or {}).get(u.rolle) or ROLLEN_TEXT.get(u.rolle, u.rolle)
    return {"geraet": u.geraet, "rolle": rolle,
            "text": abweichung_text(u.abweichung_ms),
            "warnung": abs(u.abweichung_ms) > UHR_GRENZE_MS,
            "gemessen": f"{k.datum_text(u.gemessen_ms)} {k.uhrzeit_text(u.gemessen_ms)}"}  # fmt: skip


def geraetelinks(request: Request, v: Veranstaltung) -> list[dict]:
    """Gerätelinks je Ort: Startorte, Messpunkte (Posten), Zielorte mit Rennleitung.

    Standardorte nutzen die Tokens der Veranstaltung (bisherige Links). Jeder Link trägt „gruppe“
    (Ort) für die gruppierte Anzeige unter „Geräte“.
    """
    basis = basis_url(request, request.app.state.vertraute_proxys)
    session = object_session(v)
    liste = strecken.strecken_von(session, v) if session is not None else []
    orte = strecken.orte_von(session, v) if session is not None else []
    links, hinweise = [], {}

    def strecken_text(indizes) -> str:
        namen = [liste[i].name for i in indizes]
        return ("Strecken: " if len(namen) > 1 else "Strecke: ") + ", ".join(namen) if namen else ""

    # Messpunkte, die keine Strecke nutzt, haben keinen Gerätelink (Token bleibt)
    orte = [m for m in orte if not liste or strecken.genutzt(session, v, m)]
    mehrere = {art: sum(1 for m in orte if m.art == art) > 1 for art in ("start", "ziel")}
    for m in orte:
        gruppe = strecken.messpunkt_titel(m)
        if m.art == "punkt":  # Ein Posten-Link je Messpunkt, mit den nutzenden Strecken
            links.append(
                (
                    f"Posten {m.name}",
                    f"{basis}/posten?token={m.token}&ort={m.id}",
                    f"posten-{m.id}",
                    gruppe,
                )
            )
            hinweise[f"posten-{m.id}"] = strecken_text(strecken.nutzende_strecken(liste, m.id))
            continue
        rolle = m.art if m.standard else f"{m.art}-{m.id}"
        token = getattr(v, f"token_{m.art}") if m.standard else m.token
        ort = "" if m.standard else f"&ort={m.id}"  # Eigenes Geräteprofil im Browser
        links.append(
            (strecken.ort_bezeichnung(m), f"{basis}/{m.art}?token={token}{ort}", rolle, gruppe)
        )
        if mehrere[m.art]:
            hinweise[rolle] = strecken_text(strecken.strecken_am_ort(session, v, m))
        if m.art == "ziel":
            getrennt = v.rennleitung_getrennt if m.standard else m.rennleitung_getrennt
            titel = "Rennleitung" if m.standard else f"Rennleitung {m.name}"
            if getrennt:
                rtoken = v.token_rennleitung if m.standard else m.token_rennleitung
                rrolle = "rennleitung" if m.standard else f"rennleitung-{m.id}"
                links.append((titel, f"{basis}/rennleitung?token={rtoken}{ort}", rrolle, gruppe))
            else:
                links.append(
                    (titel, f"{basis}/ziel?token={token}{ort}&sicht=rennleitung", rolle, gruppe)
                )
    if not orte:  # ohne Datenbank-Sitzung (nur Tests): Standard-Links
        links = [("Start", f"{basis}/start?token={v.token_start}", "start", "Start"),
                 ("Ziel", f"{basis}/ziel?token={v.token_ziel}", "ziel", "Ziel")]  # fmt: skip
    return [
        {
            "titel": titel,
            "url": url,
            "rolle": rolle,
            "gruppe": gruppe,
            "qr": segno.make(url, error="m").svg_inline(scale=4, border=2),
            "hinweis": "" if titel.startswith("Rennleitung") else hinweise.get(rolle, ""),
        }
        for titel, url, rolle, gruppe in links
    ]


def token_rollen(links: list[dict]) -> list[tuple[str, str]]:
    """Knöpfe „Token erneuern“ je Gerätelink (Rennleitung ohne eigenen Link: über den Ziel-Token)."""
    rollen, gesehen = [], set()
    for l in links:
        if l["rolle"] in gesehen:
            if l["titel"].startswith("Rennleitung"):  # gleicher Token wie Ziel
                i = next(i for i, (r, _) in enumerate(rollen) if r == l["rolle"])
                rollen[i] = (
                    l["rolle"],
                    rollen[i][1].replace(" erneuern …", " erneuern (auch Rennleitung) …"),
                )
            continue
        gesehen.add(l["rolle"])
        rollen.append((l["rolle"], f"{l['titel']}-Token erneuern …" if not l["rolle"].startswith("posten-")
                       else f"{l['titel']}: Token erneuern …"))  # fmt: skip
    return rollen


def _druck_zurueck(zurueck: str, vid: int) -> str:
    """Ziel des Buttons „Zurück zum Admin“ in Druckansichten (Standard: Drucken & Export)."""
    return _rueckweg(zurueck, f"/admin/v/{vid}/drucken")


@router.get("/v/{vid}/aushang", response_class=HTMLResponse)
def aushang(request: Request, vid: int, session: SessionDep, _a: AdminDep, zurueck: str = ""):
    """Druckbarer A4-Aushang „Live-Ergebnisse“ mit großem QR-Code."""
    from app.oeffentlich import ergebnisse_url, qr_svg

    v = _veranstaltung(session, vid)
    url = ergebnisse_url(request)
    return templates.TemplateResponse(request, "aushang_ergebnisse.html",
                                      {"v": v, "url": url, "qr": qr_svg(url, 12),
                                       "zurueck": _druck_zurueck(zurueck, vid)})  # fmt: skip


@router.get("/v/{vid}/geraetelinks-druck", response_class=HTMLResponse)
def geraetelinks_druck(request: Request, vid: int, session: SessionDep, _a: AdminDep,
                       zurueck: str = ""):  # fmt: skip
    """A4-Blatt „Gerätelinks mit QR-Codes“ für die Helfer."""
    from app.oeffentlich import qr_svg

    v = _veranstaltung(session, vid)
    monitor = f"{basis_url(request, request.app.state.vertraute_proxys)}/monitor"
    links = [
        *geraetelinks(request, v),
        {"titel": "Zielmonitor", "url": monitor, "qr": qr_svg(monitor, 4)},
    ]
    return templates.TemplateResponse(request, "geraetelinks_druck.html", {
        "v": v, "links": links, "zurueck": _druck_zurueck(zurueck, vid)})  # fmt: skip


def urkunde_moeglich(z) -> bool:
    """Urkunde nur mit gültiger Zeit: nicht bei DNS, DNF, DSQ (auch vorläufig), Konflikt, offen."""
    return z.status == Status.OK and z.fahrzeit_ms is not None and not z.dsq_vorlaeufig


@router.get("/v/{vid}/urkunden", response_class=HTMLResponse)
def urkunden(request: Request, vid: int, session: SessionDep, _a: AdminDep, nr: int | None = None,
             zurueck: str = ""):  # fmt: skip
    """Urkunden als Druckansicht, eine je Seite, A4 quer."""
    from app.strecken import km_text

    v = _veranstaltung(session, vid)
    erg = k.ergebnis(session, v)
    zeilen = [z for z in sorted(erg.zeilen, key=lambda z: z.startnummer) if urkunde_moeglich(z)]
    if nr is not None:
        zeilen = [z for z in zeilen if z.startnummer == nr]
        if not zeilen:
            raise HTTPException(404, "Für diese Startnummer gibt es keine Urkunde.")
    return templates.TemplateResponse(request, "urkunden.html", {
        "v": v, "zeilen": zeilen, "strecken": erg.strecken, "offiziell": erg.offiziell,
        "wertung": erg.wertung, "STUFE_TEXT": STUFE_TEXT, "km_text": km_text,
        "zurueck": _druck_zurueck(zurueck, vid)})  # fmt: skip


@router.get("/v/{vid}/papierprotokoll", response_class=HTMLResponse)
def papierprotokoll(request: Request, vid: int, session: SessionDep, _a: AdminDep,
                    zurueck: str = ""):  # fmt: skip
    """Start- und Zielprotokoll zum Ausdrucken (Rückfallebene ohne Handy/Netz)."""
    v = _veranstaltung(session, vid)
    erg = k.ergebnis(session, v)
    zeilen = sorted(erg.zeilen, key=lambda z: z.startnummer)
    return templates.TemplateResponse(request, "papierprotokoll.html", {
        "v": v, "zeilen": zeilen, "strecken": erg.strecken, "leer": range(10),
        "zurueck": _druck_zurueck(zurueck, vid)})  # fmt: skip


@router.get("/v/{vid}/geraete", response_class=HTMLResponse)
def geraete(request: Request, vid: int, session: SessionDep, _a: AdminDep):
    v = _veranstaltung(session, vid)
    uhren = session.scalars(
        select(GeraetUhr).where(GeraetUhr.veranstaltung_id == v.id).order_by(GeraetUhr.geraet)
    ).all()
    from app.oeffentlich import qr_svg

    monitor = f"{basis_url(request, request.app.state.vertraute_proxys)}/monitor"
    links = geraetelinks(request, v)
    zuletzt: dict[str, int] = {}  # Status je Karte (letzte Meldung der Rolle)
    for u in uhren:
        zuletzt[u.rolle] = max(zuletzt.get(u.rolle, 0), u.gemessen_ms)
    ziele = {geraete_rolle(m): m for m in strecken.orte_von(session, v, "ziel")}
    for l in links:
        l["verbunden_ms"] = zuletzt.get(l["rolle"])
        m = ziele.get(l["rolle"])
        if m is not None and not l["titel"].startswith("Rennleitung"):  # Option je Ziel
            l["ziel_ort"] = SimpleNamespace(id=m.id, name=m.name, standard=m.standard,
                                            getrennt=v.rennleitung_getrennt if m.standard else m.rennleitung_getrennt)  # fmt: skip
    return _seite(request, "admin_geraete.html", v=v, reiter="geraete",
                  monitor={"titel": "Zielmonitor", "url": monitor, "qr": qr_svg(monitor, 4)},
                  links=links, token_rollen=token_rollen(links), freigabe=freigabe_schritte(v),
                  hat_zeiten=_hat_zeiten(session, v),
                  geraete_gruppen=list({l["gruppe"]: [x for x in links if x["gruppe"] == l["gruppe"]] for l in links}.items()),
                  uhren=[uhr_zeile(u, posten_namen(session, v)) for u in uhren])  # fmt: skip


@router.post("/v/{vid}/rennleitung")
def rennleitung_modus(
    request: Request, vid: int, session: SessionDep, _a: AdminDep,
    csrf: Annotated[str, Form()] = "", zusammen: Annotated[str, Form()] = "",
    bestaetigung: Annotated[str, Form()] = "", ort: Annotated[str, Form()] = "",
):  # fmt: skip
    v = _veranstaltung(session, vid)
    return _ausfuehren(request, csrf, f"/admin/v/{vid}/geraete",  # Option auf der Ziel-Karte
                       lambda: k.rennleitung_umschalten(session, v, bool(zusammen),
                                                        bool(bestaetigung), ort))  # fmt: skip


@router.post("/v/{vid}/token")
def token_erneuern(
    request: Request, vid: int, session: SessionDep, _a: AdminDep,
    csrf: Annotated[str, Form()] = "", rolle: Annotated[str, Form()] = "",
):  # fmt: skip
    v = _veranstaltung(session, vid)

    def aktion():
        name = k.token_erneuern(session, v, rolle)
        return f"{name} erneuert. Alte Links funktionieren nicht mehr."

    return _ausfuehren(request, csrf, f"/admin/v/{vid}/geraete", aktion)


# --- Protokoll und Exporte -----------------------------------------------------------


PROTOKOLL_ARTEN = {
    "": ("alle", None), "START": ("Start", {Art.START}), "ZIEL": ("Ziel", {Art.ZIEL}),
    "korrekturen": ("Korrekturen", {Art.ZUORDNUNG, Art.STORNO, Art.MANUELLE_ZEIT, Art.ZEITKORREKTUR}),
    "STATUS": ("Status", {Art.STATUS}), "sonstige": ("Ergebnis und Merkmale", {Art.ERGEBNIS, Art.MERKMAL}),
    "DURCHFAHRT": ("Durchfahrten (Segmente)", {Art.DURCHFAHRT, Art.RUNDE}),
}  # fmt: skip


@router.get("/v/{vid}/protokoll", response_class=HTMLResponse)
def protokoll(request: Request, vid: int, session: SessionDep, _a: AdminDep, nr: str = "",
              art: str = ""):  # fmt: skip
    v = _veranstaltung(session, vid)
    ereignisse, gueltig = protokoll_von(session, v, int(nr) if nr.strip().isdigit() else None)
    arten = PROTOKOLL_ARTEN.get(art, PROTOKOLL_ARTEN[""])[1]
    if arten:
        ereignisse = [e for e in ereignisse if e.art in arten]
    erg = k.ergebnis(session, v)
    return _seite(
        request,
        "admin_protokoll.html",
        v=v,
        reiter="protokoll",
        korrekturen={
            s.uuid: s.korrektur_ms
            for s in [*erg.zielstempel, *erg.durchfahrtstempel]
            if s.korrektur_ms
        },
        nicht_gezaehlt=erg.nicht_gezaehlt,
        mp_namen={m.id: m.name for st in erg.strecken for m in st.messpunkte},
        hier=f"/admin/v/{v.id}/protokoll" + (f"?nr={nr}&art={art}" if nr or art else ""),
        ereignisse=ereignisse,
        gueltig=gueltig,
        nr=nr,
        art=art if art in PROTOKOLL_ARTEN else "",
        PROTOKOLL_ARTEN=PROTOKOLL_ARTEN,
    )


@router.get("/v/{vid}/export/ergebnis.csv")
def export_ergebnis(
    request: Request, vid: int, session: SessionDep, _a: AdminDep, rangliste: str = ""
):
    v = _veranstaltung(session, vid)
    erg = _erg(request, session, v)
    if rangliste:  # Rangliste eines Abschnitts
        from app import ranglisten
        from app.export import rangliste_csv

        r = ranglisten.rangliste(erg, rangliste)
        if r is None:
            raise HTTPException(404, "Rangliste nicht gefunden")
        return _download(rangliste_csv(v, erg, r), f"rangliste-{v.datum.isoformat()}.csv")
    inhalt = ergebnis_csv(v, erg)
    return _download(inhalt, f"ergebnis-{v.datum.isoformat()}.csv")


@router.get("/v/{vid}/export/protokoll.csv")
def export_protokoll(vid: int, session: SessionDep, _a: AdminDep):
    v = _veranstaltung(session, vid)
    return _download(protokoll_csv(v, k.ereignisse_von(session, v)), f"protokoll-{v.datum}.csv")


def _download(inhalt: bytes, dateiname: str) -> Response:
    return Response(
        inhalt,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{dateiname}"'},
    )


# --- Einstellungen und Lebenszyklus -------------------------------------------------------


# Einstellungen mit Reitern: eigene Adresse, eigener Speichern-Button und Hilfe-Anker je Reiter
EINSTELLUNGEN_REITER = {"allgemein": ("Allgemein", "", "einstellungen"),
                        "klassen": ("Klassen & Wertung", "/klassen", "klassen-wertung"),
                        # Früher „Öffentlichkeit & Zielmonitor“ unter /oeffentlichkeit
                        "zielmonitor": ("Zielmonitor und Ergebnisse", "/zielmonitor", "zielmonitor-ergebnisse")}  # fmt: skip


def _einstellungen(request: Request, session: Session, vid: int, tab: str):
    from app import monitor_konfig as mk

    v = _veranstaltung(session, vid)
    daten = {}
    if tab == "klassen":
        daten |= {"regeln": saetze.regeln(session, v), "klassensaetze": saetze.klassensaetze(session),
                  "wertungssaetze": saetze.wertungssaetze(session), **strecken_kontext(session, v)}  # fmt: skip
    if tab == "zielmonitor":
        liste = strecken.strecken_von(session, v)
        daten |= {"spalten": spalten_kontext(liste),
                  **mk.kontext(v, liste, k.ergebnis(session, v).wertung is not None,
                               f"/admin/v/{v.id}/einstellungen/zielmonitor")}  # fmt: skip
    return _seite(request, "admin_einstellungen.html", v=v, reiter="einstellungen", heute=k.heute(),
                  hilfe=EINSTELLUNGEN_REITER[tab][2], tab=tab, REITER=EINSTELLUNGEN_REITER,
                  hat_zeiten=_hat_zeiten(session, v),
                  **daten)  # fmt: skip


@router.get("/v/{vid}/einstellungen", response_class=HTMLResponse)
def einstellungen(request: Request, vid: int, session: SessionDep, _a: AdminDep):
    return _einstellungen(request, session, vid, "allgemein")


@router.get("/v/{vid}/einstellungen/{tab}", response_class=HTMLResponse)
def einstellungen_reiter(request: Request, vid: int, tab: str, session: SessionDep, _a: AdminDep):
    if tab == "strecken":  # Strecken haben eine eigene Seite
        return RedirectResponse(f"/admin/v/{vid}/strecken", status_code=307)
    if tab == "oeffentlichkeit":  # Jetzt „Zielmonitor und Ergebnisse“
        return RedirectResponse(f"/admin/v/{vid}/einstellungen/zielmonitor", status_code=307)
    if tab not in EINSTELLUNGEN_REITER or tab == "allgemein":
        raise HTTPException(404, "Unbekannter Bereich")
    return _einstellungen(request, session, vid, tab)


# --- Seiten „Strecken“ und „Messpunkte“ -----------------------------------------


@router.get("/v/{vid}/strecken", response_class=HTMLResponse)
def strecken_seite(request: Request, vid: int, session: SessionDep, _a: AdminDep):
    v = _veranstaltung(session, vid)
    return _seite(request, "admin_strecken.html", v=v, reiter="strecken", hilfe="strecken",
                  **strecken_kontext(session, v))  # fmt: skip


@router.get("/v/{vid}/orte")
def orte_seite(vid: int, _a: AdminDep):
    return RedirectResponse(f"/admin/v/{vid}/messpunkte", status_code=303)


@router.get("/v/{vid}/messpunkte", response_class=HTMLResponse)
def messpunkte_seite(request: Request, vid: int, session: SessionDep, _a: AdminDep):
    """Messpunkte der Veranstaltung: Start, Zwischenzeit, Ziel mit Gerätestatus."""
    v = _veranstaltung(session, vid)
    kontext = strecken_kontext(session, v)
    zuletzt: dict[str, int] = {}  # Gerät verbunden (letzte Meldung je Rolle)
    for u in session.scalars(select(GeraetUhr).where(GeraetUhr.veranstaltung_id == v.id)):
        zuletzt[u.rolle] = max(zuletzt.get(u.rolle, 0), u.gemessen_ms)
    liste = kontext["strecken"]
    orte = []
    for m in strecken.orte_von(session, v):
        nutzer = [liste[i].name for i in strecken.strecken_am_ort(session, v, m)]
        # Auch Standard-Start/-Ziel sind entfernbar, wenn sie keine Strecke nutzt
        grund = ("es gibt bereits Zeiten" if kontext["strecken_eingefroren"]
                 else "genutzt von " + ", ".join(nutzer) if nutzer else "")  # fmt: skip
        orte.append(SimpleNamespace(id=m.id, name=m.name, art=m.art, standard=m.standard, strecken=nutzer,
                                    genutzt=bool(nutzer),
                                    verbunden_ms=zuletzt.get(geraete_rolle(m)), sperre=grund))  # fmt: skip
    return _seite(request, "admin_messpunkte.html", v=v, reiter="messpunkte", hilfe="messpunkte", orte=orte,
                  ORTSARTEN=strecken.ORTSARTEN, **kontext)  # fmt: skip


def spalten_kontext(liste) -> list[SimpleNamespace]:
    """„Ergebnistabelle“: je Strecke mit Abschnitten deren Zeilen."""
    art = lambda a: "Segment" if a.segment else ("Rundenblock" if a.block else "eigener Abschnitt")
    return [SimpleNamespace(id=s.id, name=s.name, zeilen=[SimpleNamespace(id=a.id, name=a.name, art=art(a),
                                                                          anzeigen=not a.aus) for a in abschnitte])
            for s in liste if (abschnitte := strecken.abschnitte_von(s))]  # fmt: skip


@router.post("/v/{vid}/spalten")
async def spalten_speichern(request: Request, vid: int, session: SessionDep, _a: AdminDep):
    """Ein Speichern für alle Strecken; Felder „anzeigen“ = „Strecken-ID:Abschnitt-ID“."""
    form = await request.form()
    v = _veranstaltung(session, vid)
    ziel = f"/admin/v/{vid}/einstellungen/zielmonitor"
    admin_auth.csrf_pruefen(request, str(form.get("csrf") or ""))
    sichtbar: dict[int, set[str]] = {
        int(x): set() for x in form.getlist("spalten_strecke") if str(x).isdigit()
    }
    for wert in form.getlist("anzeigen"):
        sid, _, aid = str(wert).partition(":")
        if sid.isdigit() and int(sid) in sichtbar:
            sichtbar[int(sid)].add(aid)
    try:
        strecken.anzeigen_speichern(session, v, sichtbar)
    except saetze.SatzFehler as e:
        session.rollback()
        return _zurueck(ziel, fehler=str(e))
    session.commit()
    antwort = _zurueck(ziel, ok="Ergebnistabelle gespeichert.")
    antwort.headers["location"] += "#spalten"
    return antwort


def geraete_rolle(m) -> str:
    """Rolle in GeraetUhr bzw. Gerätekarte zu einem Ort (Standardorte: „start“/„ziel“)."""
    if m.art == "punkt":
        return f"posten-{m.id}"
    return m.art if m.standard else f"{m.art}-{m.id}"


@router.post("/v/{vid}/strecken/zuordnung")
async def zuordnung_speichern(request: Request, vid: int, session: SessionDep, _a: AdminDep):
    """Klassenzuordnung aller Strecken in einer Tabelle: je Klasse eine Strecke."""
    form = await request.form()
    v = _veranstaltung(session, vid)

    def aktion():
        zeilen, zuordnung, mit_tn, offiziell = _strecken_stand(session, v)
        index = {z["id"]: str(i) for i, z in enumerate(zeilen)}
        for schluessel, wert in form.multi_items():
            if schluessel.startswith("klasse:") and str(wert) in index:
                zuordnung[schluessel[len("klasse:") :]] = index[str(wert)]
        hinweise = strecken.strecken_speichern(
            session, v, zeilen, zuordnung, mit_tn, offiziell=offiziell
        )
        return "Klassenzuordnung gespeichert.", hinweise

    return _strecken_aktion(
        request, session, v, str(form.get("csrf") or ""), f"/admin/v/{vid}/strecken", aktion
    )


# --- Strecken: Messpunkte, Strecke hinzufügen, Seite je Strecke -------------------


def _strecken_stand(
    session: Session, v: Veranstaltung
) -> tuple[list[dict], dict[str, str], set[str], bool]:
    """Aktuelle Strecken als Formularzeilen, Zuordnung Klasse → Zeile, Klassen mit Teilnehmern, offiziell."""
    strecken.sicherstellen(session, v)
    liste = strecken.strecken_von(session, v)
    zeilen = [
        {"id": str(s.id), "name": s.name, "laenge": strecken.km_text(s.laenge_km)} for s in liste
    ]
    zuordnung = {kl: str(i) for kl, i in strecken.zuordnung(liste).items()}
    erg = k.ergebnis(session, v)
    return zeilen, zuordnung, {z.klasse for z in erg.zeilen if z.klasse}, erg.offiziell


def _strecken_aktion(
    request: Request, session: Session, v: Veranstaltung, csrf: str, ziel: str, aktion
):
    """Wie _ausfuehren, aber Strecken-Fehler zurückrollen und Hinweise als Warnung melden."""
    admin_auth.csrf_pruefen(request, csrf)
    try:
        meldung, hinweise = aktion()
    except saetze.SatzFehler as e:
        session.rollback()
        return _zurueck(ziel, fehler=str(e))
    return _zurueck(ziel, ok=meldung, warnung="; ".join(hinweise) or None)


def _strecke_index(session: Session, v: Veranstaltung, sid: int) -> tuple[int, object]:
    liste = strecken.strecken_von(session, v)
    i = next((i for i, s in enumerate(liste) if s.id == sid), None)
    if i is None:
        raise HTTPException(404, "Strecke nicht gefunden")
    return i, liste[i]


@router.post("/v/{vid}/orte")
@router.post("/v/{vid}/messpunkte")
async def orte_speichern(request: Request, vid: int, session: SessionDep, _a: AdminDep):
    """Seite „Messpunkte“; die Adresse „/orte“ nimmt weiter an."""
    form = await request.form()
    v = _veranstaltung(session, vid)
    feld = lambda name: str(form.get(name) or "")

    def aktion():
        zeilen = [{"id": feld(f"mp_id_{j}"), "name": feld(f"mp_name_{j}"), "art": feld(f"mp_art_{j}"),
                   "entfernen": bool(feld(f"mp_entfernen_{j}"))}
                  for j in range(int(feld("mp_anzahl") or 0))]  # fmt: skip
        return "Messpunkte gespeichert.", strecken.orte_speichern(session, v, zeilen)

    return _strecken_aktion(request, session, v, feld("csrf"), f"/admin/v/{vid}/messpunkte", aktion)


@router.post("/v/{vid}/strecken/neu")
def strecke_neu(request: Request, vid: int, session: SessionDep, _a: AdminDep,
                csrf: Annotated[str, Form()] = ""):  # fmt: skip
    v = _veranstaltung(session, vid)
    ziel = f"/admin/v/{vid}/strecken"

    def aktion():
        zeilen, zuordnung, mit_tn, offiziell = _strecken_stand(session, v)
        namen = {z["name"].lower() for z in zeilen}
        name = next(
            f"Strecke {n}" for n in range(len(zeilen) + 1, 999) if f"strecke {n}" not in namen
        )
        hinweise = strecken.strecken_speichern(session, v, [*zeilen, {"id": "", "name": name, "laenge": ""}],
                                               zuordnung, mit_tn, offiziell=offiziell)  # fmt: skip
        return (
            f"Strecke „{name}“ angelegt – Klassen unter „Strecken“ → „Klassenzuordnung“ zuordnen.",
            hinweise,
        )

    antwort = _strecken_aktion(request, session, v, csrf, ziel, aktion)
    neu = strecken.strecken_von(session, v)
    if "fehler=" not in antwort.headers["location"] and neu:
        return _zurueck(
            f"/admin/v/{vid}/strecken/{neu[-1].id}", ok=f"Strecke „{neu[-1].name}“ angelegt."
        )
    return antwort


# --- Strecke als Datei speichern und laden ---------------------------------------


@router.get("/v/{vid}/strecken/laden", response_class=HTMLResponse)
def strecke_laden_seite(request: Request, vid: int, session: SessionDep, _a: AdminDep):
    v = _veranstaltung(session, vid)
    ersetzen = request.query_params.get("ersetzen", "")
    return _seite(request, "admin_strecke_laden.html", v=v, reiter="strecken", hilfe="strecke-datei",
                  vorschau=None, inhalt="", ersetzen=ersetzen if ersetzen.isdigit() else "",
                  platz=request.query_params.get("platz", "")[:50],
                  sperre="Die Streckenzuordnung ist eingefroren (es gibt bereits Zeiten)."
                  if v.regeln_json else None)  # fmt: skip


@router.post("/v/{vid}/strecken/laden", response_class=HTMLResponse)
async def strecke_laden_vorschau(request: Request, vid: int, session: SessionDep, _a: AdminDep,
                                 csrf: Annotated[str, Form()] = "", ersetzen: Annotated[str, Form()] = "",
                                 platz: Annotated[str, Form()] = "",
                                 datei: Annotated[UploadFile | None, File()] = None):  # fmt: skip
    from app import strecken_datei as sd

    admin_auth.csrf_pruefen(request, csrf)
    v = _veranstaltung(session, vid)
    roh = await datei.read() if datei is not None and datei.filename else b""
    if not roh:
        return _zurueck(
            f"/admin/v/{vid}/strecken/laden", fehler="Bitte eine Streckendatei (.toml) wählen."
        )
    inhalt = sd._dekodieren(roh)
    vorschau = sd.vorschau(session, v, sd.lesen(roh), int(ersetzen) if ersetzen.isdigit() else None)
    return _seite(request, "admin_strecke_laden.html", v=v, reiter="strecken", hilfe="strecke-datei",
                  vorschau=vorschau, inhalt=inhalt, sperre=vorschau.sperre, ersetzen=ersetzen,
                  platz=platz[:50])  # fmt: skip


@router.post("/v/{vid}/strecken/uebernehmen")
def strecke_uebernehmen(request: Request, vid: int, session: SessionDep, _a: AdminDep,
                        csrf: Annotated[str, Form()] = "", inhalt: Annotated[str, Form()] = "",
                        name: Annotated[str, Form()] = "", ziel: Annotated[str, Form()] = "",
                        modus: Annotated[str, Form()] = "", ersetzen: Annotated[str, Form()] = "",
                        platz: Annotated[str, Form()] = "", bestaetigt: Annotated[str, Form()] = ""):  # fmt: skip
    """Strecke aus der Datei anlegen oder (modus „ersetzen“) eine bestehende ersetzen.
    Gibt es eine ersetzbare Strecke, ist die Wahl Pflicht; „Ersetzen“ nur bestätigt."""
    from app import strecken_datei as sd

    admin_auth.csrf_pruefen(request, csrf)
    v = _veranstaltung(session, vid)
    alt = None
    if ersetzen and modus not in ("ersetzen", "neu"):
        return _zurueck(f"/admin/v/{vid}/strecken/laden", fehler="Bitte wählen: „Bestehende Strecke "
                        "ersetzen“ oder „Unter anderem Namen anlegen“.")  # fmt: skip
    if modus == "ersetzen":
        alt = next((s for s in strecken.strecken_von(session, v) if str(s.id) == ersetzen), None)
        if alt is None:
            return _zurueck(
                f"/admin/v/{vid}/strecken/laden",
                fehler="Die zu ersetzende Strecke gibt es nicht mehr.",
            )
        if bestaetigt != "1":
            return _zurueck(f"/admin/v/{vid}/strecken/laden",
                            fehler="Ersetzen bitte bestätigen: " + sd.ersetzen_verlust(alt))  # fmt: skip
    try:
        _, _, mit_tn, _ = _strecken_stand(session, v)
        entwurf = sd.lesen(inhalt.encode("utf-8"))
        neu = sd.uebernehmen(session, v, entwurf, name, mit_tn, ersetzen=alt)
        if platz and v.einrichtung_schritt:  # Platz im Assistenten – Klassen gleich dazu
            platz_hinweise = strecken.platz_entscheiden(session, v, platz, neu)
            session.commit()
    except saetze.SatzFehler as e:
        session.rollback()
        return _zurueck(f"/admin/v/{vid}/strecken/laden", fehler=str(e))
    rueck = _rueckweg(ziel, "")
    if platz and v.einrichtung_schritt:
        p = next(x for x in strecken.plaetze(session, v) if x.schluessel == platz)
        warnung = "; ".join([x for x in (p.hinweis, *platz_hinweise) if x]) or None
        return _zurueck(rueck or f"/admin/einrichtung/{vid}/2", warnung=warnung,
                        ok=f"Strecke „{neu.name}“ aus der Datei geladen – sie fährt {', '.join(p.klassen)}.")  # fmt: skip
    weiter = f"/admin/v/{vid}/strecken/{neu.id}" + (f"?zurueck={rueck}" if rueck else "")
    hinweis = " ".join(entwurf.hinweise) or None  # Z. B. ignorierte Darstellung
    if alt is not None:
        return _zurueck(
            weiter,
            warnung=hinweis,
            ok=f"Strecke „{neu.name}“ aus der Datei ersetzt – die Klassenzuordnung bleibt.",
        )
    return _zurueck(
        weiter,
        warnung=hinweis,
        ok=f"Strecke „{neu.name}“ aus der Datei übernommen – Klassen unter „Strecken“ → „Klassenzuordnung“ zuordnen.",
    )


@router.get("/v/{vid}/strecken/{sid}/datei")
def strecke_datei(vid: int, sid: int, session: SessionDep, _a: AdminDep):
    from app import strecken_datei as sd

    v = _veranstaltung(session, vid)
    _, s = _strecke_index(session, v, sid)
    sicher = "".join(z if z.isalnum() else "-" for z in s.name).strip("-") or "strecke"
    return Response(sd.strecke_toml(session, v, s).encode("utf-8"), media_type="application/toml; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="strecke-{sicher}.toml"'})  # fmt: skip


@router.get("/v/{vid}/strecken/{sid}", response_class=HTMLResponse)
def strecke_seite(request: Request, vid: int, sid: int, session: SessionDep, _a: AdminDep):
    """Eigene Seite je Strecke: Grunddaten, Streckenverlauf, Auswertungsabschnitte."""
    v = _veranstaltung(session, vid)
    kontext = strecken_kontext(session, v)
    i, s = _strecke_index(session, v, sid)
    liste = kontext["strecken"]
    orte = strecken.orte_von(session, v)
    return _seite(request, "admin_strecke.html", v=v, reiter="strecken", hilfe="strecke", s=s, index=i,
                  startorte=[m for m in orte if m.art == "start"], zielorte=[m for m in orte if m.art == "ziel"],
                  startort=strecken.startort_von(s, orte), zielort=strecken.zielort_von(s, orte),
                  klassen_hier=[kl for kl, j in kontext["zuordnung"].items() if j == i],
                  andere=[x for x in liste if x.id != s.id],
                  abschnitte_hier=[a for a in kontext["abschnitt_zeilen"] if a.zeile == i and not a.segment],
                  uebersicht=next((u for u in kontext["segment_uebersicht"] if u.strecke == s.name), None),
                  verlauf_start=verlauf_start(s, strecken.messpunkte_von(session, v), kontext["strecken_eingefroren"]),
                  abschnitt_texte=abschnitt_texte(s, strecken.messpunkte_von(session, v)), ART_TEXT=sg.ART_TEXT,
                  abschnitt_hinweise=strecken.abschnitt_hinweise(s),
                  ab_optionen=abschnitt_optionen(s, strecken.messpunkte_von(session, v)), **kontext)  # fmt: skip


def abschnitt_optionen(s, mps) -> dict[str, list[tuple[str, str]]]:
    """Auswahl für Anfang und Ende eigener Abschnitte je Art: Wert „Nr:Punkt-Name“."""
    namen = {str(m.id): m.name for m in mps}
    teile = strecken.teile_von(s)
    alle = {sg.START: "Start", sg.ZIEL: "Ziel", **namen}
    return {
        sg.EINMALIG: [
            (f"{nr}:{alle.get(p, p)}", text) for p, nr, text in sg.durchfahrt_optionen(teile, namen)
        ],
        sg.WIEDERKEHREND: [(f"1:{text}", text) for _, text in sg.punkt_optionen(teile, namen)],
    }


def abschnitt_texte(s, mps) -> dict[str, str]:
    """Vorschau in Worten je Auswertungsabschnitt der Strecke."""
    namen, teile = {str(m.id): m.name for m in mps}, strecken.teile_von(s)
    return {a.id: sg.abschnitt_text(a, teile, namen) for a in strecken.abschnitte_von(s)}


def verlauf_start(s, mps, eingefroren: bool) -> dict:
    """Startdaten des Punktfolgen-Editors: Punktfolge mit Messpunkt-Namen und die
    Angaben der abgeleiteten Segmente in Fahrreihenfolge."""
    namen = {sg.START: "Start", sg.ZIEL: "Ziel", **{str(m.id): m.name for m in mps}}
    teile = strecken.teile_von(s)
    einstellungen = {a.segment: a for a in strecken.abschnitte_von(s) if a.segment}
    punkte = [{"block": x.name, "wdh": x.wdh, "punkte": [namen.get(p, p) for p in x.punkte]}
              if isinstance(x, sg.VerlaufBlock) else namen.get(x, x) for x in sg.verlauf_aus_teilen(teile)]  # fmt: skip
    segmente = []
    for t, teil in enumerate(teile):
        for j, x in enumerate(teil.segmente):
            a = einstellungen.get(f"{t}.{j}")
            km = (
                "" if x.km is None else f"{x.km:.2f}".removesuffix("0").replace(".", ",")
            )  # 1,25 bleibt 1,25
            segmente.append({"name": x.name, "km": km,
                             "anzeigen": not (a and a.aus), "vergleich": bool(a and a.vergleich)})  # fmt: skip
    return {
        "punkte": punkte,
        "segmente": segmente,
        "messpunkte": [m.name for m in mps],
        "eingefroren": eingefroren,
    }


def _strecke_formular(form, i: int) -> dict:
    """Felder des Strecken-Editors → Argumente für strecken_speichern (fehlende Reiter bleiben)."""
    feld = lambda name: str(form.get(name) or "")
    teile = abschnitte = None
    if "tl_anzahl" in form:
        teile = [{key: feld(f"tl_{key}_{j}") for key in ("block", "wdh", "name", "von", "nach", "km")}
                 | {"strecke": str(i), "entfernen": bool(feld(f"tl_entfernen_{j}"))}
                 for j in range(int(feld("tl_anzahl") or 0))]  # fmt: skip
    if "ab_anzahl" in form:
        abschnitte = [{key: feld(f"ab_{key}_{j}") for key in ("segment", "block", "id", "name", "von", "nach", "art",
                                                               "von_nr", "nach_nr")}
                      | {key: bool(feld(f"ab_{key}_{j}")) for key in ("vergleich", "aus", "entfernen")
                         if key == "entfernen" or f"ab_{key}_{j}" in form}  # Fehlt = bleibt
                      | {"strecke": str(i)} for j in range(int(feld("ab_anzahl") or 0))]  # fmt: skip
    if "verlauf" in form:  # Punktfolge mit den abgeleiteten Segmenten sg_*_k
        teile, segment_zeilen = _verlauf_lesen(feld("verlauf"), feld, i)
        if abschnitte is not None:
            abschnitte = segment_zeilen + [a for a in abschnitte if not a["segment"]]
    return {"teile": teile, "abschnitte": abschnitte}


def _verlauf_lesen(text: str, feld, i: int) -> tuple[list[dict], list[dict]]:
    """Punktfolge aus dem Editor (JSON mit Messpunkt-Namen) → Segment-Zeilen für strecken_speichern
    und Einstellungen der Segment-Abschnitte (anzeigen, Vergleich)."""
    import json

    try:
        daten = json.loads(text or "{}")
        folge = [sg.VerlaufBlock(str(x.get("block") or "").strip()[:100], int(x.get("wdh") or 1),
                                 tuple(str(p or "").strip() for p in x.get("punkte") or []))
                 if isinstance(x, dict) else str(x or "").strip() for x in daten.get("punkte") or []]  # fmt: skip
    except (ValueError, TypeError, AttributeError) as e:
        raise strecken.StreckenFehler("Der Verlauf ist unlesbar.", "verlauf") from e
    fehler = sg.verlauf_pruefen(folge, {})
    if fehler:
        raise strecken.StreckenFehler(" ".join(fehler), "verlauf")
    if any(not 1 <= x.wdh <= sg.MAX_WDH for x in folge if isinstance(x, sg.VerlaufBlock)):
        raise strecken.StreckenFehler(f"Wiederholungen müssen 1 bis {sg.MAX_WDH} sein.", "verlauf")
    text_von = {sg.START: "Start", sg.ZIEL: "Ziel"}
    zeilen, segmente, k = [], [], 0
    if not folge and not (feld("sg_name_0") or feld("sg_km_0")):
        return [], []  # nur Start und Ziel ohne Segmentangaben: einfache Strecke
    teile = sg.teile_aus_verlauf(folge)
    for t, teil in enumerate(teile):
        for j, s in enumerate(teil.segmente):
            zeilen.append({"teil": t, "block": teil.name, "wdh": str(teil.wdh) if teil.block else "",
                           "name": feld(f"sg_name_{k}"), "von": text_von.get(s.von, s.von),
                           "nach": text_von.get(s.nach, s.nach), "km": feld(f"sg_km_{k}"), "strecke": str(i)})  # fmt: skip
            segmente.append(
                {"segment": f"{t}.{j}", "strecke": str(i)}
            )  # anzeigen/Vergleich bleiben
            k += 1
    return zeilen, segmente


def _fehler_ort(e: saetze.SatzFehler) -> tuple[str, str | None]:
    """Reiter und Feld eines Prüffehlers im Strecken-Editor."""
    bereich = getattr(e, "bereich", None)
    if bereich:
        return bereich, None
    text = str(e)
    if "Komoot" in text or "https" in text:
        return "grunddaten", "s-komoot"
    return "grunddaten", "s-laenge" if "Länge" in text or "Streckenlänge" in text else "s-name"


@router.post("/v/{vid}/strecken/{sid}")
async def strecke_alles_speichern(
    request: Request, vid: int, sid: int, session: SessionDep, _a: AdminDep
):
    """Strecken-Editor: „Alles speichern“ übernimmt Grunddaten, Verlauf und
    Auswertungsabschnitte auf einmal. Mit „Accept: application/json“ (Editor) kommt die Antwort als
    JSON mit Reiter und Feld des Fehlers, sonst als Weiterleitung."""
    form = await request.form()
    admin_auth.csrf_pruefen(request, str(form.get("csrf") or ""))
    v = _veranstaltung(session, vid)
    i, s = _strecke_index(session, v, sid)
    feld = lambda name: str(form.get(name) or "")
    rueck = _rueckweg(feld("zurueck"), "")  # aufrufende Seite (z. B. Assistent) bleibt erhalten
    ziel = f"/admin/v/{vid}/strecken/{sid}" + (f"?zurueck={rueck}" if rueck else "")
    json_antwort = "application/json" in request.headers.get("accept", "")
    try:
        from app.strecken_datei import komoot_pruefen

        zeilen, zuordnung, mit_tn, offiziell = _strecken_stand(session, v)
        komoot = komoot_pruefen(feld("komoot")) if "komoot" in form else s.komoot_url
        strecken.strecke_orte_setzen(session, v, s, form.get("startort"), form.get("zielort"))
        if "name" in form:
            zeilen[i] |= {"name": feld("name"), "laenge": feld("laenge")}
        hinweise = strecken.strecken_speichern(session, v, zeilen, zuordnung, mit_tn, offiziell=offiziell,
                                               nur={i}, **_strecke_formular(form, i))  # fmt: skip
        if "beschreibung" in form:
            s.beschreibung = feld("beschreibung").strip()[:2000] or None
        s.komoot_url = komoot
        session.commit()
    except saetze.SatzFehler as e:
        session.rollback()
        if json_antwort:
            reiter, feldname = _fehler_ort(e)
            return JSONResponse({"ok": False, "fehler": str(e), "reiter": reiter, "feld": feldname})
        return _zurueck(ziel, fehler=str(e))
    antwort = _zurueck(ziel, ok="Strecke gespeichert.", warnung="; ".join(hinweise) or None)
    if json_antwort:
        return JSONResponse({"ok": True, "ziel": antwort.headers["location"]})
    return antwort


@router.post("/v/{vid}/strecken/{sid}/entfernen")
def strecke_entfernen(request: Request, vid: int, sid: int, session: SessionDep, _a: AdminDep,
                      csrf: Annotated[str, Form()] = "", zurueck: Annotated[str, Form()] = "",
                      klassen_an: Annotated[str, Form()] = ""):  # fmt: skip
    """Strecke entfernen; hat sie Klassen, übernimmt sie die ausdrücklich gewählte Strecke."""
    v = _veranstaltung(session, vid)
    i, s = _strecke_index(session, v, sid)
    rueck = _rueckweg(zurueck, "")
    ziel = f"/admin/v/{vid}/strecken/{sid}" + (f"?zurueck={rueck}" if rueck else "")
    name = s.name
    meldung = f"Strecke „{name}“ entfernt."

    def aktion():
        nonlocal meldung
        zeilen, zuordnung, mit_tn, offiziell = _strecken_stand(session, v)
        if len(zeilen) == 1:
            raise saetze.SatzFehler(
                "Die letzte Strecke einer Veranstaltung bleibt – jede Klasse braucht eine Strecke."
            )
        if s.klassen:  # keine stillschweigende Umverteilung: Ziel ausdrücklich gewählt
            j = next((j for j, z in enumerate(zeilen) if z["id"] == klassen_an and j != i), None)
            if j is None:
                raise saetze.SatzFehler(
                    f"Bitte wählen, welche Strecke die Klassen von „{name}“ übernimmt."
                )
            for kl, wert in zuordnung.items():
                if wert == str(i):
                    zuordnung[kl] = str(j)
            meldung = f"Strecke „{name}“ entfernt – ihre Klassen fährt jetzt „{zeilen[j]['name']}“."
        zeilen[i]["entfernen"] = True
        hinweise = strecken.strecken_speichern(
            session, v, zeilen, zuordnung, mit_tn, offiziell=offiziell
        )
        return meldung, hinweise

    antwort = _strecken_aktion(request, session, v, csrf, ziel, aktion)
    if "fehler=" in antwort.headers["location"]:
        return antwort
    return _zurueck(rueck or f"/admin/v/{vid}/strecken", ok=meldung)


@router.post("/v/{vid}/oeffentlichkeit")
def oeffentlichkeit_speichern(request: Request, vid: int, session: SessionDep, _a: AdminDep,
                              csrf: Annotated[str, Form()] = "",
                              zwischenzeiten_oeffentlich: Annotated[str, Form()] = ""):  # fmt: skip
    v = _veranstaltung(session, vid)

    def aktion():
        v.zwischenzeiten_oeffentlich = bool(zwischenzeiten_oeffentlich)
        session.commit()
        return "Gespeichert."

    return _ausfuehren(request, csrf, f"/admin/v/{vid}/einstellungen/zielmonitor", aktion)


def strecken_kontext(session: Session, v: Veranstaltung) -> dict:
    """Daten für den Formularteil „Strecken & Zuordnung“ (Reiter und Assistent)."""
    strecken.sicherstellen(session, v)
    session.commit()
    r = saetze.regeln(session, v)
    erg = k.ergebnis(session, v)
    anzahl: dict[str, int] = {}
    for z in erg.zeilen:
        anzahl[z.klasse] = anzahl.get(z.klasse, 0) + 1
    liste = strecken.strecken_von(session, v)
    mps = strecken.messpunkte_von(session, v)
    messpunkte = [SimpleNamespace(id=m.id, name=m.name,
                                  strecken=[liste[i].name for i in strecken.nutzende_strecken(liste, m.id)])
                  for m in mps]  # fmt: skip
    return {
        "strecken": liste, "zuordnung": strecken.zuordnung(liste), "messpunkte": messpunkte,
        "segment_uebersicht": segment_uebersicht(liste, mps, strecken.orte_von(session, v)),
        "abschnitt_zeilen": abschnitt_zeilen(liste, mps),
        "mit_segmenten": {s.id for s in liste if strecken.teile_von(s)},
        "zuordnungsklassen": strecken.zuordnungsklassen(r.klassen), "klassen_anzahl": anzahl,
        "strecken_eingefroren": bool(v.regeln_json), "strecken_offiziell": erg.offiziell,
        "km_text": strecken.km_text, "FARBEN": strecken.FARBEN,
    }  # fmt: skip


def abschnitt_zeilen(liste, mps) -> list[SimpleNamespace]:
    """Formularzeilen der Auswertungsabschnitte aller Strecken (Segment-Abschnitte zuerst)."""
    namen = {sg.START: "Start", sg.ZIEL: "Ziel", **{str(m.id): m.name for m in mps}}
    return [SimpleNamespace(zeile=i, id=a.id, segment=a.segment, block=a.block, name=a.name, von=namen.get(a.von, a.von),
                            nach=namen.get(a.nach, a.nach), art=a.art, von_nr=a.von_nr, nach_nr=a.nach_nr,
                            vergleich=a.vergleich, aus=a.aus)
            for i, s in enumerate(liste) for a in strecken.abschnitte_von(s)]  # fmt: skip


def segment_uebersicht(liste, mps, orte=()) -> list[SimpleNamespace]:
    """Je Strecke mit Segmenten: erwartete Abfolge (mit Start- und Zielort) und Durchfahrten je Messpunkt."""
    aus = []
    for s in liste:
        teile = strecken.teile_von(s)
        if not teile:
            continue
        namen = {str(m.id): m.name for m in mps}
        if orte:  # Namen von Start- und Zielort statt „Start“/„Ziel“
            namen |= {
                sg.START: strecken.startort_von(s, orte).name,
                sg.ZIEL: strecken.zielort_von(s, orte).name,
            }
        anzahl = sg.erwartete_anzahl(teile)
        aus.append(SimpleNamespace(strecke=s.name, abfolge=sg.abfolge_text(teile, namen),
                                   anzahl=[(m.name, anzahl[str(m.id)]) for m in mps if str(m.id) in anzahl]))  # fmt: skip
    return aus


def regeln_aus_formular(session: Session, v: Veranstaltung, form) -> tuple[list[str], bool]:
    """Klassensatz, Wertungssatz, Strecken und Zuordnung aus dem Formular übernehmen.

    Gibt (Hinweise, neu vorgeschlagen) zurück. Ändert sich Klassen- oder Wertungssatz (vor dem
    Einfrieren), werden die Strecken aus dem Wertungssatz neu vorgeschlagen und die übermittelten
    Strecken nicht übernommen.
    """
    feld = lambda name: str(form.get(name) or "")
    ks = int(feld("klassensatz_id")) if feld("klassensatz_id").isdigit() else v.klassensatz_id
    ws = int(feld("wertungssatz_id")) if feld("wertungssatz_id").isdigit() else None
    if feld("zwischenzeiten_formular"):  # Segmente: Anzeige
        v.zwischenzeiten_oeffentlich = bool(feld("zwischenzeiten_oeffentlich"))
    neu = not v.regeln_json and (ks != v.klassensatz_id or ws != v.wertungssatz_id)
    mit_strecken = not neu and feld("strecken_anzahl").isdigit()
    hinweise = saetze.regeln_setzen(session, v, ks, ws, saetze.strecke_parsen(feld("strecke_km")),
                                    abschliessen=not mit_strecken,
                                    laengen_verwerfen=bool(feld("laengen_verwerfen")))  # fmt: skip
    if not mit_strecken:
        return hinweise, neu
    zeilen = [{"id": feld(f"strecke_id_{i}"), "name": feld(f"strecke_name_{i}"),
               "laenge": feld(f"strecke_laenge_{i}"), "entfernen": bool(feld(f"strecke_entfernen_{i}"))}
              for i in range(int(feld("strecken_anzahl")))]  # fmt: skip
    messpunkte = teile = None
    if feld("mp_anzahl").isdigit():
        messpunkte = [{"id": feld(f"mp_id_{j}"), "name": feld(f"mp_name_{j}"),
                       "entfernen": bool(feld(f"mp_entfernen_{j}"))}
                      for j in range(int(feld("mp_anzahl")))]  # fmt: skip
    abschnitte = None
    if feld("ab_anzahl").isdigit():  # Auswertungsabschnitte
        abschnitte = [{key: feld(f"ab_{key}_{j}") for key in ("strecke", "segment", "block", "id", "name", "von", "nach", "art",
                                                               "von_nr", "nach_nr")}
                      | {key: bool(feld(f"ab_{key}_{j}")) for key in ("vergleich", "aus", "entfernen")
                         if key == "entfernen" or f"ab_{key}_{j}" in form}  # Fehlt = bleibt
                      for j in range(int(feld("ab_anzahl")))]  # fmt: skip
    if feld("tl_anzahl").isdigit():
        teile = [{key: feld(f"tl_{key}_{j}") for key in ("strecke", "block", "wdh", "name", "von", "nach", "km")}
                 | {"vergleich": bool(feld(f"tl_vergleich_{j}")), "entfernen": bool(feld(f"tl_entfernen_{j}"))}
                 for j in range(int(feld("tl_anzahl")))]  # fmt: skip
    zuordnung = {feld(f"zuordnung_klasse_{j}"): feld(f"zuordnung_strecke_{j}")
                 for j in range(int(feld("zuordnung_anzahl") or 0))}  # fmt: skip
    erg = k.ergebnis(session, v)
    hinweise += strecken.strecken_speichern(
        session,
        v,
        zeilen,
        zuordnung,
        {z.klasse for z in erg.zeilen if z.klasse},
        offiziell=erg.offiziell,
        messpunkte=messpunkte,
        teile=teile,
        abschnitte=abschnitte,
    )
    return hinweise, False


@router.post("/v/{vid}/stammdaten")
def stammdaten(
    request: Request, vid: int, session: SessionDep, _a: AdminDep,
    csrf: Annotated[str, Form()] = "", name: Annotated[str, Form()] = "",
    datum: Annotated[str, Form()] = "", anonymisieren_ab: Annotated[str, Form()] = "",
):  # fmt: skip
    v = _veranstaltung(session, vid)

    def aktion():
        k.stammdaten_speichern(session, v, name, _datum(datum),
                               _datum(anonymisieren_ab, "Anonymisieren ab"))  # fmt: skip
        return "Gespeichert."

    return _ausfuehren(request, csrf, f"/admin/v/{vid}/einstellungen", aktion)


@router.post("/v/{vid}/regeln")
async def regeln_speichern(request: Request, vid: int, session: SessionDep, _a: AdminDep):
    form = await request.form()
    admin_auth.csrf_pruefen(request, str(form.get("csrf") or ""))
    v = _veranstaltung(session, vid)
    ziel = str(form.get("ziel") or "")
    standard = "strecken" if form.get("strecken_anzahl") else "einstellungen/klassen"
    zurueck = ziel if ziel.startswith("/admin/") else f"/admin/v/{vid}/{standard}"
    try:
        hinweise, _ = regeln_aus_formular(session, v, form)
    except saetze.SatzFehler as e:
        session.rollback()
        return _zurueck(zurueck, fehler=str(e))
    meldung = (
        "Klassen, Leistungswertung und Strecken gespeichert."
        if v.wertungssatz_id
        else "Klassen und Strecken gespeichert (Leistungswertung ausgeschaltet)."
    )
    return _zurueck(zurueck, ok=meldung, warnung="; ".join(hinweise) or None)


@router.post("/v/{vid}/aktivieren")
def aktivieren(request: Request, vid: int, session: SessionDep, _a: AdminDep,
               csrf: Annotated[str, Form()] = ""):  # fmt: skip
    v = _veranstaltung(session, vid)

    def aktion():
        k.aktiv_setzen(session, v)
        return f"„{v.name}“ ist jetzt die aktive Veranstaltung."

    return _ausfuehren(request, csrf, f"/admin/v/{vid}/status", aktion)


@router.post("/v/{vid}/deaktivieren")
def deaktivieren(request: Request, vid: int, session: SessionDep, _a: AdminDep,
                 csrf: Annotated[str, Form()] = ""):  # fmt: skip
    """„Nicht mehr aktiv setzen …“: nur ohne Startzeit; die Geräte sperren sich wieder."""
    v = _veranstaltung(session, vid)

    def aktion():
        k.aktiv_beenden(session, v)
        return f"„{v.name}“ ist nicht mehr aktiv – die Geräte sind wieder gesperrt."

    return _ausfuehren(request, csrf, f"/admin/v/{vid}/status", aktion)


def _doppelt_bestaetigt(v: Veranstaltung, bestaetigung: str, name: str) -> None:
    if not bestaetigung or name.strip() != v.name:
        raise k.KorrekturFehler("Bitte zur Bestätigung den Namen der Veranstaltung eintippen.")


@router.post("/v/{vid}/abschliessen")
def abschliessen(request: Request, vid: int, session: SessionDep, _a: AdminDep,
                 csrf: Annotated[str, Form()] = "", bestaetigung: Annotated[str, Form()] = "",
                 name: Annotated[str, Form()] = ""):  # fmt: skip
    v = _veranstaltung(session, vid)

    def aktion():
        _doppelt_bestaetigt(v, bestaetigung, name)  # nicht umkehrbar: Namen eintippen
        k.abschliessen(session, v)
        return "Veranstaltung abgeschlossen. Gerätelinks sind ungültig."

    return _ausfuehren(request, csrf, f"/admin/v/{vid}/status", aktion)


@router.post("/v/{vid}/anonymisieren")
def anonymisieren(
    request: Request, vid: int, session: SessionDep, _a: AdminDep,
    csrf: Annotated[str, Form()] = "", bestaetigung: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
):  # fmt: skip
    v = _veranstaltung(session, vid)

    def aktion():
        _doppelt_bestaetigt(v, bestaetigung, name)
        k.anonymisieren(session, v)
        return "Veranstaltung anonymisiert."

    return _ausfuehren(request, csrf, f"/admin/v/{vid}/status", aktion)


@router.post("/v/{vid}/loeschen")
def loeschen(
    request: Request, vid: int, session: SessionDep, _a: AdminDep,
    csrf: Annotated[str, Form()] = "", bestaetigung: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
):  # fmt: skip
    admin_auth.csrf_pruefen(request, csrf)
    v = _veranstaltung(session, vid)
    try:
        _doppelt_bestaetigt(v, bestaetigung, name)
    except k.KorrekturFehler as e:
        return _zurueck(f"/admin/v/{vid}/status", fehler=str(e))
    titel = v.name
    k.loeschen(session, v)
    return _zurueck("/admin", ok=f"Veranstaltung „{titel}“ komplett gelöscht.")
