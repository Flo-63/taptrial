"""Kurzanleitungen: gedruckt genau eine DIN-A4-Seite; Hilfe-Link auf den Geräten; Screenshot-Skript."""

import re
import subprocess
import sys
from pathlib import Path

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import PROJEKT, oeffnen


@pytest.mark.parametrize("name", ["start", "ziel", "rennleitung", "monitor", "posten"])
def test_kurzanleitung_passt_auf_eine_a4_seite(server, browser, name):
    page = browser.new_page()
    page.goto(f"{server.url}/hilfe/{name}")
    page.wait_for_load_state("networkidle")
    pdf = page.pdf(format="A4", prefer_css_page_size=True)
    seiten = len(re.findall(rb"/Type\s*/Page[^s]", pdf))
    assert seiten == 1, f"{name}: {seiten} Seiten"
    page.close()


def test_hilfe_link_im_ziel_wechselt_mit_der_sicht(server, neues_geraet):
    ziel = oeffnen(neues_geraet(), server, "ziel")
    expect(ziel.locator("#link-hilfe")).to_have_attribute("href", "/hilfe/ziel")
    ziel.click("#tab-rennleitung")
    expect(ziel.locator("#link-hilfe")).to_have_attribute("href", "/hilfe/rennleitung")
    ziel.click("#link-hilfe")
    expect(ziel.locator("h1")).to_have_text("Kurzanleitung Rennleitung")


def test_screenshot_skript(tmp_path):
    r = subprocess.run(
        [sys.executable, "scripts/screenshots.py", "--ziel", str(tmp_path)],
        cwd=PROJEKT, capture_output=True, text=True, timeout=240, check=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr[-2000:]
    erwartet = {p.name for p in Path(PROJEKT / "docs" / "handbuch" / "bilder").glob("*.png")}
    erzeugt = {p.name for p in tmp_path.glob("*.png")}
    assert erwartet <= erzeugt
    assert all((tmp_path / n).stat().st_size > 10_000 for n in erzeugt)
