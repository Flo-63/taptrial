"""Einstellungen aus Umgebungsvariablen (Präfix TAPTRIAL_, alte Namen RCB_ als Rückfall).

Start- und Ziel-Tokens liegen je Veranstaltung in der Datenbank;
aus der Umgebung kommt nur noch der Admin-Token (Pflicht). Eine PIN gibt es nicht mehr.
"""

import secrets
from dataclasses import dataclass, fields
from pathlib import Path

from app import umgebung

PROJEKT_DIR = Path(__file__).resolve().parent.parent
STANDARD_KLASSEN_DATEI = str(PROJEKT_DIR / "config" / "altersklassen.toml")
STANDARD_WERTUNGEN = str(PROJEKT_DIR / "config" / "wertungen")
PFLICHT = ("token_admin",)
MIN_PASSPHRASE = 24
PLATZHALTER = ("bitte-aendern",)  # steht in .env.example
ANMELDUNGEN = ("token", "proxy")


class EinstellungsFehler(RuntimeError):
    pass


def _passphrase_pruefen(wert: str) -> None:
    """Keine bekannte oder zu kurze Passphrase: Sie schützt Admin, API und Sitzungen."""
    name = umgebung.name("token_admin")
    tipp = "Bitte in .env ändern, z. B. mit „openssl rand -base64 32“."
    if wert.strip().lower() in PLATZHALTER:
        raise EinstellungsFehler(
            f"{name} ist noch der Platzhalter aus .env.example. App startet nicht. {tipp}"
        )
    if len(wert.strip()) < MIN_PASSPHRASE:
        raise EinstellungsFehler(
            f"{name} ist zu kurz (mindestens {MIN_PASSPHRASE} Zeichen). App startet nicht. {tipp}"
        )


@dataclass(frozen=True)
class Einstellungen:
    token_admin: str
    db_pfad: str = "data/zeitfahren.sqlite3"
    klassen_datei: str = STANDARD_KLASSEN_DATEI
    wertungen_verzeichnis: str = STANDARD_WERTUNGEN
    vertraute_proxys: str = "127.0.0.1,::1"  # nur von hier werden X-Forwarded-* ausgewertet
    # Anmeldung im Admin: „token“ (Passphrase) oder „proxy“ (Forward-Auth)
    anmeldung: str = "token"
    superadmins: str = ""  # Benutzernamen, durch Komma getrennt: immer Superadmin
    proxy_jwks_url: str = ""
    proxy_jwt_issuer: str = ""
    proxy_jwt_audience: str = (
        ""  # Pflicht für „proxy“: sonst gälte ein Token einer anderen Anwendung
    )
    proxy_gruppe: str = ""  # leer = nicht geprüft; sonst muss sie im JWT-Feld „groups“ stehen
    proxy_netze: str = ""  # leer = jeder Absender; sonst nur direkte Verbindungen aus diesen Netzen
    proxy_logout_url: str = ""
    proxy_header_uid: str = "X-Authentik-Uid"
    proxy_header_benutzername: str = "X-Authentik-Username"
    proxy_header_email: str = "X-Authentik-Email"
    proxy_header_name: str = "X-Authentik-Name"
    proxy_header_jwt: str = "X-Authentik-Jwt"

    @property
    def superadmin_namen(self) -> frozenset[str]:
        # genau so geschrieben wie beim Anmeldedienst: „Chefin“ und „chefin“ können zwei Personen sein
        return frozenset(n.strip() for n in self.superadmins.split(",") if n.strip())

    @property
    def proxy(self) -> bool:
        return self.anmeldung == "proxy"

    def proxy_fehlt(self) -> list[str]:
        """Pflichtangaben für „proxy“, die fehlen: dann ist keine Anmeldung möglich (fail closed),
        Geräte und öffentliche Seiten laufen weiter."""
        return [umgebung.name(f) for f in ("proxy_jwks_url", "proxy_jwt_issuer", "proxy_jwt_audience")
                if not getattr(self, f).strip()]  # fmt: skip

    @classmethod
    def aus_umgebung(cls) -> "Einstellungen":
        """Liest TAPTRIAL_*- (bzw. alte RCB_*-)Variablen. Der Admin-Token ist Pflicht, es gibt
        keinen Standardwert."""
        werte: dict[str, str] = {}
        felder = [f.name for f in fields(cls)]
        for feld in felder:
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
        _passphrase_pruefen(werte["token_admin"])
        werte["anmeldung"] = werte.get("anmeldung", "token").strip().lower()
        if werte["anmeldung"] not in ANMELDUNGEN:
            raise EinstellungsFehler(
                f"{umgebung.name('anmeldung')} muss „token“ oder „proxy“ sein, nicht "
                f"„{werte['anmeldung']}“. App startet nicht."
            )
        url = werte.get("proxy_jwks_url", "")
        if url and not url.startswith(("https://", "http://")):
            raise EinstellungsFehler(
                f"{umgebung.name('proxy_jwks_url')} muss mit https:// beginnen. App startet nicht."
            )
        if werte.get("proxy_netze"):
            from app.proxy import Netze

            try:
                Netze.aus_text(werte["proxy_netze"], umgebung.name("proxy_netze"))
            except ValueError as e:
                raise EinstellungsFehler(f"{e}. App startet nicht.") from e
        return cls(**werte)

    @classmethod
    def fuer_tests(cls, **werte: str) -> "Einstellungen":
        """Nur für Tests: ein fehlender Admin-Token wird mit einem Zufallswert belegt."""
        zufall = {
            "token_admin": secrets.token_urlsafe(16),
        }
        return cls(**{**zufall, **werte})
