"""E2E: Rennleitung übersichtlicher (AUSBAU A4, Test 08.10.): Reiter, kompakte aufklappbare Zeilen, „im Ziel“."""

import re

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import oeffnen
from tests.e2e.test_ziel import kachel, rennleitung


@pytest.fixture
def teilnehmer_anzahl() -> int:
    return 40


def sichtbare_bereiche(page):
    return page.locator(".rl-bereich:visible").evaluate_all("bs => bs.map(b => b.id)")


def test_reiter_nur_in_rennleitung_immer_ein_bereich(server, neues_geraet):
    server.starten(*range(1, 11))
    ziel = oeffnen(neues_geraet(), server, "ziel")
    expect(ziel.locator("#kopf-rennleitung")).to_be_hidden()
    ziel.click("#btn-ohne-nummer")
    rennleitung(ziel)
    expect(ziel.locator("#kopf-rennleitung")).to_be_visible()
    expect(ziel.locator("#rl-tab-offen")).to_have_text("Offene Zeiten (1)")
    expect(ziel.locator("#rl-tab-letzte")).to_have_text("Letzte Einläufe")
    expect(ziel.locator("#rl-tab-status")).to_have_text("Status")
    # Start-Reiter: offene Zeiten vorhanden
    expect(ziel.locator("#rl-tab-offen")).to_have_attribute("aria-selected", "true")
    assert sichtbare_bereiche(ziel) == ["rl-offen"]
    for b in ("letzte", "status", "offen"):
        ziel.click(f"#rl-tab-{b}")
        expect(ziel.locator(f"#rl-tab-{b}")).to_have_class(re.compile("aktiv"))
        assert sichtbare_bereiche(ziel) == [f"rl-{b}"]
    # gewählter Reiter bleibt, auch wenn die offenen Zeiten verschwinden und beim Sichtwechsel
    ziel.click("#rl-tab-status")
    ziel.click("#tab-erfassen")
    ziel.click("#btn-ohne-nummer")
    rennleitung(ziel)
    assert sichtbare_bereiche(ziel) == ["rl-status"]


def test_start_reiter_ohne_offene_zeiten_letzte_einlaeufe(server, neues_geraet):
    server.starten(1)
    ziel = oeffnen(neues_geraet(), server, "ziel")
    rennleitung(ziel)
    expect(ziel.locator("#rl-tab-letzte")).to_have_attribute("aria-selected", "true")
    assert sichtbare_bereiche(ziel) == ["rl-letzte"]


def test_status_kachel_im_ziel_dezent_mit_text_und_kontrast(server, neues_geraet):
    server.starten(1, 2)
    ziel = oeffnen(neues_geraet(), server, "ziel")
    kachel(ziel, 1).click()
    rennleitung(ziel)
    ziel.click("#rl-tab-status")
    k1 = ziel.locator("#status-auswahl .kachel[data-nr='1']")
    expect(k1).to_contain_text("✓ im Ziel")
    expect(k1).not_to_have_class(re.compile("erledigt"))
    expect(ziel.locator("#status-auswahl .kachel[data-nr='2']")).not_to_contain_text("im Ziel")
    hintergrund = k1.evaluate("e => getComputedStyle(e).backgroundColor")
    assert hintergrund == ziel.locator("#status-auswahl .kachel[data-nr='2']").evaluate(
        "e => getComputedStyle(e).backgroundColor"
    )  # keine grüne Fläche
    kontrast = k1.locator(".kachel-im-ziel").evaluate("""e => {
      const rgb = s => s.match(/\\d+/g).slice(0, 3).map(Number);
      const lum = c => { const [r, g, b] = c.map(v => { v /= 255;
        return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
        return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
      const a = lum(rgb(getComputedStyle(e).color));
      const b = lum(rgb(getComputedStyle(e.parentElement).backgroundColor));
      return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    }""")
    assert kontrast >= 4.5


def test_letzte_einlaeufe_kompakt_nur_eine_zeile_offen(server, neues_geraet):
    server.starten(1, 2)
    ziel = oeffnen(neues_geraet(), server, "ziel")
    kachel(ziel, 1).click()
    ziel.wait_for_timeout(1100)
    kachel(ziel, 2).click()
    rennleitung(ziel)
    ziel.click("#rl-tab-letzte")
    zeilen = ziel.locator("#letzte li")
    expect(zeilen).to_have_count(2)
    expect(ziel.locator("#letzte .letzte-aktionen:visible")).to_have_count(0)
    erste, zweite = zeilen.nth(0), zeilen.nth(1)
    erste.locator(".letzte-text").click()
    expect(erste.locator(".letzte-aktionen")).to_be_visible()
    for knopf, text in ((".btn-korrektur-minus", "−1 s"), (".btn-korrektur-plus", "+1 s"),
                        (".btn-umbuchen", "Umbuchen"), (".btn-rueckgaengig", "Rückgängig")):  # fmt: skip
        expect(erste.locator(knopf)).to_have_text(text)
    expect(erste.locator(".letzte-text")).to_have_attribute("aria-expanded", "true")
    zweite.locator(".letzte-text").click()  # nur eine Zeile offen
    expect(zweite.locator(".letzte-aktionen")).to_be_visible()
    expect(erste.locator(".letzte-aktionen")).to_be_hidden()
    zweite.locator(".btn-korrektur-plus").click()  # bleibt offen für weitere Tipps
    expect(zweite).to_contain_text("korrigiert +1,0 s")
    expect(zweite.locator(".letzte-aktionen")).to_be_visible()
    zweite.locator(".letzte-text").click()  # erneut antippen: zu
    expect(ziel.locator("#letzte .letzte-aktionen:visible")).to_have_count(0)
    # kompakt: eine Zeile ist deutlich niedriger als vorher mit Knöpfen
    assert erste.bounding_box()["height"] < 60
