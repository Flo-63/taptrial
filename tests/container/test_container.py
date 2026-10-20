"""Container-Tests: Image bauen, starten, Healthcheck, Pflicht-Secrets, nicht als root.

Werden übersprungen, wenn Docker nicht verfügbar ist. Die E2E-Suite gegen den Container
läuft mit `scripts/container_e2e.sh` (setzt TAPTRIAL_E2E_IMAGE).
"""

import shutil
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import pytest

from tests.e2e.conftest import TOKENS, _freier_port, _seed

PROJEKT = Path(__file__).resolve().parents[2]
IMAGE = "rcb-zeitfahren:test"


def _docker_ok() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True, check=False).returncode == 0


pytestmark = pytest.mark.skipif(not _docker_ok(), reason="Docker nicht verfügbar")


def docker(*args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=False, **kw)


@pytest.fixture(scope="module")
def image() -> str:
    r = docker("build", "-t", IMAGE, str(PROJEKT), timeout=900)
    assert r.returncode == 0, r.stderr[-3000:]
    return IMAGE


@pytest.fixture
def container(image, tmp_path):
    tmp_path.chmod(0o777)
    _seed(str(tmp_path / "zeitfahren.sqlite3"), 3)
    for f in tmp_path.iterdir():
        f.chmod(0o666)
    name = f"rcb-test-{uuid.uuid4().hex[:8]}"
    port = _freier_port()
    r = docker(
        "run", "-d", "--name", name, "-p", f"127.0.0.1:{port}:8000",
        "-v", f"{tmp_path}:/data",
        "-e", f"TAPTRIAL_TOKEN_ADMIN={TOKENS['admin']}",
        image,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(f"{url}/up").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        else:
            raise AssertionError(docker("logs", name).stderr)
        yield name, url
    finally:
        docker("rm", "-f", name)


def test_up_und_daten_vom_volume(container):
    _, url = container
    assert httpx.get(f"{url}/up").json() == {"status": "ok"}
    stand = httpx.get(f"{url}/api/stand", headers={"Authorization": f"Bearer {TOKENS['ziel']}"})
    assert [t["startnummer"] for t in stand.json()["teilnehmer"]] == [1, 2, 3]
    assert httpx.get(f"{url}/start").status_code == 200
    assert "rcb-shell-" in httpx.get(f"{url}/sw.js").text
    assert "Kurzanleitung Start" in httpx.get(f"{url}/hilfe/start").text  # Handbücher im Image
    assert httpx.get(f"{url}/hilfe/bilder/start.png").status_code == 200


def test_db_im_wal_modus(container):
    name, _ = container
    r = docker(
        "exec", name, "python", "-c",
        "import sqlite3; print(sqlite3.connect('/data/zeitfahren.sqlite3')"
        ".execute('PRAGMA journal_mode').fetchone()[0])",
    )  # fmt: skip
    assert r.stdout.strip() == "wal", r.stderr


def test_healthcheck_wird_healthy(container):
    name, _ = container
    for _ in range(60):
        r = docker("inspect", "-f", "{{.State.Health.Status}}", name)
        if r.stdout.strip() == "healthy":
            return
        time.sleep(1)
    raise AssertionError(f"Healthcheck: {r.stdout}")


def test_laeuft_nicht_als_root(container):
    name, _ = container
    assert docker("exec", name, "id", "-u").stdout.strip() != "0"


def test_ein_prozess_uvicorn(image):
    r = docker(
        "image", "inspect", "-f", "{{json .Config.Cmd}} {{json .Config.ExposedPorts}}", image
    )
    assert "uvicorn" in r.stdout and "8000/tcp" in r.stdout
    assert "--no-proxy-headers" in r.stdout and "forwarded-allow-ips" not in r.stdout


def test_ohne_secrets_kein_start(image):
    r = docker("run", "--rm", image, timeout=60)
    assert r.returncode != 0
    assert "TAPTRIAL_TOKEN_ADMIN" in r.stdout + r.stderr


def test_verwaltung_im_container(image, tmp_path):
    tmp_path.chmod(0o777)
    csv = "startnummer;vorname;nachname;geschlecht;geburtsjahr\n1;Anna;A;w;1990\n"
    r = docker(
        "run", "--rm", "-i", "-v", f"{tmp_path}:/data", "-e", "TAPTRIAL_DB_PFAD=/data/x.sqlite3",
        image, "sh", "-c",
        "python -m app.verwaltung veranstaltung Test 2026-10-10 && "
        "python -m app.verwaltung import -",
        input=csv, timeout=60,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert "1 Teilnehmer" in r.stdout


def test_konfiguration_im_image(image):
    r = docker("run", "--rm", "--entrypoint", "ls", image, "/app/config", "/app/config/wertungen")
    assert r.returncode == 0, r.stderr
    assert "altersklassen.toml" in r.stdout
    assert "radsportabzeichen-a1-flach.toml" in r.stdout
    if (Path(__file__).resolve().parents[2] / "config" / "deploy.yml").exists():  # fehlt im Export
        assert "deploy.yml" in r.stdout  # unkritisch, enthält keine Secrets
