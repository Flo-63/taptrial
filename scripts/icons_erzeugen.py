"""Erzeugt die PNG-App-Icons aus app/static/icon.svg (Chromium via Playwright).

    uv run python scripts/icons_erzeugen.py

PNG-Icons sind vollflächig (ohne abgerundete Ecken), iOS und Android runden selbst.
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
GROESSEN = (180, 192, 512)


def main() -> None:
    svg = (STATIC / "icon.svg").read_text(encoding="utf-8").replace(' rx="96"', "")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for groesse in GROESSEN:
            page = browser.new_page(viewport={"width": groesse, "height": groesse})
            page.set_content(
                f"<html><body style='margin:0'><div style='width:{groesse}px;height:{groesse}px'>"
                f"{svg.replace('<svg ', '<svg width="100%" height="100%" ')}</div></body></html>"
            )
            ziel = STATIC / "icons" / f"icon-{groesse}.png"
            page.screenshot(path=str(ziel), omit_background=False)
            page.close()
            print(ziel)
        browser.close()


if __name__ == "__main__":
    main()
