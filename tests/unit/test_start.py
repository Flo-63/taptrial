import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest


def _freier_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.parametrize("praefix", ["TAPTRIAL_", "RCB_"])  # alter Name: bestehender Betrieb
def test_uvicorn_startet(tmp_path, praefix):
    port = _freier_port()
    env = {
        f"{praefix}DB_PFAD": str(tmp_path / "x.sqlite3"),
        "PATH": "/usr/bin:/bin",
        f"{praefix}TOKEN_ADMIN": "a",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
        env=env,
        cwd=Path(__file__).resolve().parents[2],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(50):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/up") as r:
                    assert r.status == 200
                    return
            except OSError:
                time.sleep(0.1)
        raise AssertionError("uvicorn ist nicht gestartet")
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_uvicorn_startet_nicht_ohne_tokens(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(_freier_port())],
        env={"TAPTRIAL_DB_PFAD": str(tmp_path / "x.sqlite3"), "PATH": "/usr/bin:/bin"},
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert proc.returncode != 0
    assert "TAPTRIAL_TOKEN_ADMIN" in proc.stderr and "App startet nicht" in proc.stderr
    assert "Admin-Passphrase" in proc.stderr and ".env.example" in proc.stderr
