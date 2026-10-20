"""Umgebungsvariablen: `TAPTRIAL_<NAME>`, die alten Namen `RCB_<NAME>` gelten weiter als Rückfall.

Der neue Name gewinnt. Wird ein alter Name benutzt, steht einmal je Prozess und Variable eine
Hinweiszeile „veraltet“ im Log – bestehende Installationen laufen ohne Änderung weiter.
"""

import logging
import os

NEU = "TAPTRIAL_"
ALT = "RCB_"
_log = logging.getLogger("app.umgebung")
_gemeldet: set[str] = set()


def name(feld: str) -> str:
    """Neuer Variablenname, z. B. `name("token_admin")` → „TAPTRIAL_TOKEN_ADMIN“."""
    return f"{NEU}{feld.upper()}"


def wert(feld: str, standard: str | None = None) -> str | None:
    """Wert der Variablen (leer = nicht gesetzt): erst TAPTRIAL_…, dann RCB_…, sonst standard."""
    neu = os.environ.get(name(feld), "").strip()
    if neu:
        return neu
    alt_name = f"{ALT}{feld.upper()}"
    alt = os.environ.get(alt_name, "").strip()
    if alt:
        if alt_name not in _gemeldet:
            _gemeldet.add(alt_name)
            _log.warning("Umgebungsvariable %s ist veraltet – bitte %s verwenden (gilt weiter).",
                         alt_name, name(feld))  # fmt: skip
        return alt
    return standard
