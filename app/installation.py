"""Globale Einstellungen der Installation (nicht je Veranstaltung): Vereinsname aus dem Branding,
Impressum und Datenschutz. Gespeichert als Schlüssel/Wert in
`app_einstellung`; fehlt ein Schlüssel, gilt „“ (frische Installation: neutral, ohne Verein)."""

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app import marke
from app.models import AppEinstellung

SCHLUESSEL = (
    "vereinsname",
    "impressum_url",
    "impressum_text",
    "datenschutz_url",
    "datenschutz_text",
)
MAX_LAENGE = {"vereinsname": 100, "impressum_url": 500, "datenschutz_url": 500,
              "impressum_text": 20000, "datenschutz_text": 20000}  # fmt: skip
RECHTLICHES = {"impressum": "Impressum", "datenschutz": "Datenschutzerklärung"}


class EingabeFehler(ValueError):
    pass


def lesen(session) -> dict[str, str]:
    werte = dict.fromkeys(SCHLUESSEL, "")
    for e in session.scalars(select(AppEinstellung)):
        if e.schluessel in werte:
            werte[e.schluessel] = e.wert or ""
    return werte


def speichern(session, werte: dict[str, str]) -> None:
    for schluessel, wert in werte.items():
        if schluessel not in SCHLUESSEL:
            raise KeyError(schluessel)
        e = session.get(AppEinstellung, schluessel)
        if e is None:
            session.add(AppEinstellung(schluessel=schluessel, wert=wert))
        else:
            e.wert = wert
    session.commit()


def fuer_vorlage(request) -> dict[str, str]:
    """Für Jinja (`installation(request)`): liest mit einer eigenen, kurzen Sitzung."""
    try:
        with request.app.state.sessionmaker() as session:
            return lesen(session)
    except (AttributeError, SQLAlchemyError):  # z. B. ohne App-Zustand – dann neutral
        return dict.fromkeys(SCHLUESSEL, "")


def vereinsname(request) -> str:
    """v0.1.1: Ist ein Vereinsname gesetzt, steht er vorn; „TapTrial“ nur noch klein."""
    return fuer_vorlage(request)["vereinsname"].strip()


def titel_zusatz(request) -> str:
    """Zusatz im Seitentitel: „<Verein> · TapTrial“ oder neutral „TapTrial“."""
    verein = vereinsname(request)
    return f"{verein} · {marke.NAME}" if verein else marke.NAME


def kuerzel(name: str) -> str:
    """Zeichen im Admin-Kopf: kurze Namen ganz („RCB“), sonst die Anfangsbuchstaben (max. 3)."""
    name = name.strip()
    if len(name) <= 4 and " " not in name:
        return name.upper()
    return "".join(w[0] for w in name.split() if w[:1].isalnum())[:3].upper() or marke.NAME[:2]


def rechtliches_pruefen(form) -> dict[str, str]:
    """Je Seite (Impressum, Datenschutz) ein Link (https) **oder** ein Text, beides leer
    erlaubt. Gibt die zu speichernden Werte zurück, sonst EingabeFehler."""
    werte = {}
    for art, titel in RECHTLICHES.items():
        url = str(form.get(f"{art}_url") or "").strip()
        text = str(form.get(f"{art}_text") or "").replace("\r\n", "\n").strip()
        if url and text:
            raise EingabeFehler(
                f"{titel}: entweder einen Link oder einen Text angeben, nicht beides."
            )
        if url and (not url.startswith("https://") or " " in url or len(url) < 12):
            raise EingabeFehler(f"{titel}: Der Link muss mit https:// beginnen.")
        for schluessel, wert in ((f"{art}_url", url), (f"{art}_text", text)):
            if len(wert) > MAX_LAENGE[schluessel]:
                raise EingabeFehler(f"{titel}: höchstens {MAX_LAENGE[schluessel]} Zeichen.")
            werte[schluessel] = wert
    return werte


def rechtliches_links(werte: dict[str, str]) -> list[tuple[str, str]]:
    """Links für Fußzeilen: (Titel, Adresse) – externer Link oder eigene Seite (/impressum)."""
    links = []
    for art, titel in (("impressum", "Impressum"), ("datenschutz", "Datenschutz")):
        if werte.get(f"{art}_url"):
            links.append((titel, werte[f"{art}_url"]))
        elif werte.get(f"{art}_text"):
            links.append((titel, f"/{art}"))
    return links


def rechtliches_fehlt(werte: dict[str, str]) -> list[str]:
    return [titel for art, titel in RECHTLICHES.items()
            if not (werte.get(f"{art}_url") or werte.get(f"{art}_text"))]  # fmt: skip
