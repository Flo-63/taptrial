"""FastAPI-Anwendung."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import marke, saetze, strecken
from app.admin import nicht_angemeldet
from app.admin import router as admin_router
from app.admin_auth import NichtAngemeldet
from app.admin_einrichtung import router as einrichtung_router
from app.admin_geraetedatei import router as geraetedatei_router
from app.admin_import import router as import_router
from app.admin_saetze import router as saetze_router
from app.api import router as api_router
from app.db import erzeuge_engine, erzeuge_sessionmaker, tabellen_anlegen
from app.einstellungen import Einstellungen, EinstellungsFehler
from app.hilfe import router as hilfe_router
from app.oeffentlich import router as oeffentlich_router
from app.proxy import Netze
from app.seiten import STATIC_DIR
from app.seiten import router as seiten_router
from app.version import VERSION

log = logging.getLogger(__name__)


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
        tabellen_anlegen(engine)
        with erzeuge_sessionmaker(engine)() as session:
            # erster Start: Dateien aus config/ als Sätze; Migration bestehender Veranstaltungen
            saetze.standardsaetze_anlegen(
                session, einstellungen.klassen_datei, einstellungen.wertungen_verzeichnis
            )
            saetze.veranstaltungen_zuordnen(session)
            strecken.migrieren(session)  # Je Veranstaltung mindestens eine Strecke
        app.state.engine = engine
        app.state.sessionmaker = erzeuge_sessionmaker(engine)
        yield
        engine.dispose()

    app = FastAPI(title=marke.NAME, version=VERSION, lifespan=lifespan)

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
