"""Token-Prüfung je Rolle (KONZEPT 8).

Admin-Token aus der Umgebung; Start- und Ziel-Token gehören zu genau einer Veranstaltung
(DB) und gelten nur, solange diese läuft (nicht abgeschlossen/anonymisiert).
"""

import secrets
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import GeloeschterToken, Veranstaltung, token_fingerabdruck

GERAETEROLLEN = ("start", "ziel", "rennleitung")


def get_session(request: Request):
    with request.app.state.sessionmaker() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


class VeranstaltungGeloescht(HTTPException):
    """410: Der Gerätetoken gehörte zu einer gelöschten Veranstaltung (nur Fingerabdruck bekannt)."""

    def __init__(self):
        super().__init__(410, "Veranstaltung gelöscht")


class VeranstaltungBeendet(HTTPException):
    """410: Der Gerätetoken gehört zu einer abgeschlossenen/anonymisierten Veranstaltung.

    Das Gerät räumt daraufhin seine lokalen Daten auf (ungesendete Einträge bleiben).
    """

    def __init__(self):
        super().__init__(410, "Veranstaltung beendet")


@dataclass
class Zugang:
    rolle: str
    veranstaltung: Veranstaltung | None  # bei Geräten die Veranstaltung des Tokens
    messpunkt: Any = None  # Rolle „posten“: der Messpunkt des Tokens (Phase 14)
    ort: Any = None  # Start/Ziel/Rennleitung eines weiteren Orts (Segmente 38); None = Standardort


def gleich(a: str, b: str | None) -> bool:
    return b is not None and secrets.compare_digest(a.encode(), b.encode())


def token_aus_anfrage(request: Request) -> str | None:
    kopf = request.headers.get("authorization", "")
    if kopf.lower().startswith("bearer "):
        return kopf[7:].strip()
    return request.headers.get("x-token") or request.query_params.get("token")


def zugang_fuer_token(request: Request, session: Session, token: str | None) -> Zugang | None:
    if not token:
        return None
    if gleich(token, request.app.state.einstellungen.token_admin):
        return Zugang("admin", None)
    return geraet_zugang(session, token)


def geraet_zugang(session: Session, token: str) -> Zugang | None:
    """Zugang eines Gerätetokens (Start, Ziel, Rennleitung, Posten, weitere Orte) – auch ohne
    Anfrage nutzbar, z. B. für die Demo-Veranstaltung (Segmente 68)."""
    # wenige Veranstaltungen: in Python vergleichen (zeitkonstant), statt per SQL zu suchen
    for v in session.scalars(select(Veranstaltung)):
        for rolle in GERAETEROLLEN:
            if gleich(token, getattr(v, f"token_{rolle}")):
                if rolle == "rennleitung" and not v.rennleitung_getrennt and v.laufend:
                    return None  # eigener Rennleitungs-Link nur, wenn getrennt
                if not v.laufend:
                    raise VeranstaltungBeendet()
                return Zugang(rolle, v)
    from app.strecken import messpunkt_fuer_token  # Posten: Token je Messpunkt (Phase 14)

    treffer = messpunkt_fuer_token(session, token)
    if treffer is not None:
        m, v, rolle = treffer
        if not v.laufend:
            raise VeranstaltungBeendet()
        return Zugang("posten", v, m) if rolle == "posten" else Zugang(rolle, v, ort=m)
    if session.scalar(
        select(GeloeschterToken.id).where(
            GeloeschterToken.fingerabdruck == token_fingerabdruck(token)
        )
    ):
        raise VeranstaltungGeloescht()
    return None


def geraetetoken_gueltig(request: Request, session: Session, rolle: str, token: str | None) -> bool:
    try:
        z = zugang_fuer_token(request, session, token)
    except (VeranstaltungBeendet, VeranstaltungGeloescht):
        return False
    return z is not None and z.rolle == rolle


def erfordere(*erlaubt: str):
    """Dependency: 401 ohne/mit unbekanntem Token, 403 bei falscher Rolle."""

    def pruefen(request: Request, session: SessionDep) -> Zugang:
        zugang = zugang_fuer_token(request, session, token_aus_anfrage(request))
        if zugang is None:
            raise HTTPException(401, "Token fehlt oder ist ungültig")
        if zugang.rolle not in erlaubt:
            raise HTTPException(403, "Keine Berechtigung für diese Rolle")
        return zugang

    return pruefen
