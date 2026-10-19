"""Zentrales Stylesheet (ADMIN-REVIEW Schritt 1): Tokens, Bausteine, lokale Schrift."""

import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"
CSS = APP / "static" / "css"
FARBE = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")

TOKENS = ["--ground", "--surface", "--line", "--text", "--muted", "--brand", "--brand-ink",
          "--brand-soft", "--ok", "--ok-soft", "--warn", "--warn-soft", "--danger", "--danger-soft",
          "--info-soft", "--font", "--mono", "--r", "--r-sm", "--s4"]  # fmt: skip
BAUSTEINE = [".btn-primary", ".btn-secondary", ".btn-quiet", ".btn-danger", ".btn-sm", ".badge",
             ".card", ".alert", ".a-ok", ".a-info", ".a-warn", ".a-danger", ".tbl", ".field",
             ".page-head", ".danger-zone", "dialog.dialog"]  # fmt: skip


def ohne_tokenbloecke(text: str) -> str:
    """CSS ohne die :root-Blöcke (dort stehen die Farbwerte)."""
    return re.sub(r":root\s*\{[^}]*\}", "", text)


def test_tokens_und_bausteine():
    text = (CSS / "basis.css").read_text(encoding="utf-8")
    for t in TOKENS:
        assert re.search(rf"{t}:\s", text), t
    for b in BAUSTEINE:
        assert b in text, b
    assert "--brand: #1F4E8C" in text and "--ground: #F3F4F6" in text


def test_schrift_lokal_mit_lizenz():
    text = (CSS / "basis.css").read_text(encoding="utf-8")
    urls = re.findall(r'url\("(/static/fonts/[^"]+)"\)', text)
    assert len(urls) >= 5 and any("mono" in u for u in urls)
    for u in urls:
        assert (APP / u.removeprefix("/")).is_file(), u
    assert "Open Font License" in (APP / "static/fonts/OFL.txt").read_text(encoding="utf-8")
    for vorlage in (APP / "templates").glob("*.html"):
        assert "fonts.googleapis" not in vorlage.read_text(encoding="utf-8"), vorlage.name


@pytest.mark.parametrize("datei", ["basis.css", "admin.css", "oeffentlich.css", "hilfe.css"])
def test_keine_farben_ausserhalb_der_tokens(datei):
    text = ohne_tokenbloecke((CSS / datei).read_text(encoding="utf-8"))
    assert not FARBE.findall(text), FARBE.findall(text)


@pytest.mark.parametrize("vorlage", ["admin_basis.html", "ergebnisse.html", "ergebnisse_druck.html",
                                     "monitor.html", "startseite.html", "hilfe.html"])  # fmt: skip
def test_seiten_laden_basis_zuerst(vorlage):
    text = (APP / "templates" / vorlage).read_text(encoding="utf-8")
    links = re.findall(r'href="/static/css/([a-z]+)\.css(?:\?v=[^"]*)?"', text)  # Segmente 62: ?v=
    assert links[0] == "basis" and 2 <= len(links) <= 3, links
