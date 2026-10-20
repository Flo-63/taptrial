"""Mehrere Geräte im selben Browser – Gerätekennung, Outbox, Einstellungen und
Uhrmeldung je Rolle und Messpunkt getrennt (z. B. zwei Posten oder Ziel und Rennleitung auf einem
Laptop)."""

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import TOKENS, admin_seite, jetzt_synchronisieren, warte_bis
from tests.e2e.test_import_vorschau import anmelden
from tests.e2e.test_ziel import kachel


@pytest.fixture
def rundkurs():
    return "messpunkte"


def tab(ctx, url: str):
    page = ctx.new_page()
    page.goto(url)
    page.wait_for_function("window.geraetBereit === true")
    return page


def test_rollen_im_selben_browser_getrennt(server, browser, playwright):
    server.starten(1, 2)
    ctx = browser.new_context(**playwright.devices["Pixel 7"], service_workers="allow")
    # wie die Gerätelinks unter „Geräte“: Posten mit ?ort=<Messpunkt>, Ziel und Rennleitungs-Sicht
    kreisel = tab(ctx, f"{server.url}/posten?token={TOKENS['posten']}&ort=1")
    berg = tab(ctx, f"{server.url}/posten?token={TOKENS['posten-berg']}&ort=2")
    ziel = tab(ctx, f"{server.url}/ziel?token={TOKENS['ziel']}")
    rl = tab(ctx, f"{server.url}/ziel?token={TOKENS['ziel']}&sicht=rennleitung")
    expect(kreisel.locator("#posten-titel")).to_have_text("Zwischenzeit Kreisel")
    expect(berg.locator("#posten-titel")).to_have_text("Zwischenzeit Berg")
    expect(rl.locator("#sicht-rennleitung")).to_be_visible()
    kachel(kreisel, 1).click()
    kachel(berg, 2).click()
    kachel(ziel, 2).click()
    for p in (kreisel, berg, ziel, rl):
        jetzt_synchronisieren(p)
    warte_bis(lambda: len([e for e in server.ereignisse() if e["art"] in ("DURCHFAHRT", "ZIEL")]) == 3,
              text="drei Einträge")  # fmt: skip
    ids = {p: p.evaluate("window.geraet.geraetId") for p in (kreisel, berg, ziel, rl)}
    assert len(set(ids.values())) == 4, ids
    ereignisse = {e["art"] + (e["wert"] or ""): e["geraet"] for e in server.ereignisse()
                  if e["art"] in ("DURCHFAHRT", "ZIEL")}  # fmt: skip
    assert ereignisse == {"DURCHFAHRT1": ids[kreisel], "DURCHFAHRT2": ids[berg], "ZIEL": ids[ziel]}
    # keine Einträge in der falschen Outbox: jeder Tab kennt nur seine eigenen
    for p, profil in ((kreisel, "posten-1"), (berg, "posten-2"), (ziel, "ziel"), (rl, "ziel-rl")):
        assert p.evaluate("window.geraet.profil") == profil
        assert p.evaluate("window.geraet.log.every(e => e.profil === window.geraet.profil)")
    assert (
        kreisel.evaluate("window.geraet.log.length") == 1
        and rl.evaluate("window.geraet.log.length") == 0
    )
    # nach dem Neuladen bleibt jeder Tab bei seinem Messpunkt und seiner Kennung
    kreisel.reload()
    kreisel.wait_for_function("window.geraetBereit === true")
    expect(kreisel.locator("#posten-titel")).to_have_text("Zwischenzeit Kreisel")
    assert kreisel.evaluate("window.geraet.geraetId") == ids[kreisel]
    for p in (kreisel, berg, ziel, rl):
        jetzt_synchronisieren(p)
    # Admin: jede Rolle einzeln unter „Verbundene Geräte und Uhren“, keine in „Als Nächstes“
    actx, a = anmelden(browser, server)
    admin_seite(a, "geraete")
    zeilen = a.locator("#uhren tbody tr")
    expect(zeilen).to_have_count(4)
    texte = sorted(zeilen.all_inner_texts())
    for p, rolle in (
        (kreisel, "Posten Kreisel"),
        (berg, "Posten Berg"),
        (ziel, "Ziel"),
        (rl, "Ziel"),
    ):
        assert any(ids[p] in t and rolle in t for t in texte), (rolle, texte)
    admin_seite(a, "uebersicht")
    naechstes = a.locator("#naechstes").inner_text()
    assert (
        "Posten Kreisel noch nicht" not in naechstes and "Posten Berg noch nicht" not in naechstes
    )
    assert (
        "Zielgerät noch nicht" not in naechstes and "Startgerät noch nicht verbunden" in naechstes
    )
    actx.close()
    ctx.close()
