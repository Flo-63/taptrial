"""E2E: Die Statusleiste behält ihre Höhe; Uhrzeit und Verbindung bleiben immer ganz sichtbar."""

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import oeffnen, offline
from tests.e2e.test_uhr import _verstellen

ROLLEN = ["start", "ziel", "posten", "rennleitung"]
HOEHE = 52
# breiter als jede übliche Handy-Schrift: DejaVu Sans und zusätzlich gesperrt
BREITE_SCHRIFT = "body { font-family: 'DejaVu Sans' !important; letter-spacing: 0.06em; }"
LANGER_ORT = "Start · Steinbruch am Nordwesthang"


@pytest.fixture
def rundkurs():
    return "messpunkte"


def _oeffnen(ctx, server, rolle, breite):
    seite = server.seite(rolle) if rolle != "rennleitung" else None
    page = ctx.new_page()
    page.set_viewport_size({"width": breite, "height": 700})
    if seite is None:  # ohne eigenen Link: Zielgerät übernimmt die Rennleitung
        seite = f"{server.url}/rennleitung?token=e2e-ziel"
    page.goto(seite)
    page.wait_for_function("window.geraetBereit === true")
    page.add_style_tag(content=BREITE_SCHRIFT)
    return page


def _pruefen(page, breite):
    """Leiste in fester Höhe; Uhrzeit und Verbindung vollständig, nichts ragt über den Rand."""
    leiste = page.locator("#statusleiste").bounding_box()
    assert leiste["height"] == HOEHE
    for s in ("#uhr", "#netz"):
        expect(page.locator(s)).to_be_visible()
        box = page.locator(s).bounding_box()
        assert box["y"] >= leiste["y"] and box["y"] + box["height"] <= leiste["y"] + HOEHE, s
        assert box["x"] >= 0 and box["x"] + box["width"] <= breite, s
        assert page.locator(s).evaluate("e => e.scrollWidth <= e.clientWidth || !e.clientWidth"), s
    assert page.evaluate("document.documentElement.scrollWidth") <= breite  # kein Querscrollen
    for s in ("#ausstehend", "#uhr-pruefung"):  # Hinweise: gekürzt, nie unterhalb abgeschnitten
        box = page.locator(s).bounding_box()
        assert box["y"] + box["height"] <= leiste["y"] + HOEHE, s
        assert box["x"] + box["width"] <= breite, s


def _sichtbarer_text_reicht(page, anfang):
    """Der Anfang des Hinweises steht im sichtbaren Teil und nicht hinter dem Kürzungszeichen."""
    return page.locator("#uhr-pruefung").evaluate(
        """(e, anfang) => {
          const r = document.createRange();
          r.setStart(e.firstChild, 0);
          r.setEnd(e.firstChild, anfang.length);
          return e.textContent.startsWith(anfang)
            && r.getBoundingClientRect().right <= e.getBoundingClientRect().right;
        }""",
        anfang,
    )


@pytest.mark.parametrize("breite", [320, 390])
@pytest.mark.parametrize("rolle", ROLLEN)
def test_warnung_laesst_uhrzeit_und_verbindung_stehen(server, neues_geraet, rolle, breite):
    ctx = neues_geraet()
    _verstellen(ctx, -12000)
    page = _oeffnen(ctx, server, rolle, breite)
    expect(page.locator("#uhr-pruefung")).to_have_class("uhr-pruefung uhr-warnung")
    _pruefen(page, breite)
    assert _sichtbarer_text_reicht(page, "Uhr weicht 12,0 s ab")
    expect(page.locator("#ausstehend")).to_be_visible()


@pytest.mark.parametrize("breite", [320, 390])
def test_viele_ausstehende_und_langer_ort(server, neues_geraet, breite):
    ctx = neues_geraet()
    _verstellen(ctx, -12000)
    page = _oeffnen(ctx, server, "start", breite)
    page.evaluate("""ort => {
      document.querySelector('#statusleiste .rolle').textContent = ort;
      window.geraet.ausstehend = () => new Array(128);
      window.geraet.abgelehnt = () => new Array(12);
      window.geraet.zeigeStatus();
    }""", LANGER_ORT)  # fmt: skip
    expect(page.locator("#ausstehend")).to_have_text("128 ausstehend · 12 abgelehnt")
    _pruefen(page, breite)
    assert _sichtbarer_text_reicht(page, "Uhr weicht 12,0 s ab")


@pytest.mark.parametrize("breite", [320, 390])
def test_offline_ohne_warnung(server, neues_geraet, breite):
    ctx = neues_geraet()
    page = _oeffnen(ctx, server, "ziel", breite)
    offline(ctx, True)
    expect(page.locator("#netz")).to_have_text("offline")
    expect(page.locator("#uhr-pruefung")).to_have_text("Uhr ungeprüft")
    _pruefen(page, breite)
    expect(page.locator("#letzter-sync")).to_be_visible()


def test_warnung_verschiebt_nichts_und_verschwindet_wieder(server, neues_geraet):
    """Kommt die Warnung hinzu oder geht sie, bleiben Kacheln und Knöpfe, wo sie sind."""
    ctx = neues_geraet()
    page = oeffnen(ctx, server, "start")
    page.add_style_tag(content=BREITE_SCHRIFT)
    knopf = page.locator("#btn-start").bounding_box()
    page.evaluate("window.geraet.uhrAbweichung = () => 12000; window.geraet.zeigeStatus()")
    expect(page.locator("#uhr-pruefung")).to_have_text("Uhr weicht 12,0 s ab – Gerät prüfen")
    assert page.locator("#btn-start").bounding_box() == knopf
    _pruefen(page, page.viewport_size["width"])
    expect(page.locator("#letzter-sync")).to_be_hidden()  # am ehesten verzichtbar
    page.evaluate("window.geraet.uhrAbweichung = () => 100; window.geraet.zeigeStatus()")
    expect(page.locator("#letzter-sync")).to_be_visible()
    assert page.locator("#btn-start").bounding_box() == knopf


def test_e2e_laeuft_mit_der_festgelegten_schrift(server, neues_geraet):
    """Die Systemschrift der Geräteseiten ist im Browser wirklich DejaVu Sans (schriften.conf)."""
    page = oeffnen(neues_geraet(), server, "start")
    breiten = page.evaluate("""() => {
      const c = document.createElement("canvas").getContext("2d");
      return ["system-ui", "'DejaVu Sans'", "monospace"].map((f) => {
        c.font = `16px ${f}`;
        return c.measureText("Uhr weicht 12,0 s ab – Gerät prüfen").width;
      });
    }""")
    assert breiten[0] == breiten[1] != breiten[2]
    assert "system-ui" in page.evaluate("getComputedStyle(document.body).fontFamily")
