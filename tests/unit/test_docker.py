"""Release 5: Docker-Image (Nicht-root, Healthcheck /up, Daten unter /data, OCI-Labels), Compose mit
fertigem Image, Variante mit Caddy (automatisches HTTPS) und .env.example mit allen Variablen."""

import dataclasses
import re
from pathlib import Path

import yaml

from app.einstellungen import Einstellungen

PROJEKT = Path(__file__).resolve().parents[2]
IMAGE = "ghcr.io/flo-63/taptrial"


def lesen(name: str) -> str:
    return (PROJEKT / name).read_text(encoding="utf-8")


def test_dockerfile():
    d = lesen("Dockerfile")
    assert re.search(r"^USER zeitfahren$", d, re.MULTILINE)  # nicht root
    assert "HEALTHCHECK" in d and "http://127.0.0.1:8000/up" in d
    assert 'VOLUME ["/data"]' in d and "RCB_DB_PFAD=/data/zeitfahren.sqlite3" in d
    for label, wert in (("title", '"TapTrial"'), ("version", '"${VERSION}"'),
                        ("licenses", '"PolyForm-Noncommercial-1.0.0"'),
                        ("source", '"https://github.com/Flo-63/taptrial"')):  # fmt: skip
        assert f"org.opencontainers.image.{label}={wert}" in d, label
    assert "Caddy" in d and "COPY pyproject.toml" in d  # Version im Image lesbar


def test_compose_mit_fertigem_image():
    c = yaml.safe_load(lesen("docker-compose.yml"))
    app = c["services"]["app"]
    assert app["image"] == f"{IMAGE}:${{TAPTRIAL_VERSION:-latest}}"
    assert "taptrial-daten:/data" in app["volumes"] and app["env_file"] == ".env"
    assert "build" not in app


def test_compose_mit_caddy():
    c = yaml.safe_load(lesen("docker-compose.caddy.yml"))
    app, caddy = c["services"]["app"], c["services"]["caddy"]
    assert app["image"].startswith(IMAGE) and {"80:80", "443:443"} <= set(app["ports"])
    assert caddy["network_mode"] == "service:app"  # Caddy kommt von 127.0.0.1 → vertrauter Proxy
    assert "./Caddyfile:/etc/caddy/Caddyfile:ro" in caddy["volumes"]
    caddyfile = lesen("Caddyfile")
    assert "{$TAPTRIAL_DOMAIN}" in caddyfile and "reverse_proxy 127.0.0.1:8000" in caddyfile
    assert Einstellungen.fuer_tests().vertraute_proxys == "127.0.0.1,::1"


def test_env_example_vollstaendig():
    text = lesen(".env.example")
    for feld in dataclasses.fields(Einstellungen):
        assert f"RCB_{feld.name.upper()}" in text, feld.name
    for name in ("TAPTRIAL_DOMAIN", "TAPTRIAL_VERSION", "TAPTRIAL_PORT"):
        assert name in text
    assert re.search(r"^RCB_TOKEN_ADMIN=", text, re.MULTILINE)
    assert not re.search(r"^RCB_DB_PFAD=", text, re.MULTILINE)  # im Image fest auf /data
