"""Einstellungen aus Umgebungsvariablen (Präfix TAPTRIAL_, alte Namen RCB_ als Rückfall).

Start- und Ziel-Tokens liegen je Veranstaltung in der Datenbank;
aus der Umgebung kommt nur noch der Admin-Token (Pflicht). Eine PIN gibt es nicht mehr.
"""

import secrets
from dataclasses import dataclass
from pathlib import Path

from app import umgebung

PROJEKT_DIR = Path(__file__).resolve().parent.parent
STANDARD_KLASSEN_DATEI = str(PROJEKT_DIR / "config" / "altersklassen.toml")
STANDARD_WERTUNGEN = str(PROJEKT_DIR / "config" / "wertungen")
PFLICHT = ("token_admin",)


class EinstellungsFehler(RuntimeError):
    pass


@dataclass(frozen=True)
class Einstellungen:
    token_admin: str
    db_pfad: str = "data/zeitfahren.sqlite3"
    klassen_datei: str = STANDARD_KLASSEN_DATEI
    wertungen_verzeichnis: str = STANDARD_WERTUNGEN
    vertraute_proxys: str = "127.0.0.1,::1"  # nur von hier werden X-Forwarded-* ausgewertet

    @classmethod
    def aus_umgebung(cls) -> "Einstellungen":
        """Liest TAPTRIAL_*- (bzw. alte RCB_*-)Variablen. Der Admin-Token ist Pflicht, es gibt
        keinen Standardwert."""
        werte: dict[str, str] = {}
        for feld in (*PFLICHT, "db_pfad", "klassen_datei", "wertungen_verzeichnis",
                     "vertraute_proxys"):  # fmt: skip
            wert = umgebung.wert(feld)
            if wert:
                werte[feld] = wert
        fehlend = [umgebung.name(f) for f in PFLICHT if f not in werte]
        if fehlend:
            raise EinstellungsFehler(
                f"Pflicht-Umgebungsvariablen fehlen: {', '.join(fehlend)}. App startet nicht. "
                "TAPTRIAL_TOKEN_ADMIN ist die Admin-Passphrase (lange, zufällige Zeichenfolge, z. B. "
                "„openssl rand -base64 32“) – in .env eintragen (Vorlage .env.example)."
            )
        return cls(**werte)

    @classmethod
    def fuer_tests(cls, **werte: str) -> "Einstellungen":
        """Nur für Tests: ein fehlender Admin-Token wird mit einem Zufallswert belegt."""
        zufall = {
            "token_admin": secrets.token_urlsafe(16),
        }
        return cls(**{**zufall, **werte})
