"""Wer ist im Admin angemeldet?

- Modus `token`: Passphrase im Formular → signiertes Cookie (app/admin_auth.py); die Person ist
  „Admin (Passphrase)“ mit den Rechten eines Superadmins.
- Modus `proxy`: Ein Forward-Auth-Dienst (z. B. Authentik) reicht Kopfzeilen und ein signiertes JWT
  weiter. Geprüft wird bei jeder Admin-Anfrage: Absendernetz (optional), Signatur über die Schlüssel
  von TAPTRIAL_PROXY_JWKS_URL, Ablauf, Aussteller, Zielgruppe und Zugangsgruppe (optional). Uid und
  Benutzername stammen aus dem Token und müssen den Kopfzeilen gleichen. Scheitert etwas: keine
  Anmeldung (fail closed), Grund im Log.

Spätere Arten (eigene Konten, OIDC) liefern ebenfalls eine `Identitaet`; `anmelden()` und die
Rechteprüfung (app/rechte_admin.py) bleiben gleich.
"""

import contextvars
import logging
import threading
import time
from dataclasses import dataclass

import jwt
from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AdminAktion, Benutzer

log = logging.getLogger(__name__)

PASSPHRASE = "Admin (Passphrase)"
ALGORITHMEN = ["RS256", "RS384", "RS512", "PS256", "ES256", "ES384"]
SPIELRAUM_S = 30  # Uhrabweichung zwischen Anmeldedienst und App
ANMELDUNG_MERKEN_MS = 15 * 60 * 1000  # „letzte Anmeldung“ höchstens so oft schreiben


@dataclass(frozen=True)
class Identitaet:
    """Geprüfte Identität vom Anmeldedienst."""

    uid: str
    benutzername: str
    name: str = ""
    email: str = ""


@dataclass(frozen=True)
class Person:
    """Die angemeldete Person mit ihren Rollen (bei jeder Anfrage aus der Datenbank)."""

    anzeige: str
    benutzer_id: int | None = None
    superadmin: bool = False
    vereine: frozenset[int] = frozenset()  # Vereinsadmin
    veranstaltungen: frozenset[int] = frozenset()  # Event-Admin
    aktiv: bool = True
    csrf_basis: str = ""  # proxy: an die Uid gebunden; token: leer (Cookie)

    @property
    def hat_rolle(self) -> bool:
        return self.aktiv and bool(self.superadmin or self.vereine or self.veranstaltungen)

    @property
    def rolle_text(self) -> str:
        if not self.aktiv:
            return "deaktiviert"
        if self.superadmin:
            return "Superadmin"
        if self.vereine:
            return "Vereinsadmin"
        if self.veranstaltungen:
            return "Event-Admin"
        return "ohne Rolle"


PASSPHRASE_PERSON = Person(PASSPHRASE, superadmin=True)


class AnmeldungFehlgeschlagen(HTTPException):
    """401: Im Modus `proxy` ließ sich niemand sicher erkennen."""

    def __init__(self, grund: str):
        super().__init__(401, "Anmeldung nicht möglich")
        self.grund = grund


# --- Wer löst gerade aus? (Protokoll der Admin-Ereignisse) -----------------------------------------


@dataclass
class _Halter:
    person: Person | None = None


_aktuell: contextvars.ContextVar[_Halter | None] = contextvars.ContextVar(
    "admin_person", default=None
)


def halter_setzen() -> contextvars.Token:
    """Je Admin-Anfrage ein Halter; die Rechteprüfung trägt die Person ein. Ein Halter (statt der
    Person selbst), weil FastAPI Abhängigkeiten in einem eigenen Thread mit kopiertem Kontext
    ausführt: Die Kopie zeigt auf denselben Halter."""
    return _aktuell.set(_Halter())


def halter_loesen(token: contextvars.Token) -> None:
    _aktuell.reset(token)


def person_merken(person: Person) -> None:
    halter = _aktuell.get()
    if halter is not None:
        halter.person = person


def aktuelle_person() -> Person | None:
    halter = _aktuell.get()
    return halter.person if halter else None


def ausloeser() -> str:
    """Text für `ereignis.benutzer`: angemeldete Person, sonst „Admin“ (Kommandozeile, Demo)."""
    person = aktuelle_person()
    return person.anzeige if person else "Admin"


# --- Modus proxy -----------------------------------------------------------------------------------


class Schluessel:
    """Schlüssel des Anmeldedienstes (JWKS über http/https), zwischengespeichert; ein unbekannter
    Schlüssel lädt neu."""

    NEU_LADEN_FRUEHESTENS_S = 60  # unbekannte Schlüssel-ID: nicht bei jeder Anfrage neu laden

    def __init__(self, url: str):
        self.url = url
        self._client = jwt.PyJWKClient(url, cache_keys=True, lifespan=600, timeout=5)
        self._sperre = threading.Lock()
        self._geladen = 0.0

    def fuer(self, token: str):
        kid = jwt.get_unverified_header(token).get("kid")
        with self._sperre:
            if self._geladen == 0.0 or time.monotonic() - self._geladen > 600:
                self._client.get_signing_keys(refresh=True)
                self._geladen = time.monotonic()
            schluessel = {k.key_id: k for k in self._client.get_signing_keys()}
            if (
                kid not in schluessel
                and time.monotonic() - self._geladen > self.NEU_LADEN_FRUEHESTENS_S
            ):
                schluessel = {k.key_id: k for k in self._client.get_signing_keys(refresh=True)}
                self._geladen = time.monotonic()
        if kid not in schluessel:
            raise jwt.PyJWKClientError(f"unbekannte Schlüssel-ID {kid!r}")
        return schluessel[kid].key


def _kopf(request: Request, name: str) -> str:
    return (request.headers.get(name) or "").strip()


def proxy_identitaet(request: Request) -> Identitaet:
    """Prüft Absender, Token und Kopfzeilen; wirft AnmeldungFehlgeschlagen."""
    e = request.app.state.einstellungen
    if fehlt := e.proxy_fehlt():
        raise AnmeldungFehlgeschlagen(f"Einstellung fehlt: {', '.join(fehlt)}")
    netze = request.app.state.proxy_netze
    peer = request.client.host if request.client else None
    if netze is not None and not netze.enthaelt(peer):
        raise AnmeldungFehlgeschlagen(f"Absender {peer} nicht in TAPTRIAL_PROXY_NETZE")
    token = _kopf(request, e.proxy_header_jwt)
    if not token:
        raise AnmeldungFehlgeschlagen(f"Kopfzeile {e.proxy_header_jwt} fehlt")
    try:
        schluessel = request.app.state.proxy_schluessel.fuer(token)
        daten = jwt.decode(
            token,
            schluessel,
            algorithms=ALGORITHMEN,
            issuer=e.proxy_jwt_issuer,
            audience=e.proxy_jwt_audience or None,
            leeway=SPIELRAUM_S,
            options={"require": ["exp", "iss", "sub"], "verify_aud": bool(e.proxy_jwt_audience)},
        )
    except (jwt.PyJWTError, OSError) as fehler:
        raise AnmeldungFehlgeschlagen(f"Token ungültig: {fehler}") from fehler
    uid = str(daten["sub"])
    benutzername = str(daten.get("preferred_username") or "").strip()
    if not benutzername:
        raise AnmeldungFehlgeschlagen("Token ohne preferred_username")
    if _kopf(request, e.proxy_header_uid) != uid:
        raise AnmeldungFehlgeschlagen("Uid der Kopfzeile passt nicht zum Token")
    kopf_name = _kopf(request, e.proxy_header_benutzername)
    if kopf_name and kopf_name != benutzername:
        raise AnmeldungFehlgeschlagen("Benutzername der Kopfzeile passt nicht zum Token")
    if e.proxy_gruppe and e.proxy_gruppe not in (daten.get("groups") or []):
        raise AnmeldungFehlgeschlagen(f"nicht in der Zugangsgruppe {e.proxy_gruppe}")
    return Identitaet(
        uid=uid,
        benutzername=benutzername,
        name=str(daten.get("name") or _kopf(request, e.proxy_header_name))[:200],
        email=str(daten.get("email") or _kopf(request, e.proxy_header_email))[:200],
    )


def jetzt_ms() -> int:
    return int(time.time() * 1000)


def protokollieren(session: Session, person_text: str, aktion: str, text: str = "",
                   benutzer_id: int | None = None, veranstaltung_id: int | None = None) -> None:  # fmt: skip
    session.add(AdminAktion(zeit_ms=jetzt_ms(), benutzer=person_text[:120], benutzer_id=benutzer_id,
                            veranstaltung_id=veranstaltung_id, aktion=aktion, text=text[:300]))  # fmt: skip


def anmelden(session: Session, ident: Identitaet) -> Benutzer:
    """Konto zur Identität: nach Uid, sonst ein vorab angelegtes Konto gleichen Namens ohne Uid,
    sonst neu und ohne Rolle. E-Mail und Name werden aktualisiert."""
    jetzt = jetzt_ms()
    b = session.scalars(select(Benutzer).where(Benutzer.uid == ident.uid)).first()
    if b is None:
        # genau gleich geschrieben: beim Anmeldedienst können sich Namen nur in Groß/Klein unterscheiden
        b = session.scalars(
            select(Benutzer).where(Benutzer.benutzername == ident.benutzername)
        ).first()
        if b is not None and b.uid is not None:
            # Name gehört schon einer anderen Uid: nie übernehmen
            raise AnmeldungFehlgeschlagen(
                f"Benutzername {ident.benutzername} gehört schon einem anderen Konto"
            )
        if b is not None:
            protokollieren(session, b.anzeige, "Konto verknüpft", "erste Anmeldung", b.id)
        if b is None:
            b = Benutzer(benutzername=ident.benutzername, angelegt_ms=jetzt, herkunft="proxy")
            session.add(b)
            session.flush()
            protokollieren(session, b.benutzername, "Konto angelegt",
                           "bei der ersten Anmeldung, ohne Rolle", b.id)  # fmt: skip
        b.uid = ident.uid
    if ident.email and b.email != ident.email:
        b.email = ident.email
    if ident.name and not b.name:
        b.name = ident.name
    if b.letzte_anmeldung_ms is None or jetzt - b.letzte_anmeldung_ms > ANMELDUNG_MERKEN_MS:
        if not b.rollen and b.letzte_anmeldung_ms is not None:
            protokollieren(session, b.anzeige, "Anmeldung ohne Rolle", "", b.id)
        b.letzte_anmeldung_ms = jetzt
    if session.dirty or session.new:
        session.commit()
    return b


def person_von(benutzer: Benutzer, superadmins: frozenset[str]) -> Person:
    immer = benutzer.benutzername in superadmins
    rollen = benutzer.rollen if benutzer.aktiv or immer else []
    return Person(
        anzeige=benutzer.anzeige,
        benutzer_id=benutzer.id,
        superadmin=immer or any(r.rolle == "superadmin" for r in rollen),
        vereine=frozenset(r.verein_id for r in rollen if r.rolle == "vereinsadmin" and r.verein_id),
        veranstaltungen=frozenset(
            r.veranstaltung_id for r in rollen if r.rolle == "eventadmin" and r.veranstaltung_id
        ),
        aktiv=benutzer.aktiv or immer,
        csrf_basis=f"proxy:{benutzer.uid or benutzer.id}",
    )
