"""E2E im Modus proxy: Anmeldung über (nachgestellte) Forward-Auth, Rollen in der Oberfläche,
Kein Zugriff; Geräte laufen ohne Anmeldung weiter. Den Modus token prüfen alle übrigen E2E-Tests."""

import re

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import oeffnen
from tests.proxy_hilfe import ZIELGRUPPE, Anmeldedienst


@pytest.fixture
def dienst():
    d = Anmeldedienst.starten()
    yield d
    d.beenden()


@pytest.fixture
def server_umgebung(dienst):
    return {"TAPTRIAL_ANMELDUNG": "proxy", "TAPTRIAL_PROXY_JWKS_URL": dienst.jwks_url,
            "TAPTRIAL_PROXY_JWT_ISSUER": dienst.aussteller, "TAPTRIAL_PROXY_JWT_AUDIENCE": ZIELGRUPPE,
            "TAPTRIAL_SUPERADMINS": "chefin"}  # fmt: skip


def _als(browser, dienst, uid, name, anzeige=""):
    ctx = browser.new_context(extra_http_headers=dienst.kopf(uid, name, name=anzeige))
    return ctx.new_page()


def test_rollen_im_modus_proxy(server, browser, dienst):
    chefin = _als(browser, dienst, "u-chefin", "chefin", "Clara Chefin")
    chefin.goto(f"{server.url}/admin")
    expect(chefin.locator("#angemeldet")).to_contain_text("Clara Chefin (chefin)")
    expect(chefin.locator("#angemeldet")).to_contain_text("Superadmin")
    # unbekannte Person: Konto ohne Rolle, „Kein Zugriff“
    neu = _als(browser, dienst, "u-erika", "erika.muster", "Erika Muster")
    neu.goto(f"{server.url}/admin")
    expect(neu.locator("#admin-hinweis h1")).to_have_text("Kein Zugriff")
    # Superadmin sieht sie oben, gibt ihr die Veranstaltung als Event-Admin
    chefin.click("#nav-benutzer")
    zeile = chefin.locator('tr[data-benutzer="erika.muster"]')
    expect(zeile).to_have_class(re.compile("zeile-neu"))
    zeile.locator("select[name=veranstaltung]").select_option(index=1)
    zeile.locator(".rolle-vergeben button").click()
    expect(chefin.locator(".meldung")).to_contain_text("an Erika Muster (erika.muster) vergeben")
    # jetzt: nur ihre Veranstaltung, keine Vorlagen, kein Löschen
    neu.goto(f"{server.url}/admin")
    expect(neu.locator("#angemeldet")).to_contain_text("Event-Admin")
    expect(neu.locator("#nav-vorlagen")).to_have_count(0)
    expect(neu.locator("#nav-benutzer")).to_have_count(0)
    expect(neu.locator("#neue-veranstaltung")).to_have_count(0)
    neu.locator("table a.btn", has_text="Öffnen").first.click()
    neu.locator(".sl-nav a[data-seite=status]").click()
    expect(neu.locator("#verwalten-hinweis")).to_be_visible()
    expect(neu.locator("#lebenszyklus")).to_have_count(0)
    # Direktaufruf einer fremden Funktion: abgewiesen
    antwort = neu.request.get(f"{server.url}/admin/saetze")
    assert antwort.status == 403
    # Gerät ohne jede Anmeldung
    geraet = browser.new_context()
    start = oeffnen(geraet, server, "start")
    expect(start.locator("#netz")).to_have_text("online")


def test_ohne_gueltiges_token_keine_anmeldung(server, browser, dienst):
    seite = browser.new_context(extra_http_headers={
        **dienst.kopf("u-chefin", "chefin"), "X-Authentik-Jwt": "x.y.z"}).new_page()  # fmt: skip
    antwort = seite.goto(f"{server.url}/admin")
    assert antwort.status == 401
    expect(seite.locator("#admin-hinweis h1")).to_have_text("Anmeldung nicht möglich")
