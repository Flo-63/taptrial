"""Segmente 46 im Browser: Geräte zeigen bis zur Freigabe einen Sperrbildschirm, schalten sich
ohne Neuladen frei; abgelehnte Einträge bleiben erhalten und gehen nach der Freigabe durch."""

from playwright.sync_api import expect

from tests.e2e.conftest import jetzt_synchronisieren, oeffnen, offline, warte_bis


def starts(server):
    alle = server.api("/api/ereignisse?veranstaltung_id=1")["ereignisse"]  # auch wenn nicht aktiv
    return [e for e in alle if e["art"] == "START"]


def test_sperrbildschirm_und_freischalten_ohne_neuladen(server, neues_geraet):
    assert "nicht mehr aktiv" in server.admin_post("/admin/v/1/deaktivieren")
    p = oeffnen(neues_geraet(), server, "start")
    expect(p.locator("#sperre")).to_be_visible()
    expect(p.locator("#sperre h1")).to_have_text("Zeitnahme noch nicht freigegeben")
    expect(p.locator("#sperre-schritte li")).to_have_text(["Veranstaltung aktiv setzen"])
    expect(p.locator("#sperre")).to_contain_text(
        "Diese Seite schaltet sich danach von selbst frei."
    )
    expect(p.locator("#btn-start")).to_be_hidden()
    server.admin_post("/admin/v/1/aktivieren")
    jetzt_synchronisieren(p)
    expect(p.locator("#sperre")).to_be_hidden()
    expect(p.locator("#btn-start")).to_be_visible()


def test_offline_erfasst_abgelehnt_bleibt_und_geht_nach_freigabe_durch(server, neues_geraet):
    ctx = neues_geraet()
    p = oeffnen(ctx, server, "start")
    expect(p.locator("#btn-start")).to_be_visible()
    offline(ctx, True)
    server.admin_post("/admin/v/1/deaktivieren")
    p.click("#btn-start")  # offline erfasst, Stand noch „freigegeben“
    expect(p.locator("#ausstehend")).to_have_text("1 ausstehend")
    offline(ctx, False)
    jetzt_synchronisieren(p)
    expect(p.locator("#sperre")).to_be_visible()
    expect(p.locator("#sperre-ausstehend")).to_have_text(
        "1 Eintrag wartet auf die Freigabe und wird danach gesendet."
    )
    expect(p.locator("#ausstehend")).to_have_text("1 ausstehend")  # nicht gelöscht, nicht abgelehnt
    assert starts(server) == []
    p.reload()  # übersteht auch Neuladen
    p.wait_for_function("window.geraetBereit === true")
    expect(p.locator("#ausstehend")).to_have_text("1 ausstehend")
    server.admin_post("/admin/v/1/aktivieren")
    jetzt_synchronisieren(p)
    warte_bis(lambda: len(starts(server)) == 1, text="Start nach der Freigabe übertragen")
    expect(p.locator("#sperre")).to_be_hidden()
    expect(p.locator("#ausstehend")).to_have_text("0 ausstehend")


def test_laufende_veranstaltung_nicht_verdraengen(server, browser):
    """Segmente 55: „Aktiv setzen“ einer anderen Veranstaltung ist gesperrt, solange die laufende
    Startzeiten hat – mit Grund und Button zur laufenden Veranstaltung."""
    from tests.e2e.test_import_vorschau import anmelden

    server.admin_post("/admin/veranstaltungen", name="Herbst", datum="2026-11-01")
    server.starten(1)
    ctx, a = anmelden(browser, server)
    a.goto(f"{server.url}/admin/v/2/status")
    expect(a.locator("#aktivieren")).to_be_disabled()
    expect(a.locator("#aktivieren-grund")).to_have_text(
        "Gesperrt: ‚E2E-Zeitfahren‘ läuft (Startzeiten vorhanden). Erst abschließen."
    )
    a.click("#zur-laufenden")
    expect(a.locator("h1")).to_have_text("Status & Datenschutz")
    assert a.url.endswith("/admin/v/1/status")
    ctx.close()
