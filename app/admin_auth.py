"""Anmeldung für den Admin: Admin-Token im Anmeldeformular, danach signiertes Sitzungs-Cookie.

Keine zusätzliche Abhängigkeit: Cookie = Rolle + Ablaufzeit + HMAC-SHA256 (Schlüssel: Admin-Token).
Ändert sich der Token, werden alle Sitzungen ungültig. Die Rolle steckt schon heute in der Sitzung,
damit später Benutzer mit Rollen (Super-Admin, Race-Admin) andocken können.
Formulare tragen ein CSRF-Token. Nach 5 Fehlversuchen ist die IP 15 Minuten gesperrt
(Client-IP hinter dem Proxy siehe app/proxy.py). Das Cookie gilt für die ganze Seite: Auch die
öffentlichen Ansichten erkennen so die Admin-Vorschau; HttpOnly und SameSite=strict bleiben.
"""

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.anmeldung import (
    PASSPHRASE_PERSON,
    AnmeldungFehlgeschlagen,
    Person,
    anmelden,
    person_merken,
    person_von,
    proxy_identitaet,
)
from app.auth import SessionDep
from app.models import Veranstaltung
from app.proxy import absender, client_ip, ist_https
from app.rechte_admin import (
    OFFEN,
    V_LESEN,
    VERANSTALTUNGSRECHTE,
    KeineBerechtigung,
    darf,
    recht_der_route,
)

log = logging.getLogger(__name__)

COOKIE = "rcb_admin"
COOKIE_PFAD = "/"
ALTER_COOKIE_PFAD = "/admin"
SITZUNG_S = 12 * 3600
MAX_FEHLVERSUCHE = 5
MAX_ABSENDER = 10_000  # begrenzt den Speicher bei wechselnden Absendern
SPERRE_S = 15 * 60
ROLLE_ADMIN = "admin"

_fehlversuche: dict[str, list[float]] = {}


class NichtAngemeldet(HTTPException):
    def __init__(self):
        super().__init__(401, "Nicht angemeldet")


@dataclass(frozen=True)
class Sitzung:
    rolle: str
    ablauf: int


def _schluessel(request: Request) -> bytes:
    return request.app.state.einstellungen.token_admin.encode()


def _signatur(request: Request, nachricht: str) -> str:
    return hmac.new(_schluessel(request), nachricht.encode(), hashlib.sha256).hexdigest()


def cookie_wert(request: Request, rolle: str = ROLLE_ADMIN, jetzt: float | None = None) -> str:
    ablauf = int((jetzt or time.time()) + SITZUNG_S)
    return f"{rolle}.{ablauf}.{_signatur(request, f'{rolle}|{ablauf}')}"


def sitzung(request: Request) -> Sitzung | None:
    teile = request.cookies.get(COOKIE, "").split(".")
    if len(teile) != 3 or not (teile[1].isascii() and teile[1].isdigit()) or len(teile[1]) > 20:
        return None
    rolle, ablauf, sig = teile
    if int(ablauf) < time.time():
        return None
    if not hmac.compare_digest(sig.encode(), _signatur(request, f"{rolle}|{ablauf}").encode()):
        return None
    return Sitzung(rolle, int(ablauf))


def angemeldet(request: Request) -> bool:
    return sitzung(request) is not None


def vorschau_erlaubt(request: Request, session: Session, v: Veranstaltung) -> bool:
    """Öffentliche Seiten: Admin-Vorschau nur für eine Sitzung, deren Person die Veranstaltung
    sehen darf. `token`: Passphrase-Sitzung; `proxy`: Cookie mit der Benutzer-ID aus /admin."""
    s = sitzung(request)
    if s is None:
        return False
    einstellungen = request.app.state.einstellungen
    if not einstellungen.proxy:
        return s.rolle == ROLLE_ADMIN
    nummer = s.rolle[1:]
    if not (s.rolle.startswith("b") and nummer.isdigit() and len(nummer) < 10):
        return False
    from app.models import Benutzer

    benutzer = session.get(Benutzer, int(nummer))
    if benutzer is None:
        return False
    return darf(person_von(benutzer, einstellungen.superadmin_namen), V_LESEN, v)


def sitzung_setzen(request: Request, antwort: Response, rolle: str = ROLLE_ADMIN) -> None:
    antwort.set_cookie(
        COOKIE,
        cookie_wert(request, rolle),
        max_age=SITZUNG_S,
        httponly=True,
        samesite="strict",
        secure=ist_https(request, request.app.state.vertraute_proxys),
        path=COOKIE_PFAD,
    )


def sitzung_loeschen(antwort: Response) -> None:
    antwort.delete_cookie(COOKIE, path=COOKIE_PFAD)
    antwort.delete_cookie(COOKIE, path=ALTER_COOKIE_PFAD)  # Sitzungen von vor 0.1.7


def csrf_token(request: Request) -> str:
    """Im Modus `proxy` an die Uid der Person gebunden, sonst an das Sitzungs-Cookie."""
    person = getattr(request.state, "person", None)
    basis = (
        person.csrf_basis
        if person is not None and person.csrf_basis
        else request.cookies.get(COOKIE, "")
    )
    return _signatur(request, "csrf:" + basis)[:32]


def csrf_pruefen(request: Request, token: str | None) -> None:
    if not token or not hmac.compare_digest(token.encode(), csrf_token(request).encode()):
        raise HTTPException(403, "Formular abgelaufen. Bitte Seite neu laden.")


def admin_token_gueltig(request: Request, token: str | None) -> bool:
    erwartet = request.app.state.einstellungen.token_admin
    return bool(token) and hmac.compare_digest(token.encode(), erwartet.encode())


def _ip(request: Request) -> str:
    """Client-IP; X-Forwarded-For nur, wenn die Anfrage vom vertrauten Proxy kommt."""
    return absender(client_ip(request, request.app.state.vertraute_proxys))


def gesperrt(ip: str) -> bool:
    jetzt = time.time()
    versuche = [t for t in _fehlversuche.get(ip, []) if jetzt - t < SPERRE_S]
    if versuche:
        _fehlversuche[ip] = versuche
    else:
        _fehlversuche.pop(ip, None)
    return len(versuche) >= MAX_FEHLVERSUCHE


def token_pruefen(request: Request, token: str | None) -> bool:
    """Prüft den Admin-Token aus Formular oder Alt-Link; zählt und protokolliert Fehlversuche."""
    ip = _ip(request)
    if gesperrt(ip):
        log.warning("Admin-Anmeldung gesperrt für %s", ip)
        raise HTTPException(429, "Zu viele Fehlversuche. Bitte 15 Minuten warten.")
    if admin_token_gueltig(request, token):
        return True
    if ip not in _fehlversuche and len(_fehlversuche) >= MAX_ABSENDER:
        # Der älteste nicht gesperrte Absender weicht: Viele neue Absender heben keine Sperre auf
        del _fehlversuche[min(_fehlversuche, key=lambda s: (len(_fehlversuche[s]) >= MAX_FEHLVERSUCHE,
                                                            max(_fehlversuche[s], default=0)))]  # fmt: skip
    _fehlversuche.setdefault(ip, []).append(time.time())
    log.warning("Admin-Anmeldung fehlgeschlagen von %s (%d. Fehlversuch)", ip,
                len(_fehlversuche[ip]))  # fmt: skip
    return False


def erfordere_admin(request: Request) -> Sitzung:
    s = sitzung(request)
    if s is None or s.rolle != ROLLE_ADMIN:
        raise NichtAngemeldet()
    return s


class KeinZugriff(HTTPException):
    """403: angemeldet, aber ohne Rolle oder deaktiviert."""

    def __init__(self):
        super().__init__(403, "Kein Zugriff", headers={"X-TapTrial-Recht": "keine-rolle"})


def angemeldete_person(request: Request, session: Session) -> Person:
    """Person der Anfrage: Passphrase-Sitzung (`token`) oder geprüftes Token (`proxy`)."""
    einstellungen = request.app.state.einstellungen
    if not einstellungen.proxy:
        s = sitzung(request)
        if s is None or s.rolle != ROLLE_ADMIN:
            raise NichtAngemeldet()
        return PASSPHRASE_PERSON
    try:
        ident = proxy_identitaet(request)
        benutzer = anmelden(session, ident)
    except AnmeldungFehlgeschlagen as e:
        log.warning("Admin-Anmeldung (proxy) abgelehnt: %s (Absender %s, %s)", e.grund,
                    request.client.host if request.client else "?", request.url.path)  # fmt: skip
        raise
    return person_von(benutzer, einstellungen.superadmin_namen)


def admin_pruefen(request: Request, session: SessionDep) -> Person:
    """Abhängigkeit jeder Admin-Route: wer, mit welcher Rolle, darf er diese Route (für diese
    Veranstaltung)? Recht aus app/rechte_admin.py; Route ohne Eintrag → abgewiesen."""
    route = request.scope.get("route")
    recht = recht_der_route(request.method, getattr(route, "path", ""))
    if recht is None:
        log.error("Admin-Route ohne Recht: %s %s", request.method, getattr(route, "path", "?"))
        raise KeineBerechtigung()
    person = angemeldete_person(request, session)
    request.state.person = person
    person_merken(person)
    if recht == OFFEN:
        return person
    if not person.hat_rolle:
        raise KeinZugriff()
    v = None
    if recht in VERANSTALTUNGSRECHTE:
        vid = str(request.path_params.get("vid", ""))
        v = session.get(Veranstaltung, int(vid)) if vid.isdigit() and len(vid) < 10 else None
        if v is None and person.superadmin:
            return person  # die Route meldet „nicht gefunden“
    if not darf(person, recht, v):
        raise KeineBerechtigung()
    return person
