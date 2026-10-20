"""HTML-Seiten der Geräte, Manifest und Service Worker."""

import hashlib
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

from app import installation, marke
from app.auth import geraetetoken_gueltig
from app.version import VERSION

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
TEMPLATE_DIR = APP_DIR / "templates"

GERAETE = {"start": "Start", "ziel": "Ziel", "rennleitung": "Rennleitung", "posten": "Posten"}
# eigener Rennleitungs-Link (getrennt): gleiche Seite und gleiches Skript wie das Ziel; Posten (Segmente)
SEITE = {"start": "start", "ziel": "ziel", "rennleitung": "ziel", "posten": "posten"}

templates = Jinja2Templates(directory=TEMPLATE_DIR)
# Name nach außen und globale Einstellungen (Vereinsname) in allen Vorlagen
templates.env.globals["APP_NAME"] = marke.NAME
templates.env.globals["APP_UNTERTITEL"] = marke.UNTERTITEL
templates.env.globals["HINWEIS_GENEHMIGUNG"] = marke.HINWEIS_GENEHMIGUNG
templates.env.globals["installation"] = installation.fuer_vorlage
templates.env.globals["vereinsname"] = installation.vereinsname  # v0.1.1: Verein vorn
templates.env.globals["titel_zusatz"] = installation.titel_zusatz
templates.env.globals["kuerzel"] = installation.kuerzel
templates.env.globals["rechtliches_links"] = installation.rechtliches_links
templates.env.globals["APP_RELEASE"] = VERSION  # APP_VERSION ist der Cache-Hash
router = APIRouter()


def _app_version() -> str:
    """Hash über statische Dateien und Templates: neue Version → neuer SW-Cache."""
    h = hashlib.sha256()
    for pfad in sorted([*STATIC_DIR.rglob("*"), *TEMPLATE_DIR.rglob("*")]):
        if pfad.is_file():
            h.update(str(pfad.relative_to(APP_DIR)).encode())
            h.update(pfad.read_bytes())
    return h.hexdigest()[:12]


APP_VERSION = _app_version()


def _gueltiger_token(request: Request, rolle: str) -> str | None:
    """Gibt den Token nur zurück, wenn er zur Rolle passt (nie Nutzereingaben ungeprüft rendern)."""
    token = request.query_params.get("token")
    if not token:
        return None
    with request.app.state.sessionmaker() as session:
        return token if geraetetoken_gueltig(request, session, rolle, token) else None


def _profil_parameter(request: Request, token: str | None) -> str:
    """„&ort=…“ und „&sicht=rennleitung“ aus dem Gerätelink für das Manifest (der
    Startbildschirm öffnet dasselbe Geräteprofil)."""
    if not token:
        return ""
    ort = "".join(c for c in request.query_params.get("ort", "") if c.isdigit())[:10]
    teile = [f"&ort={ort}"] if ort else []
    if request.query_params.get("sicht") == "rennleitung":
        teile.append("&sicht=rennleitung")
    return "".join(teile)


def _precache_liste() -> list[str]:
    statisch = [
        "/static/" + str(p.relative_to(STATIC_DIR))
        for p in sorted(STATIC_DIR.rglob("*"))
        if p.is_file() and p.name != "sw.js"
    ]
    seiten = [f"/{r}" for r in GERAETE] + [f"/{r}.webmanifest" for r in GERAETE]
    # Kurzanleitungen für die Geräte auch im Funkloch verfügbar
    from app.hilfe import OFFLINE, bilder_in

    hilfe = [f"/hilfe/{n}" for n in OFFLINE]
    bilder = sorted({f"/hilfe/bilder/{b}" for n in OFFLINE for b in bilder_in(n)})
    return seiten + hilfe + bilder + statisch


@router.get("/sw.js", include_in_schema=False)
def service_worker() -> Response:
    quelltext = (STATIC_DIR / "sw.js").read_text(encoding="utf-8")
    quelltext = quelltext.replace("__VERSION__", APP_VERSION).replace(
        "__ASSETS__", json.dumps(_precache_liste())
    )
    return Response(
        quelltext,
        media_type="text/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )


@router.get("/{rolle}.webmanifest", include_in_schema=False)
def manifest(rolle: str, request: Request) -> JSONResponse:
    if rolle not in GERAETE:
        raise HTTPException(404)
    token = _gueltiger_token(request, rolle)
    return JSONResponse(
        {
            "name": f"{marke.NAME} – {GERAETE[rolle]}",
            "short_name": GERAETE[rolle],
            "start_url": f"/{rolle}"
            + (f"?token={token}{_profil_parameter(request, token)}" if token else ""),
            "scope": "/",
            "display": "standalone",
            "orientation": "portrait",
            "background_color": "#111111",
            "theme_color": "#111111",
            "icons": [
                *(
                    {"src": f"/static/icons/icon-{g}.png", "sizes": f"{g}x{g}", "type": "image/png"}
                    for g in (192, 512)
                ),
                {"src": "/static/icon.svg", "sizes": "any", "type": "image/svg+xml"},
            ],
        },
        media_type="application/manifest+json",
    )


def geraeteseite(rolle: str):
    def seite(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            f"{SEITE[rolle]}.html",
            {
                "rolle": rolle,
                "skript": SEITE[rolle],
                "titel": GERAETE[rolle],
                "token": (token := _gueltiger_token(request, rolle)),
                "profil_parameter": _profil_parameter(request, token),
                "version": APP_VERSION,
            },
        )

    return seite


for _rolle in GERAETE:
    router.add_api_route(
        f"/{_rolle}", geraeteseite(_rolle), response_class=HTMLResponse, include_in_schema=False
    )
