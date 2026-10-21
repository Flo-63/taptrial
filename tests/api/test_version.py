"""Version aus einer Quelle (pyproject.toml), sichtbar im Admin, klein auf öffentlichen
Seiten und in der Hilfe, unter /version und als Image-Label; CHANGELOG mit Eintrag zur Version."""

import re
import tomllib
from pathlib import Path

from app.version import VERSION
from tests.api.test_einrichtung import admin  # noqa: F401 (Fixture)

PROJEKT = Path(__file__).resolve().parents[2]


def test_eine_quelle():
    pyproject = tomllib.loads((PROJEKT / "pyproject.toml").read_text(encoding="utf-8"))
    assert VERSION == pyproject["project"]["version"] == "0.1.2"
    docker = (PROJEKT / "Dockerfile").read_text(encoding="utf-8")
    assert f"ARG VERSION={VERSION}" in docker  # Vorgabe im Image = pyproject
    assert 'org.opencontainers.image.version="${VERSION}"' in docker


def test_version_json(client):
    d = client.get("/version").json()
    assert d["name"] == "TapTrial" and d["version"] == VERSION and d["oberflaeche"]


def test_version_sichtbar(client, admin):  # noqa: F811
    assert re.search(
        rf'id="admin-version">TapTrial {re.escape(VERSION)}<', admin.get("/admin").text
    )
    for pfad in ("/", "/ergebnisse"):
        assert re.search(
            rf'class="app-version">TapTrial {re.escape(VERSION)}<', client.get(pfad).text
        ), pfad
    assert f"Version {VERSION}" in client.get("/hilfe/start").text
    assert f"erstellt mit TapTrial {VERSION}" in client.get("/ergebnisse/druck").text
    assert VERSION not in client.get("/monitor").text.split("<script")[0].split("</head>")[1]


def test_changelog():
    text = (PROJEKT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "Keep a Changelog" in text and re.search(
        rf"^## \[{re.escape(VERSION)}\]", text, re.MULTILINE
    )
