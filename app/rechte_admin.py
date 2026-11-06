"""Rechte im Admin-Bereich: Rolle × Geltungsbereich × Recht.

Jede Admin-Route steht in `ROUTEN` mit genau einem Recht. Eine Route ohne Eintrag wird abgewiesen
(und ein Test schlägt fehl). Die Prüfung läuft in `admin_pruefen` (Abhängigkeit jeder Admin-Route)
und wirkt auch, wenn die Oberfläche einen Knopf schon ausblendet.
"""

from fastapi import HTTPException

from app.anmeldung import Person
from app.models import Veranstaltung

OFFEN = "offen"  # ohne Anmeldung (Anmelden, Abmelden)
ANGEMELDET = "angemeldet"  # jede Rolle
INSTALLATION = "installation"  # Branding, Rechtliches
BENUTZER = "benutzer"  # Benutzerverwaltung (Vereinsadmin eingeschränkt)
VORLAGEN = "vorlagen"
ANLEGEN = "anlegen"  # neue Veranstaltung
V_LESEN = "v_lesen"  # Seiten, Druck, Export einer Veranstaltung
V_FUEHREN = "v_fuehren"  # Einrichtung bis offizielles Ergebnis
V_ADMINS = "v_admins"  # Event-Admins zuweisen
V_VERWALTEN = "v_verwalten"  # aktivieren, deaktivieren, abschließen, anonymisieren, löschen

RECHTE = (OFFEN, ANGEMELDET, INSTALLATION, BENUTZER, VORLAGEN, ANLEGEN, V_LESEN, V_FUEHREN,
          V_ADMINS, V_VERWALTEN)  # fmt: skip
VERANSTALTUNGSRECHTE = (V_LESEN, V_FUEHREN, V_ADMINS, V_VERWALTEN)

# Recht je Route: (Methode, Pfad wie im Router) → Recht. GET-Seiten einer Veranstaltung lesen,
# POST führt; Ausnahmen stehen ausdrücklich da.
ROUTEN: dict[tuple[str, str], str] = {
    ("POST", "/admin/anmelden"): OFFEN,
    ("POST", "/admin/abmelden"): OFFEN,
    ("GET", "/admin"): ANGEMELDET,
    ("GET", "/admin/beispiel-teilnehmer.csv"): ANGEMELDET,
    # Installation
    ("GET", "/admin/saetze/branding"): INSTALLATION,
    ("POST", "/admin/saetze/branding"): INSTALLATION,
    ("GET", "/admin/saetze/rechtliches"): INSTALLATION,
    ("POST", "/admin/saetze/rechtliches"): INSTALLATION,
    # Vorlagen
    ("GET", "/admin/saetze"): VORLAGEN,
    ("GET", "/admin/saetze/import"): VORLAGEN,
    ("GET", "/admin/saetze/klassen"): VORLAGEN,
    ("POST", "/admin/saetze/klassen-import"): VORLAGEN,
    ("GET", "/admin/saetze/klassen/neu"): VORLAGEN,
    ("GET", "/admin/saetze/klassen/{sid}"): VORLAGEN,
    ("POST", "/admin/saetze/klassen/{sid}"): VORLAGEN,
    ("GET", "/admin/saetze/klassen/{sid}/export.{format}"): VORLAGEN,
    ("GET", "/admin/saetze/urkunden"): VORLAGEN,
    ("GET", "/admin/saetze/wertung"): VORLAGEN,
    ("POST", "/admin/saetze/wertung-import"): VORLAGEN,
    ("GET", "/admin/saetze/wertung/neu"): VORLAGEN,
    ("GET", "/admin/saetze/wertung/{sid}"): VORLAGEN,
    ("POST", "/admin/saetze/wertung/{sid}"): VORLAGEN,
    ("GET", "/admin/saetze/wertung/{sid}/export.{format}"): VORLAGEN,
    ("POST", "/admin/saetze/{art}/{sid}/kopieren"): VORLAGEN,
    ("POST", "/admin/saetze/{art}/{sid}/loeschen"): VORLAGEN,
    # Anlegen
    ("GET", "/admin/einrichtung/neu"): ANLEGEN,
    ("POST", "/admin/einrichtung/neu"): ANLEGEN,
    ("POST", "/admin/veranstaltungen"): ANLEGEN,
    # Einrichtung einer bestehenden Veranstaltung
    ("GET", "/admin/einrichtung/{vid}/{schritt}"): V_LESEN,
    ("POST", "/admin/einrichtung/{vid}/1"): V_FUEHREN,
    ("POST", "/admin/einrichtung/{vid}/2"): V_FUEHREN,
    ("POST", "/admin/einrichtung/{vid}/3"): V_FUEHREN,
    ("POST", "/admin/einrichtung/{vid}/5"): V_FUEHREN,
    # Benutzer und Rollen (was der Vereinsadmin davon darf, prüft app/benutzer.py)
    ("GET", "/admin/benutzer"): BENUTZER,
    ("POST", "/admin/benutzer"): BENUTZER,
    ("POST", "/admin/benutzer/{bid}/rolle"): BENUTZER,
    ("POST", "/admin/benutzer/{bid}/rolle/{rid}/entziehen"): BENUTZER,
    ("POST", "/admin/benutzer/{bid}/aktiv"): BENUTZER,
    # Event-Admins einer Veranstaltung
    ("GET", "/admin/v/{vid}/admins"): V_ADMINS,
    ("POST", "/admin/v/{vid}/admins"): V_ADMINS,
    ("POST", "/admin/v/{vid}/admins/{rid}/entziehen"): V_ADMINS,
    # Verwalten (Vereinsadmin, Superadmin)
    ("POST", "/admin/v/{vid}/aktivieren"): V_VERWALTEN,
    ("POST", "/admin/v/{vid}/deaktivieren"): V_VERWALTEN,
    ("POST", "/admin/v/{vid}/abschliessen"): V_VERWALTEN,
    ("POST", "/admin/v/{vid}/anonymisieren"): V_VERWALTEN,
    ("POST", "/admin/v/{vid}/loeschen"): V_VERWALTEN,
}

# Seiten und Aktionen einer Veranstaltung: GET lesen, POST führen
for _pfad in (
    "", "/aufgaben", "/aushang", "/drucken", "/einstellungen", "/einstellungen/{tab}", "/ergebnis",
    "/export/ergebnis.csv", "/export/protokoll.csv", "/geraete", "/geraetedatei",
    "/geraetelinks-druck", "/import", "/import/entwurf.csv", "/import/vorschau", "/klassen",
    "/korrekturen", "/messpunkte", "/orte", "/papierprotokoll", "/protokoll", "/rennen",
    "/stammdaten", "/status", "/strecken", "/strecken/laden", "/strecken/{sid}",
    "/strecken/{sid}/datei", "/teilnehmer", "/teilnehmer/neu", "/teilnehmer/{tid}",
    "/teilnehmer/{tid}/bearbeiten", "/urkunden", "/zielmonitor",
):  # fmt: skip
    ROUTEN[("GET", f"/admin/v/{{vid}}{_pfad}")] = V_LESEN
for _pfad in (
    "/dsq", "/ergebnisstatus", "/geraetedatei", "/geraetedatei/uebernehmen", "/import",
    "/import/entwurf", "/import/uebernehmen", "/import/verwerfen", "/korrektur", "/messpunkte",
    "/oeffentlichkeit", "/orte", "/regeln", "/rennleitung", "/spalten", "/stammdaten",
    "/strecken/laden", "/strecken/neu", "/strecken/{sid}", "/strecken/{sid}/entfernen",
    "/strecken/uebernehmen", "/strecken/zuordnung", "/teilnehmer", "/teilnehmer/{tid}/loeschen",
    "/teilnehmer/{tid}/merkmal", "/token", "/zielmonitor",
):  # fmt: skip
    ROUTEN[("POST", f"/admin/v/{{vid}}{_pfad}")] = V_FUEHREN


class KeineBerechtigung(HTTPException):
    """403 mit eigener Seite; die Kopfzeile unterscheidet sie von anderen 403 (z. B. CSRF)."""

    def __init__(self):
        super().__init__(403, "Keine Berechtigung", headers={"X-TapTrial-Recht": "verweigert"})


def darf(person: Person | None, recht: str, v: Veranstaltung | None = None) -> bool:
    """Die eine Stelle für alle Rechte im Admin."""
    if recht == OFFEN:
        return True
    if person is None or not person.hat_rolle:
        return False
    if person.superadmin:
        return True
    vereinsadmin_hier = v is not None and v.verein_id in person.vereine
    if recht == ANGEMELDET:
        return True
    if recht in (BENUTZER, VORLAGEN, ANLEGEN):
        return bool(person.vereine)
    if recht == INSTALLATION:
        return False
    if recht in VERANSTALTUNGSRECHTE:
        if v is None:
            return False
        if vereinsadmin_hier:
            return True
        return recht in (V_LESEN, V_FUEHREN) and v.id in person.veranstaltungen
    raise ValueError(f"unbekanntes Recht {recht}")


def recht_der_route(methode: str, pfad: str) -> str | None:
    return ROUTEN.get(("GET" if methode == "HEAD" else methode, pfad))
