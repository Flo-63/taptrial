"""Release 6: GitHub Actions – Tests bei Push auf main (ruff, pytest ohne E2E), Multi-Arch-Image bei
Tag v* nach ghcr.io/<konto>/taptrial:<version> und :latest, nur mit GITHUB_TOKEN."""

import re
from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def lade(name: str) -> tuple[dict, str]:
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    daten = yaml.safe_load(text)
    daten["on"] = daten.pop(True, daten.get("on"))  # YAML 1.1: „on“ wird zu True
    return daten, text


def test_tests_bei_push_auf_main():
    w, _ = lade("tests.yml")
    assert w["on"] == {"push": {"branches": ["main"]}}
    schritte = " ".join(str(s.get("run", "")) for s in w["jobs"]["tests"]["steps"])
    assert "ruff check" in schritte and "ruff format --check" in schritte
    assert "pytest -q --ignore=tests/e2e" in schritte and "uv sync --frozen" in schritte
    assert w["permissions"] == {"contents": "read"}


def test_image_bei_tag():
    w, text = lade("image.yml")
    assert w["on"] == {"push": {"tags": ["v*"]}}
    assert w["permissions"] == {"contents": "read", "packages": "write"}
    bauen = next(s for s in w["jobs"]["image"]["steps"] if "build-push-action" in s.get("uses", ""))
    assert bauen["with"]["platforms"] == "linux/amd64,linux/arm64" and bauen["with"]["push"] is True
    assert ":${{ steps.version.outputs.version }}" in bauen["with"]["tags"]
    assert ":latest" in bauen["with"]["tags"] and "VERSION=" in bauen["with"]["build-args"]
    assert "ghcr.io/${GITHUB_REPOSITORY_OWNER,,}/taptrial" in text  # GHCR verlangt Kleinschreibung


def test_keine_geheimnisse_ausser_github_token():
    for datei in WORKFLOWS.glob("*.yml"):
        geheim = set(re.findall(r"secrets\.([A-Za-z_]+)", datei.read_text(encoding="utf-8")))
        assert geheim <= {"GITHUB_TOKEN"}, (datei.name, geheim)
