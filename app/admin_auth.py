"""Anmeldung für den Admin: Admin-Token im Anmeldeformular, danach signiertes Sitzungs-Cookie.

Keine zusätzliche Abhängigkeit: Cookie = Rolle + Ablaufzeit + HMAC-SHA256 (Schlüssel: Admin-Token).
Ändert sich der Token, werden alle Sitzungen ungültig. Die Rolle steckt schon heute in der Sitzung,
damit später Benutzer mit Rollen (Super-Admin, Race-Admin) andocken können (KONZEPT „Ausblick“).
Formulare tragen ein CSRF-Token. Nach 5 Fehlversuchen ist die IP 15 Minuten gesperrt
(Client-IP hinter dem Proxy siehe app/proxy.py).
"""

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request
from fastapi.responses import Response

from app.proxy import client_ip, ist_https

log = logging.getLogger(__name__)

COOKIE = "rcb_admin"
SITZUNG_S = 12 * 3600
MAX_FEHLVERSUCHE = 5
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
    if len(teile) != 3 or not teile[1].isdigit() or int(teile[1]) < time.time():
        return None
    rolle, ablauf, sig = teile
    if not hmac.compare_digest(sig, _signatur(request, f"{rolle}|{ablauf}")):
        return None
    return Sitzung(rolle, int(ablauf))


def angemeldet(request: Request) -> bool:
    return sitzung(request) is not None


def sitzung_setzen(request: Request, antwort: Response, rolle: str = ROLLE_ADMIN) -> None:
    antwort.set_cookie(
        COOKIE,
        cookie_wert(request, rolle),
        max_age=SITZUNG_S,
        httponly=True,
        samesite="strict",
        secure=ist_https(request, request.app.state.vertraute_proxys),
        path="/admin",
    )


def csrf_token(request: Request) -> str:
    return _signatur(request, "csrf:" + request.cookies.get(COOKIE, ""))[:32]


def csrf_pruefen(request: Request, token: str | None) -> None:
    if not token or not hmac.compare_digest(token, csrf_token(request)):
        raise HTTPException(403, "Formular abgelaufen. Bitte Seite neu laden.")


def admin_token_gueltig(request: Request, token: str | None) -> bool:
    erwartet = request.app.state.einstellungen.token_admin
    return bool(token) and hmac.compare_digest(token.encode(), erwartet.encode())


def _ip(request: Request) -> str:
    """Client-IP; X-Forwarded-For nur, wenn die Anfrage vom vertrauten Proxy kommt."""
    return client_ip(request, request.app.state.vertraute_proxys)


def gesperrt(ip: str) -> bool:
    jetzt = time.time()
    versuche = [t for t in _fehlversuche.get(ip, []) if jetzt - t < SPERRE_S]
    _fehlversuche[ip] = versuche
    return len(versuche) >= MAX_FEHLVERSUCHE


def token_pruefen(request: Request, token: str | None) -> bool:
    """Prüft den Admin-Token aus Formular oder Alt-Link; zählt und protokolliert Fehlversuche."""
    ip = _ip(request)
    if gesperrt(ip):
        log.warning("Admin-Anmeldung gesperrt für %s", ip)
        raise HTTPException(429, "Zu viele Fehlversuche. Bitte 15 Minuten warten.")
    if admin_token_gueltig(request, token):
        return True
    _fehlversuche.setdefault(ip, []).append(time.time())
    log.warning("Admin-Anmeldung fehlgeschlagen von %s (%d. Fehlversuch)", ip,
                len(_fehlversuche[ip]))  # fmt: skip
    return False


def erfordere_admin(request: Request) -> Sitzung:
    s = sitzung(request)
    if s is None or s.rolle != ROLLE_ADMIN:
        raise NichtAngemeldet()
    return s
