"""Öffentliche Seiten ohne Token (E6, Phase 7): Ergebnisse, Druckansicht, CSV, Zielmonitor.

Es werden nur Startnummer, Name, Verein, Klasse, Zeit, Rang, Status und ggf. Abzeichen
herausgegeben (kein Jahrgang, kein Geschlecht, keine Tokens).
"""

import segno
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

from app import korrekturen as k
from app.auswertung import Ergebnis, ErgebnisZeile, Status, StreckenDef
from app.auth import SessionDep
from app.export import ergebnis_csv, ergebnis_kopf, rangliste_csv, segment_text, stufe_text
from app.models import Veranstaltung, aktive_veranstaltung
from app.proxy import basis_url
from app.ranglisten import rangliste, verfuegbar
from app.segmente import abschnitt_labels
from app.seiten import APP_VERSION, templates
from app.strecken import FARBEN

router = APIRouter(include_in_schema=False)

LETZTE = 5
NAECHSTE = 5
ENDSTATUS = (Status.DNF, Status.DSQ, Status.DNS)
# In Ergebnistabellen: gewertete, in Prüfung befindliche und DNF/DSQ/DNS (unten);
# Fahrer unterwegs bzw. noch nicht gestartet stehen im Monitor in eigenen Bereichen.
IN_TABELLE = (Status.OK, Status.KONFLIKT, *ENDSTATUS)
STATUS_OEFFENTLICH = {
    Status.OK: "",
    Status.KONFLIKT: "in Prüfung",
    Status.DNF: "DNF",
    Status.DSQ: "DSQ",
    Status.DNS: "DNS",
    Status.UNVOLLSTAENDIG: "unterwegs",
    Status.OFFEN: "nicht gestartet",
}


def _status_text(z: ErgebnisZeile) -> str:
    teile = []
    if z.ausser_konkurrenz and z.status == Status.OK:
        teile.append("a. K.")
    if z.dsq_vorlaeufig:
        teile.append("unter Vorbehalt")
    return " · ".join(teile) if teile else STATUS_OEFFENTLICH[z.status]


def strecke_text(sd: StreckenDef) -> str:
    """Gemessene Länge als Text, z. B. „20,0 km“ (leer ohne Länge)."""
    return f"{sd.zehntel_km / 10:.1f} km".replace(".", ",") if sd.zehntel_km else ""


def _zeile(z: ErgebnisZeile, mit_wertung: bool = False, erg: Ergebnis | None = None,
           zwischen: bool = False) -> dict:  # fmt: skip
    name = f"{z.vorname} {z.nachname}".strip() or "–"
    return {
        "startnummer": z.startnummer,
        "name": name,
        "verein": z.verein or "",
        "klasse": z.klasse or "",
        "zeit": z.fahrzeit_text,
        "rang": z.rang_gesamt,
        "rang_klasse": z.rang_klasse,
        "status": z.status.value,
        "status_text": _status_text(z),
        "vorbehalt": z.dsq_vorlaeufig is not None,
        "ausser_konkurrenz": z.ausser_konkurrenz,
        "stufe": z.stufe,
        "stufe_text": stufe_text(z) if mit_wertung else "",
        "start_ms": z.start_ms,
        "ziel_ms": z.ziel_ms,
        "strecke": erg.strecken[z.strecke].name if erg and erg.mehrere_strecken else "",
        "farbe": z.strecke % FARBEN,
        # Segmentzeiten je Segment-Durchfahrt der eigenen Strecke, nur wenn öffentlich
        "segmente": [segment_text(z, z.strecke, j) for j in range(len(z.abschnittszeiten))]
        if zwischen
        else [],
    }


def _sekunden(ms: int) -> str:
    s = round(ms / 1000)
    h, m, sek = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{sek:02d}" if h else f"{m}:{sek:02d}"


def letzte_abschnittszeit(z: ErgebnisZeile, abschnitt: str, vergleich: bool = False) -> dict | None:
    """Letzte vorhandene Zeit eines Abschnitts für den Monitor (ganze Sekunden); ist am Feld „mit
    Pfeil und Differenz“ gewählt (Segmente 52), dazu der Vergleich mit der vorherigen Durchfahrt
    (▼ schneller, ▲ langsamer)."""
    x = next(
        (
            x
            for x in reversed(z.abschnittszeiten)
            if x.schluessel == abschnitt and x.zeit_ms is not None
        ),
        None,
    )
    if x is None:
        return None
    d = {"label": x.label, "zeit": _sekunden(x.zeit_ms), "pfeil": "", "diff": ""}
    if vergleich and x.vergleich_ms is not None and round(x.vergleich_ms / 1000):
        d["pfeil"] = "▼" if x.vergleich_ms < 0 else "▲"
        d["diff"] = f"{abs(round(x.vergleich_ms / 1000))} s"
    return d


def beste_abschnittszeit(z: ErgebnisZeile, live: list[dict]) -> dict | None:
    """Segmente 72 (A178): im Ergebnis des Zielmonitors die beste Zeit des ersten gewählten
    Abschnittsfelds der eigenen Strecke mit mehreren Durchfahrten, z. B. „beste Runde 9:18“."""
    feld = next((x for x in live if x["strecke"] == z.strecke and x.get("mehrfach")), None)
    if feld is None:
        return None
    zeiten = [
        x.zeit_ms
        for x in z.abschnittszeiten
        if x.schluessel == feld["abschnitt"] and x.zeit_ms is not None
    ]
    if not zeiten:
        return None
    return {"label": f"beste {feld['name']}", "zeit": _sekunden(min(zeiten))}


def _ist_unterwegs(z: ErgebnisZeile) -> bool:
    return z.start_ms is not None and z.ziel_ms is None and z.status not in ENDSTATUS


ORDNUNG_G = {"w": 0, "m": 1, "d": 2}


def _ordnung(strecke: int, klasse: str | None, info: dict | None) -> list:
    g, alter = (info or {}).get(klasse, ("d", 999))
    return [strecke, ORDNUNG_G.get(g, 3), alter, klasse or ""]


def oeffentlicher_stand(v: Veranstaltung, erg: Ergebnis, klassenreihenfolge: list[str],
                        klasseninfo: dict[str, tuple[str, int]] | None = None,
                        monitor: dict | None = None) -> dict:  # fmt: skip
    zeilen = erg.zeilen
    w = erg.wertung is not None
    tabelle = [z for z in zeilen if z.status in IN_TABELLE]
    reihenfolge = {name: i for i, name in enumerate(klassenreihenfolge)}

    zwischen = bool(v.zwischenzeiten_oeffentlich) and erg.mit_durchfahrten
    monitor = monitor or {"seiten": [{"typ": "ergebnis", "dauer": 10, "live": True, "abzeichen": w,
                                      "strecken": []}], "live": []}  # fmt: skip

    def zeile(z: ErgebnisZeile) -> dict:
        return _zeile(z, w, erg, zwischen)

    def unterwegs_zeile(z: ErgebnisZeile) -> dict:
        d = zeile(z)  # Live-Bereich (Segmente 14): je ausgewähltem Abschnitt die letzte Zeit
        d["abschnitte"] = {x["id"]: letzte_abschnittszeit(z, x["abschnitt"], x.get("vergleich", False))
                           for x in monitor["live"] if x["strecke"] == z.strecke}  # fmt: skip
        return d

    def ergebnis_zeile(z: ErgebnisZeile) -> dict:
        return {**zeile(z), "beste": beste_abschnittszeit(z, monitor["live"])}

    strecken = []  # Phase 13: Gesamt- und Klassenlisten je Strecke
    for i, sd in enumerate(erg.strecken):
        auf = [z for z in tabelle if z.strecke == i]
        segmente = (
            abschnitt_labels(sd.sichtbare_abschnitte, sd.teile)
            if zwischen and sd.mit_durchfahrten
            else []
        )
        namen = sorted({z.klasse for z in auf}, key=lambda n: (reihenfolge.get(n, 999), n or ""))
        strecken.append({
            "name": sd.name, "laenge": strecke_text(sd), "farbe": i % FARBEN, "segmente": segmente,
            "gesamt": [zeile(z) for z in auf],
            "klassen": [{"name": n or "ohne Klasse", "strecke": sd.name if erg.mehrere_strecken else "",
                         "farbe": i % FARBEN, "segmente": segmente,
                         # Reihenfolge im Zielmonitor: Strecke, Damen vor Herren, Alter aufsteigend
                         "ordnung": _ordnung(i, n, klasseninfo),
                         "zeilen": [ergebnis_zeile(z) for z in auf if z.klasse == n]}
                        for n in namen],
        })  # fmt: skip
    unterwegs = sorted((z for z in zeilen if _ist_unterwegs(z)), key=lambda z: z.start_ms)
    nicht_gestartet = sorted(
        (z for z in zeilen if z.status == Status.OFFEN), key=lambda z: z.startnummer
    )
    im_ziel = sorted(
        (z for z in zeilen if z.ziel_ms is not None and z.status in (Status.OK, Status.KONFLIKT)),
        key=lambda z: z.ziel_ms,
        reverse=True,
    )
    return {
        "veranstaltung": {"name": v.name, "datum": v.datum.strftime("%d.%m.%Y")},
        "kopf": ergebnis_kopf(v, erg),
        "offiziell": erg.offiziell,
        "wertung": erg.wertung.name if erg.wertung else None,
        "zaehlung": {
            "gestartet": sum(
                1 for z in zeilen if z.start_ms is not None and z.status != Status.DNS
            ),
            "unterwegs": len(unterwegs),
            "im_ziel": sum(1 for z in zeilen if z.ziel_ms is not None),
            "dns": sum(1 for z in zeilen if z.status == Status.DNS),
            "dnf": sum(1 for z in zeilen if z.status == Status.DNF),
            "dsq": sum(1 for z in zeilen if z.status == Status.DSQ),
        },
        "mehrere_strecken": erg.mehrere_strecken,
        "zwischenzeiten": zwischen,
        "monitor": _mit_ranglisten(monitor, erg),
        "ranglisten": [{"schluessel": k, "titel": t} for k, t in verfuegbar(erg)]
        if zwischen
        else [],
        "strecken": strecken,
        "letzte": [zeile(z) for z in im_ziel[:LETZTE]],
        "unterwegs": [unterwegs_zeile(z) for z in unterwegs],
        "nicht_gestartet": {
            "anzahl": len(nicht_gestartet),
            "naechste": [z.startnummer for z in nicht_gestartet[:NAECHSTE]],
        },
        "gesamt": [zeile(z) for z in tabelle],  # alle Strecken; Ränge gelten je Strecke
        "klassen": [k for st in strecken for k in st["klassen"]],
        "server_ms": k.jetzt_ms(),
    }


def rangliste_dict(r, top: int | None = None) -> dict:
    zeilen = r.zeilen[:top] if top else r.zeilen
    return {"schluessel": r.schluessel, "titel": r.titel,
            "zeilen": [{"rang": z.rang, "startnummer": z.startnummer, "name": z.name, "verein": z.verein,
                        "klasse": z.klasse, "zeit": z.zeit, "durchfahrt": z.durchfahrt,
                        "ausser_konkurrenz": z.ausser_konkurrenz, "status": z.status} for z in zeilen]}  # fmt: skip


def _mit_ranglisten(monitor: dict, erg: Ergebnis) -> dict:
    """Monitor-Seiten „Rangliste Abschnitt“ bekommen ihre Top-N-Zeilen (Segmente 15)."""
    seiten = []
    for s in monitor["seiten"]:
        if s["typ"] == "rangliste":
            r = rangliste(erg, f"{s['strecke']}:{s['abschnitt']}")
            if r is None:
                continue
            s = {**s, "zeilen": rangliste_dict(r, s.get("top"))["zeilen"]}
        seiten.append(s)
    return {**monitor, "seiten": seiten or [{"typ": "ergebnis", "dauer": 10, "live": True,
                                              "abzeichen": erg.wertung is not None, "strecken": []}]}  # fmt: skip


def _gewaehlte_rangliste(erg: Ergebnis | None, daten: dict | None, schluessel: str) -> dict | None:
    """Öffentlich nur, wenn Segmentzeiten öffentlich sind."""
    if not schluessel or erg is None or not daten or not daten.get("zwischenzeiten"):
        return None
    r = rangliste(erg, schluessel)
    return rangliste_dict(r) if r else None


KEINE_ERGEBNISSE = "Für diese Veranstaltung gibt es noch keine Ergebnisse."


def _hat_zeiten(erg: Ergebnis) -> bool:
    return bool(erg.startstempel or erg.zielstempel) or any(
        z.start_ms is not None for z in erg.zeilen
    )


def _stand(
    request: Request, session, veranstaltung: int | None = None
) -> tuple[Veranstaltung | None, Ergebnis | None, dict | None]:
    """Aktive Veranstaltung oder (optional) eine bestimmte, z. B. für den PDF-Export im Admin."""
    v = (
        session.get(Veranstaltung, veranstaltung)
        if veranstaltung
        else aktive_veranstaltung(session)
    )
    if v is None:
        return None, None, None
    erg = k.ergebnis(session, v)
    vorschau = request.query_params.get("vorschau") == "1"  # Links aus dem Admin
    if v.laufend and v.freigabe_fehlt and not _hat_zeiten(erg) and not vorschau:
        # Segmente 46: vor der Freigabe ohne Zeiten nichts zeigen (Admin-Vorschau bleibt möglich)
        return v, None, {"veranstaltung": {"name": v.name, "datum": v.datum.strftime("%d.%m.%Y")},
                         "keine_ergebnisse": KEINE_ERGEBNISSE}  # fmt: skip
    klassen = k.klassen_von(session, v)
    reihenfolge = [kl.name for kl in klassen] + ["Offen"]
    info = {kl.name: (kl.geschlecht, kl.alter_von) for kl in klassen} | {"Offen": ("d", 0)}
    from app import monitor_konfig
    from app import strecken as st

    liste = st.strecken_von(session, v)
    monitor = monitor_konfig.fuer_monitor(monitor_konfig.laden(v, liste), liste, erg)
    return v, erg, oeffentlicher_stand(v, erg, reihenfolge, info, monitor)


def _rechtliche_seite(request: Request, session, art: str):
    """Release 3: /impressum, /datenschutz – Text als eigene Seite, Link → Weiterleitung, sonst 404."""
    from fastapi.responses import RedirectResponse

    from app import installation

    werte = installation.lesen(session)
    if werte.get(f"{art}_url"):
        return RedirectResponse(werte[f"{art}_url"], status_code=307)
    text = werte.get(f"{art}_text") or ""
    if not text:
        return HTMLResponse("Nicht eingerichtet", status_code=404)
    absaetze = [a.split("\n") for a in text.split("\n\n") if a.strip()]
    return templates.TemplateResponse(request, "rechtliches.html",
                                      {"titel": installation.RECHTLICHES[art], "absaetze": absaetze})  # fmt: skip


@router.get("/impressum", response_class=HTMLResponse)
def impressum(request: Request, session: SessionDep):
    return _rechtliche_seite(request, session, "impressum")


@router.get("/datenschutz", response_class=HTMLResponse)
def datenschutz(request: Request, session: SessionDep):
    return _rechtliche_seite(request, session, "datenschutz")


@router.get("/api/oeffentlich")
def api_oeffentlich(
    request: Request, response: Response, session: SessionDep, veranstaltung: int | None = None
) -> dict:
    _, _, daten = _stand(request, session, veranstaltung)
    # Segmente 62: der Monitor lädt sich neu, wenn eine neue App-Version läuft (sonst zeigt ein vor
    # dem Update geöffneter Monitor neue Daten mit altem Code)
    response.headers["X-App-Version"] = APP_VERSION
    return daten or {"veranstaltung": None}


@router.get("/ergebnisse", response_class=HTMLResponse)
def ergebnisse(
    request: Request, session: SessionDep, veranstaltung: int | None = None, rangliste: str = ""
):
    _, erg, daten = _stand(request, session, veranstaltung)
    return templates.TemplateResponse(
        request,
        "ergebnisse.html",
        {
            "d": daten,
            "rl": _gewaehlte_rangliste(erg, daten, rangliste),
            "veranstaltung": veranstaltung,
        },
    )


@router.get("/ergebnisse/inhalt", response_class=HTMLResponse)
def ergebnisse_inhalt(request: Request, session: SessionDep, veranstaltung: int | None = None,
                      rangliste: str = ""):  # fmt: skip
    _, erg, daten = _stand(request, session, veranstaltung)
    return templates.TemplateResponse(
        request,
        "_ergebnisse_inhalt.html",
        {
            "d": daten,
            "rl": _gewaehlte_rangliste(erg, daten, rangliste),
            "veranstaltung": veranstaltung,
        },
    )


@router.get("/ergebnisse/druck", response_class=HTMLResponse)
def ergebnisse_druck(request: Request, session: SessionDep, veranstaltung: int | None = None,
                     zurueck: str = "", rangliste: str = ""):  # fmt: skip
    _, erg, daten = _stand(request, session, veranstaltung)
    # Leiste „Zurück“: zum Admin (nur eigene Adressen) oder zu den öffentlichen Ergebnissen
    ziel = zurueck if zurueck.startswith("/admin/") and "//" not in zurueck else "/ergebnisse"
    return templates.TemplateResponse(
        request,
        "ergebnisse_druck.html",
        {"d": daten, "zurueck": ziel, "rl": _gewaehlte_rangliste(erg, daten, rangliste)},
    )


@router.get("/ergebnisse.csv")
def ergebnisse_csv(request: Request, session: SessionDep, veranstaltung: int | None = None,
                   rangliste: str = ""):  # fmt: skip
    v, erg, daten = _stand(request, session, veranstaltung)
    if v is None:
        return Response("Keine aktive Veranstaltung", status_code=404)
    if erg is None:  # Segmente 46: noch keine Ergebnisse
        return Response(KEINE_ERGEBNISSE, status_code=404)
    if rangliste:  # Rangliste eines Abschnitts (Segmente 15), nur wenn Segmentzeiten öffentlich
        from app import ranglisten

        r = ranglisten.rangliste(erg, rangliste) if daten.get("zwischenzeiten") else None
        if r is None:
            return Response("Rangliste nicht gefunden", status_code=404)
        return Response(
            rangliste_csv(v, erg, r),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="rangliste-{v.datum}.csv"'},
        )
    return Response(
        ergebnis_csv(v, erg, oeffentlich=True),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="ergebnis-{v.datum}.csv"'},
    )


@router.get("/", response_class=HTMLResponse)
def startseite(request: Request, session: SessionDep):
    """Einstieg für alle: Ergebnisse, Zielmonitor, Admin. Keine Gerätelinks (tokengeschützt)."""
    v = aktive_veranstaltung(session)
    return templates.TemplateResponse(request, "startseite.html", {"v": v})


def ergebnisse_url(request: Request) -> str:
    """Öffentliche Adresse der Live-Ergebnisse (nicht einer bestimmten Veranstaltung)."""
    return f"{basis_url(request, request.app.state.vertraute_proxys)}/ergebnisse"


def qr_svg(url: str, scale: int = 4) -> str:
    return segno.make(url, error="m").svg_inline(scale=scale, border=2, dark="#000", light="#fff")


@router.get("/monitor", response_class=HTMLResponse)
def monitor(request: Request):
    url = ergebnisse_url(request)
    return templates.TemplateResponse(
        request, "monitor.html", {"qr_url": url, "qr": qr_svg(url, 3), "version": APP_VERSION}
    )
