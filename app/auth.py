"""Token-Prüfung je Rolle.

Admin-Token aus der Umgebung; Start- und Ziel-Token gehören zu genau einer Veranstaltung
(DB) und gelten nur, solange diese läuft (nicht abgeschlossen/anonymisiert).
"""

import hashlib
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import GeloeschterToken, Veranstaltung, token_fingerabdruck
from app.proxy import absender, client_ip

log = logging.getLogger(__name__)

GERAETEROLLEN = ("start", "ziel", "rennleitung")
# API: Sperre je Absender nach verschiedenen falschen Tokens. Gültige Gerätetokens sind nie
# betroffen; ein Gerät mit altem Link zählt einmal, egal wie oft es sich meldet.
API_MAX_FEHLVERSUCHE = 20
API_SPERRE_S = 15 * 60
API_MAX_ABSENDER = 10_000  # begrenzt den Speicher bei wechselnden Absendern


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


class ZuVieleVersuche(HTTPException):
    def __init__(self):
        super().__init__(429, "Zu viele Fehlversuche. Bitte 15 Minuten warten.")


@dataclass
class Zugang:
    rolle: str
    veranstaltung: Veranstaltung | None  # bei Geräten die Veranstaltung des Tokens
    messpunkt: Any = None  # Rolle „posten“: der Messpunkt des Tokens
    ort: Any = None  # Start/Ziel/Rennleitung eines weiteren Orts; None = Standardort


def gleich(a: str, b: str | None) -> bool:
    return b is not None and secrets.compare_digest(a.encode(), b.encode())


def token_aus_anfrage(request: Request) -> str | None:
    kopf = request.headers.get("authorization", "")
    if kopf.lower().startswith("bearer "):
        return kopf[7:].strip()
    return request.headers.get("x-token") or request.query_params.get("token")


def _fehlversuche(request: Request, sender: str) -> dict[str, float]:
    """Noch zählende Fehlversuche des Absenders: Fingerabdruck des Tokens → Zeitpunkt."""
    alle = request.app.state.api_fehlversuche
    grenze = time.time() - API_SPERRE_S
    aktuell = {t: z for t, z in alle.get(sender, {}).items() if z > grenze}
    if aktuell:
        alle[sender] = aktuell
    else:
        alle.pop(sender, None)
    return aktuell


def _fehlversuch_merken(request: Request, sender: str, token: str) -> None:
    alle = request.app.state.api_fehlversuche
    if sender not in alle and len(alle) >= API_MAX_ABSENDER:
        # Der älteste nicht gesperrte Absender weicht: Viele neue Absender heben keine Sperre auf
        del alle[min(alle, key=lambda s: (len(alle[s]) >= API_MAX_FEHLVERSUCHE,
                                          max(alle[s].values(), default=0)))]  # fmt: skip
    versuche = alle.setdefault(sender, {})
    versuche[hashlib.sha256(token.encode()).hexdigest()] = time.time()
    if len(versuche) == API_MAX_FEHLVERSUCHE:
        log.warning("API gesperrt für %s (%d falsche Tokens)", sender, len(versuche))


def zugang_fuer_token(request: Request, session: Session, token: str | None) -> Zugang | None:
    """Gerätetokens zuerst und ohne Sperre: Kein Fremder im selben Netz sperrt Geräte im Rennen aus.
    Der Admin-Token und unbekannte Tokens zählen je Absender."""
    if not token:
        return None
    zugang = geraet_zugang(session, token)
    if zugang is not None:
        return zugang
    sender = absender(client_ip(request, request.app.state.vertraute_proxys))
    if len(_fehlversuche(request, sender)) >= API_MAX_FEHLVERSUCHE:
        raise ZuVieleVersuche()
    einstellungen = request.app.state.einstellungen
    if gleich(token, einstellungen.token_admin):
        if einstellungen.proxy:  # Admin nur über den Anmeldedienst, nicht mit der Passphrase
            log.warning("API: Admin-Passphrase im Modus proxy abgewiesen (%s)", sender)
            return None
        return Zugang("admin", None)
    _fehlversuch_merken(request, sender, token)
    return None


def geraet_zugang(session: Session, token: str) -> Zugang | None:
    """Zugang eines Gerätetokens (Start, Ziel, Rennleitung, Posten, weitere Orte) – auch ohne
    Anfrage nutzbar, z. B. für die Demo-Veranstaltung."""
    # wenige Veranstaltungen: in Python vergleichen (zeitkonstant), statt per SQL zu suchen
    for v in session.scalars(select(Veranstaltung)):
        for rolle in GERAETEROLLEN:
            if gleich(token, getattr(v, f"token_{rolle}")):
                if rolle == "rennleitung" and not v.rennleitung_getrennt and v.laufend:
                    return None  # eigener Rennleitungs-Link nur, wenn getrennt
                if not v.laufend:
                    raise VeranstaltungBeendet()
                return Zugang(rolle, v)
    from app.strecken import messpunkt_fuer_token  # Posten: Token je Messpunkt

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
    except (VeranstaltungBeendet, VeranstaltungGeloescht, ZuVieleVersuche):
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
