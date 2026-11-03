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

from app.proxy import absender, client_ip, ist_https

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
    return _signatur(request, "csrf:" + request.cookies.get(COOKIE, ""))[:32]


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
