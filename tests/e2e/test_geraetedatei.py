"""E2E: Exportdatei eines Geräts im Admin einlesen."""

import json
from pathlib import Path

from playwright.sync_api import expect

from tests.e2e.conftest import oeffnen, offline
from tests.e2e.test_import_vorschau import anmelden


def test_export_vom_geraet_im_admin_uebernehmen(server, neues_geraet, browser):
    server.starten(1, 2)
    ctx = neues_geraet()
    ziel = oeffnen(ctx, server, "ziel")
    expect(ziel.locator("#fahrer .kachel[data-nr='1']")).to_be_visible()
    offline(ctx, True)
    ziel.click("#fahrer .kachel[data-nr='1']")
    ziel.click("#btn-ohne-nummer")
    expect(ziel.locator("#ausstehend")).to_have_text("2 ausstehend")
    with ziel.expect_download() as dl:
        ziel.evaluate("window.geraet.exportieren()")
    pfad = Path(dl.value.path())
    export = json.loads(pfad.read_text(encoding="utf-8"))
    assert export["veranstaltung"]["id"] == 1 and len(export["eintraege"]) == 2

    actx, admin = anmelden(browser, server)
    admin.goto(f"{server.url}/admin/v/1/protokoll")
    admin.click("#geraetedatei-link")
    admin.set_input_files("#geraetedatei input[type=file]", str(pfad))
    admin.click("#geraetedatei button")
    expect(admin.locator("#gd-neu")).to_have_text("2")
    expect(admin.locator("#gd-vorhanden")).to_have_text("0")
    expect(admin.locator("#gd-warnung")).to_have_count(0)
    admin.click("#gd-uebernehmen")
    expect(admin.locator(".meldung")).to_contain_text("2 Einträge aus der Datei übernommen")
    assert server.teilnehmer(1)["ziel_ms"] is not None
    assert {e["geraet"] for e in server.ereignisse() if e["art"] == "ZIEL"} == {export["geraet"]}
    admin.goto(f"{server.url}/admin/v/1/protokoll")
    expect(admin.locator("#protokoll .quelle")).to_have_count(2)

    # danach überträgt das Gerät dieselben Einträge: keine Dubletten (gleiche UUID)
    offline(ctx, False)
    expect(ziel.locator("#ausstehend")).to_have_text("0 ausstehend", timeout=10_000)
    assert len([e for e in server.ereignisse() if e["art"] == "ZIEL"]) == 2
    actx.close()
