"""Aufräumen auf den Geräten nach Abschluss der Veranstaltung bzw. „Gerät zurücksetzen“."""

import json
from pathlib import Path

from playwright.sync_api import expect

from tests.e2e.conftest import admin_seite, jetzt_synchronisieren, oeffnen, offline, warte_bis


def lokale_daten(page) -> dict:
    return page.evaluate(
        """async () => {
            const db = await new Promise((ok, err) => {
                const r = indexedDB.open("rcb-zeitfahren"); r.onsuccess = () => ok(r.result); r.onerror = err;
            });
            const alle = (store) => new Promise((ok) => {
                const r = db.transaction(store).objectStore(store).getAll(); r.onsuccess = () => ok(r.result);
            });
            const schluessel = await new Promise((ok) => {
                const r = db.transaction("kv").objectStore("kv").getAllKeys(); r.onsuccess = () => ok(r.result);
            });
            return { log: await alle("log"), kv: schluessel, token: localStorage.getItem("rcb_token_start")
                     || localStorage.getItem("rcb_token_ziel"), url: location.href };
        }"""
    )


def starten(page, n):
    for _ in range(n):
        page.click("#btn-start")
        page.wait_for_timeout(1100)


def test_veranstaltung_beendet_geraet_raeumt_auf(server, neues_geraet):
    start = oeffnen(neues_geraet(), server, "start")
    starten(start, 2)
    warte_bis(lambda: server.stand()["gestartet"] == [1, 2], text="Starts übertragen")
    expect(start.locator("#ausstehend")).to_have_text("0 ausstehend")
    assert len(lokale_daten(start)["log"]) == 2

    server.abschliessen()
    jetzt_synchronisieren(start)
    expect(start.locator("#ende-text")).to_have_text(
        "Veranstaltung beendet – Daten auf diesem Gerät gelöscht."
    )
    expect(start.locator("#ungesendet-box")).to_be_hidden()
    daten = lokale_daten(start)
    assert daten["log"] == [] and daten["token"] is None
    assert not any(k.startswith("start:stand") for k in daten["kv"])
    assert "token=" not in daten["url"]
    expect(start.locator("#naechster-name")).to_have_text("Keine Teilnehmerliste")

    start.reload()
    start.wait_for_function("window.geraetBereit === true")
    expect(start.locator("#fehler")).to_contain_text("Kein Zugangstoken")
    expect(start.locator("#naechster-name")).to_have_text("Keine Teilnehmerliste")


def test_ungesendete_eintraege_bleiben_und_lassen_sich_exportieren(server, neues_geraet):
    ctx = neues_geraet()
    start = oeffnen(ctx, server, "start")
    starten(start, 1)
    warte_bis(lambda: server.stand()["gestartet"] == [1], text="Start übertragen")
    offline(ctx, True)
    starten(start, 2)
    expect(start.locator("#ausstehend")).to_have_text("2 ausstehend")

    server.abschliessen()
    offline(ctx, False)
    expect(start.locator("#ende-text")).to_contain_text("Veranstaltung beendet", timeout=10_000)
    expect(start.locator("#ungesendet-text")).to_have_text("2 Einträge nicht übertragen")
    daten = lokale_daten(start)
    assert sorted(e["startnummer"] for e in daten["log"]) == [2, 3]  # nur die ungesendeten
    assert all(e["gesendet"] == 0 for e in daten["log"])

    with start.expect_download() as dl:
        start.click("#btn-exportieren")
    export = json.loads(Path(dl.value.path()).read_text(encoding="utf-8"))
    assert [(e["art"], e["startnummer"]) for e in export["eintraege"]] == [
        ("START", 2),
        ("START", 3),
    ]
    assert export["geraet"].startswith("start-")

    start.reload()  # Hinweis bleibt nach Neuladen
    start.wait_for_function("window.geraetBereit === true")
    expect(start.locator("#ungesendet-text")).to_have_text("2 Einträge nicht übertragen")
    assert len(lokale_daten(start)["log"]) == 2


def test_geraet_zuruecksetzen_mit_bestaetigung(server, neues_geraet):
    ctx = neues_geraet()
    ziel = oeffnen(ctx, server, "ziel")
    server.starten(1, 2)
    jetzt_synchronisieren(ziel)
    ziel.locator("#fahrer button[data-nr='1']").click()
    warte_bis(lambda: server.stand()["im_ziel"] == [1], text="Zieleinlauf übertragen")
    offline(ctx, True)
    ziel.click("#btn-ohne-nummer")
    expect(ziel.locator("#ausstehend")).to_have_text("1 ausstehend")

    knopf = ziel.locator("#btn-zuruecksetzen")
    knopf.click()
    expect(knopf).to_have_text("Wirklich zurücksetzen?")
    assert len(lokale_daten(ziel)["log"]) == 2  # noch nichts gelöscht
    knopf.click()
    expect(ziel.locator("#ende-text")).to_have_text(
        "Gerät zurückgesetzt – Daten auf diesem Gerät gelöscht."
    )
    expect(ziel.locator("#ungesendet-text")).to_have_text("1 Eintrag nicht übertragen")
    daten = lokale_daten(ziel)
    assert [e["art"] for e in daten["log"]] == ["ZIEL"] and daten["log"][0]["gesendet"] == 0
    assert daten["token"] is None
    expect(ziel.locator("#fahrer button")).to_have_count(0)  # Teilnehmerliste gelöscht


def test_zuruecksetzen_ohne_zweiten_tipp_passiert_nichts(server, neues_geraet):
    start = oeffnen(neues_geraet(), server, "start")
    start.click("#btn-zuruecksetzen")
    start.wait_for_timeout(3300)
    expect(start.locator("#btn-zuruecksetzen")).to_have_text("Gerät zurücksetzen")
    expect(start.locator("#ende-box")).to_be_hidden()
    assert lokale_daten(start)["token"] is not None


def test_veranstaltung_geloescht_geraete_raeumen_auf(server, neues_geraet, browser):
    """Löschen im Admin: Start- und Zielgerät melden „Diese Veranstaltung gibt es nicht mehr“ und räumen auf;
    nicht übertragene Einträge bleiben erhalten."""
    from tests.e2e.conftest import TOKENS

    start = oeffnen(neues_geraet(), server, "start")
    starten(start, 1)
    warte_bis(lambda: server.stand()["gestartet"] == [1], text="Start übertragen")
    ziel_ctx = neues_geraet()
    ziel = oeffnen(ziel_ctx, server, "ziel")
    jetzt_synchronisieren(ziel)
    offline(ziel_ctx, True)
    ziel.locator("#fahrer button[data-nr='1']").click()
    expect(ziel.locator("#ausstehend")).to_have_text("1 ausstehend")

    admin_ctx = browser.new_context(
        viewport={"width": 1280, "height": 900}, service_workers="block"
    )
    a = admin_ctx.new_page()
    a.goto(f"{server.url}/admin")
    a.fill("input[name=token]", TOKENS["admin"])
    a.click("#anmelden button")
    a.click("#veranstaltungen a")
    admin_seite(a, "status")
    expect(a.locator("#loeschen-hinweis")).to_contain_text("aktive Veranstaltung")
    a.click("#loeschen-zeile summary")
    a.fill("#loeschen input[name=name]", "E2E-Zeitfahren")
    a.click("#loeschen button[type=submit]")
    expect(a.locator(".meldung")).to_contain_text("komplett gelöscht")
    a.goto(server.url + "/")
    expect(a.locator("body")).to_contain_text("Derzeit keine Veranstaltung")
    admin_ctx.close()

    jetzt_synchronisieren(start)
    expect(start.locator("#ende-text")).to_have_text(
        "Diese Veranstaltung gibt es nicht mehr – Daten auf diesem Gerät gelöscht."
    )
    expect(start.locator("#ungesendet-box")).to_be_hidden()
    daten = lokale_daten(start)
    assert daten["log"] == [] and daten["token"] is None

    offline(ziel_ctx, False)
    expect(ziel.locator("#ende-text")).to_have_text(
        "Diese Veranstaltung gibt es nicht mehr – Daten auf diesem Gerät gelöscht.", timeout=10_000
    )
    expect(ziel.locator("#ungesendet-text")).to_have_text("1 Eintrag nicht übertragen")
    assert [e["art"] for e in lokale_daten(ziel)["log"]] == ["ZIEL"]
