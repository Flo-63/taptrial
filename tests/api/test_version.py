"""Version aus einer Quelle (pyproject.toml), sichtbar im Admin, klein auf öffentlichen
Seiten und in der Hilfe, unter /version und als Image-Label; CHANGELOG mit Eintrag zur Version."""

import re
import subprocess
import tomllib
from pathlib import Path

from app.version import VERSION
from tests.api.test_einrichtung import admin  # noqa: F401 (Fixture)

PROJEKT = Path(__file__).resolve().parents[2]


def test_eine_quelle():
    """Die Nummer steht nur in pyproject.toml (und im daraus erzeugten uv.lock); Dockerfile,
    eigener Deploy und Workflows bekommen sie als Build-Argument."""
    pyproject = tomllib.loads((PROJEKT / "pyproject.toml").read_text(encoding="utf-8"))
    assert VERSION == pyproject["project"]["version"]
    lock = tomllib.loads((PROJEKT / "uv.lock").read_text(encoding="utf-8"))
    (projekt,) = [p for p in lock["package"] if p["name"] == pyproject["project"]["name"]]
    assert projekt["version"] == VERSION, "uv.lock veraltet – uv lock ausführen"
    docker = (PROJEKT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^ARG VERSION$", docker, re.MULTILINE)  # ohne Vorgabe
    assert not re.search(r"^ARG VERSION=", docker, re.MULTILINE)
    assert 'test -n "$VERSION"' in docker  # ohne Build-Argument bricht der Build ab
    assert 'org.opencontainers.image.version="${VERSION}"' in docker
    workflow = (PROJEKT / ".github" / "workflows" / "image.yml").read_text(encoding="utf-8")
    assert "build-args: VERSION=${{ steps.version.outputs.version }}" in workflow
    for skript in ("container_e2e.sh", "release-check.sh"):
        assert '--build-arg "VERSION=$version"' in (PROJEKT / "scripts" / skript).read_text(), (
            skript
        )
    deploy = PROJEKT / "config" / "deploy.yml"  # nicht im Export
    if deploy.exists():
        import yaml

        args = yaml.safe_load(deploy.read_text(encoding="utf-8"))["builder"]["args"]
        assert args == {"VERSION": '<%= File.read("pyproject.toml")[/^version = "([^"]+)"/, 1] %>'}


def test_anleitungen_bauen_mit_version():
    """Jedes „docker build“ in den öffentlichen Anleitungen übergibt VERSION – sonst bricht der
    Build ab; der Befehl aus der README ergibt die Version aus pyproject.toml."""
    dateien = [PROJEKT / "README.md", PROJEKT / "docs" / "BETRIEB.md",
               *(PROJEKT / "docs" / "handbuch").glob("*.md")]  # fmt: skip
    befehle = [z.strip() for d in dateien for z in d.read_text(encoding="utf-8").splitlines()
               if re.match(r"\s*docker build\b", z)]  # fmt: skip
    assert befehle
    for befehl in befehle:
        assert "--build-arg VERSION=" in befehl, befehl
    (readme,) = [b for b in befehle if b.endswith("-t taptrial .")]
    arg = readme.split("--build-arg ", 1)[1].rsplit(" -t ", 1)[0]
    r = subprocess.run(["bash", "-c", f"printf '%s' {arg}"], cwd=PROJEKT, capture_output=True,
                       text=True, check=True)  # fmt: skip
    assert r.stdout == f"VERSION={VERSION}"


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
