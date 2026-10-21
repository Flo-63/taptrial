"""Versionsabgleich vor jeder Veröffentlichung: `pyproject.toml`, `app/version.py` und CHANGELOG
nennen dieselbe Version – sonst bricht der Image-Build am Tag ab („Tag passt nicht zu pyproject“).
Ohne feste Versionsnummer, damit der Test beim nächsten Versionssprung unverändert gilt."""

import re
import tomllib
from pathlib import Path

from app import version

PROJEKT = Path(__file__).resolve().parents[2]


def pyproject_version() -> str:
    return tomllib.loads((PROJEKT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]


def test_pyproject_und_app_version_gleich():
    assert re.fullmatch(r"\d+\.\d+\.\d+", pyproject_version())
    assert version.VERSION == pyproject_version()
    assert version._lesen() == pyproject_version()  # nicht über den Rückfall „0.0.0“


def test_changelog_hat_eintrag_mit_datum():
    text = (PROJEKT / "CHANGELOG.md").read_text(encoding="utf-8")
    v = re.escape(pyproject_version())
    assert re.search(rf"^## \[{v}\] – \d{{4}}-\d{{2}}-\d{{2}}$", text, re.MULTILINE), (
        f"CHANGELOG.md: Abschnitt „## [{pyproject_version()}] – JJJJ-MM-TT“ fehlt"
    )
