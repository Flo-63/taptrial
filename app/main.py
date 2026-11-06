"""FastAPI-Anwendung."""

import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from app import admin_auth, marke
from app.admin import anmeldung_fehlgeschlagen, kein_zugriff, keine_berechtigung, nicht_angemeldet
from app.admin import router as admin_router
from app.admin_auth import KeinZugriff, NichtAngemeldet
from app.admin_benutzer import router as benutzer_router
from app.admin_einrichtung import router as einrichtung_router
from app.admin_geraetedatei import router as geraetedatei_router
from app.admin_import import router as import_router
from app.admin_saetze import router as saetze_router
from app.anmeldung import (
    AnmeldungFehlgeschlagen,
    Schluessel,
    aktuelle_person,
    halter_loesen,
    halter_setzen,
    protokollieren,
)
from app.api import router as api_router
from app.db import erzeuge_engine, erzeuge_sessionmaker
from app.einstellungen import Einstellungen, EinstellungsFehler
from app.hilfe import router as hilfe_router
from app.migrationen import MigrationsFehler, migrieren
from app.models import Veranstaltung
from app.oeffentlich import router as oeffentlich_router
from app.proxy import Netze
from app.rechte_admin import KeineBerechtigung
from app.seiten import STATIC_DIR
from app.seiten import router as seiten_router
from app.version import VERSION

log = logging.getLogger(__name__)

MAX_ANFRAGE_BYTES = 10_000_000  # größter echter Fall: Gerätedatei (2 MB) als Formularfeld


class AnfrageBegrenzen:
    """Lehnt zu große Anfragen ab, bevor jemand sie liest: auch ohne Anmeldung, auch ohne
    Content-Length. Sonst füllen anonyme Anfragen den Speicher der einzigen Instanz."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        zu_gross = False
        gelesen = 0

        async def _ablehnen():
            await send({"type": "http.response.start", "status": 413,
                        "headers": [(b"content-type", b"text/plain; charset=utf-8")]})  # fmt: skip
            await send({"type": "http.response.body", "body": "Anfrage zu groß.".encode()})

        laenge = dict(scope["headers"]).get(b"content-length", b"")
        if laenge.isdigit() and int(laenge) > MAX_ANFRAGE_BYTES:
            await _ablehnen()
            return

        async def begrenzt():
            nonlocal gelesen, zu_gross
            nachricht = await receive()
            gelesen += len(nachricht.get("body", b""))
            if gelesen > MAX_ANFRAGE_BYTES and not zu_gross:
                zu_gross = True
                await _ablehnen()
                return {"type": "http.disconnect"}
            return nachricht

        async def senden(nachricht):
            if not zu_gross:  # die Ablehnung ist schon gesendet
                await send(nachricht)

        try:
            await self.app(scope, begrenzt, senden)
        except Exception:
            if not zu_gross:
                raise


class AdminKontext:
    """Für /admin: hält die Person der Anfrage (Protokoll der Admin-Ereignisse), schreibt jede
    erfolgreiche Änderung (POST, Status unter 400) in `admin_aktion` und setzt im Modus `proxy`
    das signierte Cookie, an dem die öffentlichen Seiten die Admin-Vorschau erkennen."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/admin"):
            await self.app(scope, receive, send)
            return
        marke = halter_setzen()
        status = 500

        async def senden(nachricht):
            nonlocal status
            if nachricht["type"] == "http.response.start":
                status = nachricht["status"]
                ort = dict(nachricht.get("headers", [])).get(b"location", b"")
                if b"fehler=" in ort:  # abgelehnt (Rückweg mit Fehlermeldung): nichts geändert
                    status = 400
                person = aktuelle_person()
                app = scope["app"]
                abmelden = (
                    scope["path"].rstrip("/") == "/admin/abmelden"
                )  # Cookie wird dort gelöscht
                if (
                    person is not None
                    and person.benutzer_id
                    and app.state.einstellungen.proxy
                    and not abmelden
                ):
                    from starlette.requests import Request
                    from starlette.responses import Response

                    hilfe = Response()
                    admin_auth.sitzung_setzen(Request(scope), hilfe, f"b{person.benutzer_id}")
                    cookie = [(k, w) for k, w in hilfe.raw_headers if k == b"set-cookie"]
                    kopf = [*nachricht.get("headers", []), *cookie]
                    nachricht = {**nachricht, "headers": kopf}
            await send(nachricht)

        try:
            await self.app(scope, receive, senden)
            person = aktuelle_person()
            if scope["method"] == "POST" and status < 400 and person is not None:
                await run_in_threadpool(_aktion_merken, scope, person)
        finally:
            halter_loesen(marke)


def aktion_text(name: str) -> str:
    """„teilnehmer_loeschen“ → „Teilnehmer löschen“ (Name der Route als Aktion)."""
    text = re.sub(r"(?<![eq])ue", "ü", re.sub(r"oe", "ö", re.sub(r"(?<!m)ae", "ä", name)))
    return text.replace("_", " ").capitalize().replace("Dsq", "DSQ")[:80]


def _aktion_merken(scope, person) -> None:
    if scope.get("state", {}).get("aktion_text") is not None:
        return  # schon mit eigenem Text protokolliert (Rollen, Konten)
    route = scope.get("route")
    name = getattr(getattr(route, "endpoint", None), "__name__", "") or scope["path"]
    vid = str(scope.get("path_params", {}).get("vid", ""))
    with scope["app"].state.sessionmaker() as session:
        v = session.get(Veranstaltung, int(vid)) if vid.isdigit() and len(vid) < 10 else None
        aktion = aktion_text(name)
        protokollieren(session, person.anzeige, aktion, scope["path"], person.benutzer_id,
                       v.id if v else None)  # fmt: skip
        session.commit()


def create_app(einstellungen: Einstellungen | None = None) -> FastAPI:
    """Ohne Einstellungen werden sie beim Start aus der Umgebung gelesen (fehlt etwas: kein Start)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal einstellungen
        if einstellungen is None:
            try:
                einstellungen = Einstellungen.aus_umgebung()
            except EinstellungsFehler as e:
                log.error("%s", e)
                raise
        app.state.einstellungen = einstellungen
        app.state.vertraute_proxys = Netze.aus_text(einstellungen.vertraute_proxys)
        app.state.proxy_netze = (
            Netze.aus_text(einstellungen.proxy_netze, "TAPTRIAL_PROXY_NETZE")
            if einstellungen.proxy_netze
            else None
        )
        app.state.proxy_schluessel = (
            Schluessel(einstellungen.proxy_jwks_url) if einstellungen.proxy_jwks_url else None
        )
        if einstellungen.proxy:
            if fehlt := einstellungen.proxy_fehlt():
                log.error("Anmeldung „proxy“: %s fehlt – keine Admin-Anmeldung möglich. Geräte "
                          "und öffentliche Seiten laufen weiter.", ", ".join(fehlt))  # fmt: skip
            log.info("Admin-Anmeldung über Forward-Auth (proxy)")
        engine = erzeuge_engine(einstellungen.db_pfad)
        try:
            # erster Start: Dateien aus config/ als Sätze; Migration bestehender Veranstaltungen
            migrieren(engine, einstellungen.klassen_datei, einstellungen.wertungen_verzeichnis)
        except MigrationsFehler as e:
            log.error("%s", e)
            engine.dispose()
            raise
        app.state.engine = engine
        app.state.sessionmaker = erzeuge_sessionmaker(engine)
        yield
        engine.dispose()

    app = FastAPI(title=marke.NAME, version=VERSION, lifespan=lifespan)
    app.state.api_fehlversuche = {}  # Absender → falsche Tokens (app/auth.py)
    app.add_middleware(AdminKontext)
    app.add_middleware(AnfrageBegrenzen)

    @app.get("/up")
    def up() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/version")
    def version() -> dict[str, str]:
        """Name und Version (aus pyproject.toml), dazu der Stand der Oberfläche."""
        from app.seiten import APP_VERSION

        return {"name": marke.NAME, "version": VERSION, "oberflaeche": APP_VERSION}

    @app.middleware("http")
    async def html_nicht_zwischenspeichern(request, call_next):
        """HTML-Seiten (Branding, Impressum …) ändern sich ohne neue Version; der Browser fragt
        deshalb jedes Mal beim Server nach statt eine gespeicherte Seite zu zeigen."""
        antwort = await call_next(request)
        if antwort.headers.get("content-type", "").startswith("text/html"):
            antwort.headers.setdefault("Cache-Control", "no-cache")
        return antwort

    app.include_router(api_router)
    app.include_router(seiten_router)
    app.include_router(saetze_router)
    app.include_router(import_router)
    app.include_router(geraetedatei_router)
    app.include_router(einrichtung_router)
    app.include_router(benutzer_router)
    app.include_router(admin_router)
    app.include_router(oeffentlich_router)
    app.include_router(hilfe_router)
    app.add_exception_handler(NichtAngemeldet, nicht_angemeldet)
    app.add_exception_handler(KeinZugriff, kein_zugriff)
    app.add_exception_handler(KeineBerechtigung, keine_berechtigung)
    app.add_exception_handler(AnmeldungFehlgeschlagen, anmeldung_fehlgeschlagen)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
