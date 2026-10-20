"""E2E: Uhrabweichung messen und warnen, ohne Korrektur der Zeiten."""

import re

import httpx
from playwright.sync_api import expect

from tests.e2e.conftest import TOKENS, oeffnen, offline, warte_bis


def _verstellen(ctx, ms: int) -> None:
    """Geräteuhr um ms verstellen (Date.now und new Date())."""
    ctx.add_init_script(f"""(() => {{
      const Echt = Date, versatz = {ms};
      class Verstellt extends Echt {{
        constructor(...a) {{ if (a.length) super(...a); else super(Echt.now() + versatz); }}
        static now() {{ return Echt.now() + versatz; }}
      }}
      globalThis.Date = Verstellt;
    }})();""")


def test_uhr_stimmt_und_offline_ungeprueft(server, neues_geraet):
    ctx = neues_geraet()
    page = oeffnen(ctx, server, "ziel")
    expect(page.locator("#uhr-pruefung")).to_have_text(re.compile(r"^Uhr ±0,[0-4] s$"))
    expect(page.locator("#uhr-pruefung")).not_to_have_class(re.compile("uhr-warnung"))
    offline(ctx, True)
    expect(page.locator("#uhr-pruefung")).to_have_text("Uhr ungeprüft")


def test_verstellte_uhr_warnt_und_admin_zeigt_abweichung(server, neues_geraet):
    ctx = neues_geraet()
    _verstellen(ctx, -12000)  # Gerät geht 12 s nach → Abweichung Server − Gerät ≈ +12,0 s
    page = oeffnen(ctx, server, "start")
    expect(page.locator("#uhr-pruefung")).to_have_text("Uhr weicht 12,0 s ab – Gerät prüfen")
    expect(page.locator("#uhr-pruefung")).to_have_class(re.compile("uhr-warnung"))
    farbe = page.locator("#uhr-pruefung").evaluate("e => getComputedStyle(e).backgroundColor")
    assert farbe not in ("rgba(0, 0, 0, 0)", "transparent")
    # die Warnung verdrängt die Uhrzeit nicht aus der Statusleiste (feste Höhe)
    leiste = page.locator("#statusleiste").bounding_box()
    uhr = page.locator("#uhr").bounding_box()
    assert uhr["y"] + uhr["height"] <= leiste["y"] + leiste["height"]
    # Startzeit wird nicht korrigiert: sie bleibt in der (verstellten) Geräteuhr
    expect(page.locator("#naechster-nr")).to_have_text("1")
    vorher = page.evaluate("Date.now()")
    page.click("#btn-start")
    warte_bis(lambda: server.teilnehmer(1)["start_ms"] is not None, text="Start übertragen")
    start_ms = server.teilnehmer(1)["start_ms"]
    assert vorher <= start_ms <= page.evaluate("Date.now()")
    # beim nächsten Sync meldet das Gerät die Abweichung mit; Admin „Geräte“ zeigt sie
    page.evaluate("window.geraet.sync()")
    with httpx.Client(base_url=server.url, follow_redirects=True) as c:
        c.post("/admin/anmelden", data={"token": TOKENS["admin"]})
        html = c.get("/admin/v/1/geraete").text
    geraet_id = page.evaluate("localStorage.getItem('rcb_geraet_start')")
    assert geraet_id in html and "+12,0 s – Gerät prüfen" in html
