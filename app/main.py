"""FastAPI-Anwendung."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import marke
from app.admin import nicht_angemeldet
from app.admin import router as admin_router
from app.admin_auth import NichtAngemeldet
from app.admin_einrichtung import router as einrichtung_router
from app.admin_geraetedatei import router as geraetedatei_router
from app.admin_import import router as import_router
from app.admin_saetze import router as saetze_router
from app.api import router as api_router
from app.db import erzeuge_engine, erzeuge_sessionmaker
from app.einstellungen import Einstellungen, EinstellungsFehler
from app.hilfe import router as hilfe_router
from app.migrationen import MigrationsFehler, migrieren
from app.oeffentlich import router as oeffentlich_router
from app.proxy import Netze
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
    app.include_router(admin_router)
    app.include_router(oeffentlich_router)
    app.include_router(hilfe_router)
    app.add_exception_handler(NichtAngemeldet, nicht_angemeldet)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
