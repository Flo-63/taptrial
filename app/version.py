"""Version der App: eine Quelle der Wahrheit – `version` in `pyproject.toml`.

Im Docker-Image liegt `pyproject.toml` unter /app; fehlt sie (z. B. als installiertes Paket), gilt
die Paket-Metadaten-Version. `/version` liefert sie als JSON, das Image trägt sie als Label.
"""

import tomllib
from importlib import metadata
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _lesen() -> str:
    try:
        return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        try:
            return metadata.version("rcb-zeitfahren")
        except metadata.PackageNotFoundError:
            return "0.0.0"


VERSION = _lesen()
