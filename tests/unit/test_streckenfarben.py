"""Streckenfarben: ohne Rot/Orange/Gelb, kein Grün, gut unterscheidbar, Kontrast WCAG AA."""

import colorsys
import itertools
import re
from pathlib import Path

import pytest

CSS = Path(__file__).resolve().parents[2] / "app" / "static" / "css"


def farben(datei: str) -> list[str]:
    """Palette aus app.css (Geräte) bzw. den Tokens --strecke-N in basis.css (alle anderen)."""
    text = (CSS / datei).read_text(encoding="utf-8")
    muster = (
        r"--strecke-{i}: (#[0-9a-fA-F]{{6}});"
        if datei == "basis.css"
        else (r"\.strecke-farbe-{i} {{ background: (#[0-9a-f]{{6}}); }}")
    )
    return [re.search(muster.format(i=i), text).group(1).lower() for i in range(6)]


def luminanz(hexwert: str) -> float:
    def kanal(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hexwert[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * kanal(r) + 0.7152 * kanal(g) + 0.0722 * kanal(b)


@pytest.mark.parametrize("datei", ["app.css", "basis.css"])
def test_palette(datei):
    liste = farben(datei)
    assert liste == farben("app.css")  # überall dieselbe Palette
    for f in liste:
        assert 1.05 / (luminanz(f) + 0.05) >= 4.5, f  # weißer Text: WCAG AA
        r, g, b = (int(f[i : i + 2], 16) / 255 for i in (1, 3, 5))
        farbton, saettigung, _ = colorsys.rgb_to_hsv(r, g, b)
        grad = farbton * 360
        if saettigung > 0.2:  # neutrale Töne haben keinen nennenswerten Farbton
            assert not (grad < 60 or grad >= 330), f"{f}: Rot/Orange/Gelb ({grad:.0f}°)"
            assert not 75 <= grad < 160, f"{f}: Grün ({grad:.0f}°)"
    for a, b in itertools.combinations(liste, 2):
        abstand = (
            sum((int(a[i : i + 2], 16) - int(b[i : i + 2], 16)) ** 2 for i in (1, 3, 5)) ** 0.5
        )
        assert abstand >= 50, (a, b)
