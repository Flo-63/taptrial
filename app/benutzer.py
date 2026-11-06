"""Benutzer und Rollen verwalten: wer darf wem welche Rolle geben?

- Superadmin: alles (Superadmin, Vereinsadmin, Event-Admin vergeben und entziehen, Konten
  deaktivieren).
- Vereinsadmin: Konten anlegen, Event-Admins für Veranstaltungen des eigenen Vereins.
Jede Änderung steht im Protokoll der Admin-Aktionen (admin_aktion).
"""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.anmeldung import Person, jetzt_ms, protokollieren
from app.models import ROLLEN, Benutzer, Rollenzuweisung, Veranstaltung, Verein

ROLLE_TEXT = {
    "superadmin": "Superadmin",
    "vereinsadmin": "Vereinsadmin",
    "eventadmin": "Event-Admin",
}
NAME_MUSTER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,149}$")


class BenutzerFehler(ValueError):
    pass


def alle(session: Session) -> list[Benutzer]:
    """Konten ohne Rolle zuerst (neu angemeldet, warten auf Freigabe), dann nach Name."""
    konten = list(session.scalars(select(Benutzer)))
    return sorted(konten, key=lambda b: (bool(b.rollen), (b.name or b.benutzername).lower()))


def anlegen(session: Session, person: Person, benutzername: str, name: str) -> Benutzer:
    """Konto vorab anlegen; die Uid kommt bei der ersten Anmeldung dazu."""
    benutzername = benutzername.strip()  # genau wie beim Anmeldedienst (Groß/Klein zählt)
    if not NAME_MUSTER.match(benutzername):
        raise BenutzerFehler(
            "Benutzername: Buchstaben, Ziffern und . _ @ - genau wie beim Anmeldedienst, "
            "höchstens 150 Zeichen."
        )
    if session.scalar(select(Benutzer.id).where(Benutzer.benutzername == benutzername)):
        raise BenutzerFehler(f"Den Benutzernamen „{benutzername}“ gibt es schon.")
    b = Benutzer(benutzername=benutzername, name=" ".join(name.split())[:200], angelegt_ms=jetzt_ms(),
                 herkunft="proxy")  # fmt: skip
    session.add(b)
    session.flush()
    protokollieren(session, person.anzeige, "Konto angelegt", f"{b.anzeige}", person.benutzer_id)
    session.commit()
    return b


def darf_rolle(person: Person, rolle: str, verein_id: int | None, v: Veranstaltung | None) -> bool:
    """Darf diese Person die Rolle vergeben bzw. entziehen?"""
    if person.superadmin:
        return True
    return rolle == "eventadmin" and v is not None and v.verein_id in person.vereine


def rolle_text(r: Rollenzuweisung, session: Session) -> str:
    if r.rolle == "eventadmin":
        v = session.get(Veranstaltung, r.veranstaltung_id)
        return f"Event-Admin „{v.name if v else '?'}“"
    if r.rolle == "vereinsadmin":
        verein = session.get(Verein, r.verein_id)
        return f"Vereinsadmin „{verein.name if verein else '?'}“"
    return ROLLE_TEXT.get(r.rolle, r.rolle)


def vergeben(session: Session, person: Person, b: Benutzer, rolle: str,
             veranstaltung_id: int | None = None, verein_id: int | None = None) -> str:  # fmt: skip
    if rolle not in ROLLEN:
        raise BenutzerFehler("Unbekannte Rolle.")
    v = None
    if rolle == "eventadmin":
        v = session.get(Veranstaltung, veranstaltung_id) if veranstaltung_id else None
        if v is None:
            raise BenutzerFehler("Für Event-Admin bitte eine Veranstaltung wählen.")
        verein_id = v.verein_id
    elif rolle == "vereinsadmin":
        verein_id = verein_id or session.scalar(select(Verein.id).order_by(Verein.id).limit(1))
    else:
        verein_id = None
    if not darf_rolle(person, rolle, verein_id, v):
        raise BenutzerFehler("Diese Rolle darfst du nicht vergeben.")
    schluessel = (rolle, verein_id if rolle == "vereinsadmin" else None, v.id if v else None)
    if any((r.rolle, r.verein_id if r.rolle == "vereinsadmin" else None, r.veranstaltung_id)
           == schluessel for r in b.rollen):  # fmt: skip
        return f"{b.anzeige} hat diese Rolle schon."
    r = Rollenzuweisung(rolle=rolle, verein_id=verein_id, veranstaltung_id=v.id if v else None)
    b.rollen.append(r)
    session.flush()
    text = f"{rolle_text(r, session)} an {b.anzeige}"
    protokollieren(session, person.anzeige, "Rolle vergeben", text, person.benutzer_id,
                   v.id if v else None)  # fmt: skip
    session.commit()
    return f"{text} vergeben."


def entziehen(session: Session, person: Person, b: Benutzer, r: Rollenzuweisung) -> str:
    if r.benutzer_id != b.id:
        raise BenutzerFehler("Diese Rolle gehört zu einem anderen Konto.")
    v = session.get(Veranstaltung, r.veranstaltung_id) if r.veranstaltung_id else None
    if not darf_rolle(person, r.rolle, r.verein_id, v):
        raise BenutzerFehler("Diese Rolle darfst du nicht entziehen.")
    text = f"{rolle_text(r, session)} von {b.anzeige}"
    b.rollen.remove(r)
    protokollieren(session, person.anzeige, "Rolle entzogen", text, person.benutzer_id,
                   v.id if v else None)  # fmt: skip
    session.commit()
    return f"{text} entzogen."


def aktiv_setzen(session: Session, person: Person, b: Benutzer, aktiv: bool) -> str:
    if not person.superadmin:
        raise BenutzerFehler("Konten deaktivieren kann nur ein Superadmin.")
    if b.id == person.benutzer_id and not aktiv:
        raise BenutzerFehler("Das eigene Konto lässt sich nicht deaktivieren.")
    b.aktiv = aktiv
    text = f"{b.anzeige} {'aktiviert' if aktiv else 'deaktiviert'}"
    protokollieren(session, person.anzeige, "Konto " + ("aktiviert" if aktiv else "deaktiviert"),
                   text, person.benutzer_id)  # fmt: skip
    session.commit()
    return f"{text}."
