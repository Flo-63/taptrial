"""Segmente 9/14: Abschnittszeiten im Kasten „Auf der Strecke“ des Zielmonitors (Konfiguration, Standard ohne)."""

import time
import uuid

import pytest
from playwright.sync_api import expect

MIN = 60_000


@pytest.fixture
def rundkurs():
    return "monitor"


def durchfahrten(server, starts):
    """Nr. 1: Kreisel nach 3, Berg nach 8, Kreisel nach 13, Berg nach 17 min (zweite Runde 1 min schneller);
    Nr. 2: nur Kreisel nach 3 min."""
    kreisel = server.api("/api/stand", rolle="posten")["messpunkt"]["id"]
    berg = server.api("/api/stand", rolle="posten-berg")["messpunkt"]["id"]
    plan = [(1, kreisel, 3), (1, berg, 8), (1, kreisel, 13), (1, berg, 17), (2, kreisel, 3)]
    ev = [{"uuid": str(uuid.uuid4()), "art": "DURCHFAHRT", "startnummer": nr, "zeit_ms": starts[nr] + m * MIN,
           "erstellt_ms": starts[nr] + m * MIN, "geraet": "posten-api", "wert": str(mp)} for nr, mp, m in plan]  # fmt: skip
    return ev


def senden_direkt(server, ev):
    """Durchfahrten über die Posten-Tokens ihres Messpunkts."""
    kreisel = str(server.api("/api/stand", rolle="posten")["messpunkt"]["id"])
    server.senden([e for e in ev if e["wert"] == kreisel], rolle="posten")
    server.senden([e for e in ev if e["wert"] != kreisel], rolle="posten-berg")


def monitor(browser, server, breite, hoehe):
    page = browser.new_page(viewport={"width": breite, "height": hoehe})
    page.goto(f"{server.url}/monitor")
    expect(page.locator("#mon-unterwegs tr[data-nr='1'] .segment")).to_be_visible()
    return page


def test_segmentzeit_mit_pfeil_und_differenz_quer_und_hochkant(browser, server):
    t0 = int(time.time() * 1000) - 20 * MIN
    starts = {1: t0, 2: t0 + MIN, 3: t0 + 2 * MIN}
    server.senden([{"uuid": str(uuid.uuid4()), "art": "START", "startnummer": nr, "zeit_ms": t, "erstellt_ms": t,
                    "geraet": "start-api"} for nr, t in starts.items()])  # fmt: skip
    senden_direkt(server, durchfahrten(server, starts))
    for breite, hoehe in ((1366, 768), (768, 1024)):
        page = monitor(browser, server, breite, hoehe)
        feld = page.locator("#mon-unterwegs tr[data-nr='1'] .segment")
        expect(feld.locator(".seg-label")).to_have_text("Kreisel→Berg (2)")
        expect(feld.locator(".seg-zeit")).to_have_text("4:00")
        expect(feld.locator(".seg-pfeil")).to_have_text("▼")
        expect(feld.locator(".seg-pfeil")).to_have_class("seg-pfeil schneller")
        expect(feld.locator(".seg-diff")).to_be_visible()
        expect(feld.locator(".seg-diff")).to_have_text("60 s")
        # Nr. 2 war erst am Kreisel: noch keine Zeit für „Kreisel→Berg“ – leeres Feld
        expect(page.locator("#mon-unterwegs tr[data-nr='2'] .segment")).to_have_text("")
        expect(page.locator("#mon-unterwegs tr[data-nr='3'] .segment")).to_have_text("")
        # übrige Bereiche unverändert, kein waagerechtes Scrollen
        assert page.evaluate("document.documentElement.scrollWidth") <= breite
        assert (
            page.locator("#mon-unterwegs tr[data-nr='1'] td").count() == 7
        )  # Raster (Segmente 72)
        page.close()


def test_platzmangel_erst_differenz_dann_pfeil_weg_zeit_bleibt(browser, server):
    t0 = int(time.time() * 1000) - 20 * MIN
    starts = {1: t0, 2: t0 + MIN}
    server.senden([{"uuid": str(uuid.uuid4()), "art": "START", "startnummer": nr, "zeit_ms": t, "erstellt_ms": t,
                    "geraet": "start-api"} for nr, t in starts.items()])  # fmt: skip
    senden_direkt(server, durchfahrten(server, starts))
    page = monitor(browser, server, 360, 740)
    feld = page.locator("#mon-unterwegs tr[data-nr='1'] .segment")
    # Segmente 62: ein Feld hat auch bei 360 px Platz für Name, Zeit, Pfeil und Differenz
    for teil in (".seg-label", ".seg-zeit", ".seg-pfeil", ".seg-diff"):
        expect(feld.locator(teil)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth") <= 360
    # enger (Feld künstlich schmal): erst die Differenz, dann der Pfeil weg, die Zeit bleibt
    masse = feld.evaluate("""f => { const w = (s) => f.querySelector(s).getBoundingClientRect().width;
        const cs = (s) => getComputedStyle(f.querySelector(s));
        return {basis: parseFloat(cs('.seg-label').minWidth) + (parseFloat(getComputedStyle(f).columnGap) || 0) + w('.seg-zeit'),
                pfeil: w('.seg-pfeil') + parseFloat(cs('.seg-pfeil').marginLeft),
                diff: w('.seg-diff') + parseFloat(cs('.seg-diff').marginLeft)}; }""")  # fmt: skip
    ziele = ((masse["basis"] + masse["pfeil"] + masse["diff"] / 2, True),
             (masse["basis"] + masse["pfeil"] / 2, False))  # fmt: skip
    for ziel, pfeil in ziele:
        page.evaluate(f"""(() => {{ let s = document.getElementById('test-eng') || document.head.appendChild(
            Object.assign(document.createElement('style'), {{id: 'test-eng'}}));
            s.textContent = '#mon-unterwegs .segment {{ flex: none !important; width: {ziel}px !important; }}';
            window.dispatchEvent(new Event('resize')); }})()""")  # fmt: skip
        expect(feld.locator(".seg-diff")).to_be_hidden()
        if pfeil:
            expect(feld.locator(".seg-pfeil")).to_be_visible()
        else:
            expect(feld.locator(".seg-pfeil")).to_be_hidden()
        expect(feld.locator(".seg-zeit")).to_be_visible()
        box = feld.bounding_box()
        zeit = feld.locator(".seg-zeit").bounding_box()
        assert zeit["x"] + zeit["width"] <= box["x"] + box["width"] + 1  # Zeit vollständig im Feld


@pytest.mark.parametrize("rundkurs", ["messpunkte"])
def test_standard_ohne_abschnittsfelder(browser, server, rundkurs):
    server.starten(1)
    page = browser.new_page(viewport={"width": 1366, "height": 768})
    page.goto(f"{server.url}/monitor")
    expect(page.locator("#mon-unterwegs tr[data-nr='1']")).to_be_visible()
    expect(page.locator("#mon-unterwegs .segment")).to_have_count(0)


def konfig_setzen(server, daten):
    """Zielmonitor über die Admin-Seite konfigurieren (Anmeldung, CSRF, Formular)."""
    import re

    import httpx

    from tests.e2e.conftest import TOKENS

    with httpx.Client(base_url=server.url, follow_redirects=True) as c:
        c.post("/admin/anmelden", data={"token": TOKENS["admin"]})
        csrf = re.search(
            r'name="csrf" value="([0-9a-f]+)"', c.get("/admin/v/1/zielmonitor").text
        ).group(1)
        for d in daten:
            r = c.post("/admin/v/1/zielmonitor", data={"csrf": csrf, **d})
            assert "gespeichert" in r.text, r.text[-500:]


@pytest.mark.parametrize("rundkurs", [None])
def test_seiten_wechseln_live_bereich_je_seite_ohne_neuladen(browser, server, rundkurs):
    server.starten(1, 2)
    konfig_setzen(server, [{"aktion": "neu", "neuer_typ": "ergebnis", "seite_an_0": "1", "seite_dauer_0": "3",
                            "seite_live_0": "1"},
                           {"aktion": "speichern", "seite_an_0": "1", "seite_dauer_0": "3", "seite_live_0": "1",
                            "seite_an_1": "1", "seite_dauer_1": "3"}])  # fmt: skip
    page = browser.new_page(viewport={"width": 1366, "height": 768})
    page.goto(f"{server.url}/monitor")
    oben = page.locator("#box-unterwegs")
    expect(oben).to_be_visible()
    expect(oben).to_be_hidden(timeout=6_000)  # zweite Seite ohne Live-Bereich
    expect(oben).to_be_visible(timeout=6_000)  # und wieder die erste
    # Änderung im Admin: nur noch eine Seite ohne Live-Bereich – der Monitor übernimmt sie ohne Neuladen
    konfig_setzen(server, [{"aktion": "weg:0", "seite_an_0": "1", "seite_dauer_0": "3", "seite_live_0": "1",
                            "seite_an_1": "1", "seite_dauer_1": "3"}])  # fmt: skip
    page.wait_for_timeout(7_000)
    expect(oben).to_be_hidden()
    page.wait_for_timeout(4_000)
    expect(oben).to_be_hidden()


def test_monitor_seite_rangliste_abschnitt(browser, server):
    """Segmente 15: Monitor-Seite „Rangliste Abschnitt“ mit Top N."""
    import re

    import httpx

    from tests.e2e.conftest import TOKENS

    t0 = int(time.time() * 1000) - 20 * MIN
    starts = {1: t0, 2: t0 + MIN}
    server.senden([{"uuid": str(uuid.uuid4()), "art": "START", "startnummer": nr, "zeit_ms": t, "erstellt_ms": t,
                    "geraet": "start-api"} for nr, t in starts.items()])  # fmt: skip
    senden_direkt(server, durchfahrten(server, starts))
    with httpx.Client(base_url=server.url, follow_redirects=True) as c:
        c.post("/admin/anmelden", data={"token": TOKENS["admin"]})
        html = c.get("/admin/v/1/zielmonitor").text
        abschnitt = re.search(r'name="live_abschnitt" value="([^"]+:s1\.0)"', html).group(1)
    konfig_setzen(server, [{"aktion": "neu", "neuer_typ": "rangliste", "seite_dauer_0": "10"},
                           {"aktion": "speichern", "seite_dauer_0": "10", "seite_an_1": "1", "seite_dauer_1": "30",
                            "seite_live_1": "1", "seite_abschnitt_1": abschnitt, "seite_top_1": "5"}])  # fmt: skip
    page = browser.new_page(viewport={"width": 1366, "height": 768})
    page.goto(f"{server.url}/monitor")
    tabelle = page.locator(".mon-rangliste")
    expect(tabelle).to_be_visible()
    expect(page.locator("#mon-bereich")).to_have_text("Rangliste Kreisel→Berg")
    expect(tabelle.locator("tr[data-nr='1'] td.zeit")).to_have_text("4:00,0")
    expect(tabelle.locator("tr[data-nr='1'] td.status")).to_have_text("Kreisel→Berg (2)")
    assert page.evaluate("document.documentElement.scrollWidth") <= 1366


# --- Segmente 34: gespeicherte Einstellungen wirken in Vorschau und Live-Monitor ---------------


def _durchfahrt(server, nr, mp, rolle, zeit):
    server.senden([{"uuid": str(uuid.uuid4()), "art": "DURCHFAHRT", "startnummer": nr, "zeit_ms": zeit,
                    "erstellt_ms": zeit, "geraet": "posten-api", "wert": str(mp)}], rolle=rolle)  # fmt: skip


@pytest.mark.parametrize("rundkurs", ["messpunkte"])
def test_einstellungen_wirken_sofort_in_offener_vorschau(browser, server, rundkurs):
    """Einstellung im Admin ändern → Speichern → die offene Vorschau zeigt sofort den neuen Stand
    (Seitenfolge, Dauer, Live-Bereich, Abschnittsfelder); Enter im Feld speichert auch."""
    from tests.e2e.conftest import TOKENS, einstellungen

    t0 = int(time.time() * 1000) - 20 * MIN
    starts = {1: t0, 2: t0 + MIN}
    server.senden([{"uuid": str(uuid.uuid4()), "art": "START", "startnummer": nr, "zeit_ms": t, "erstellt_ms": t,
                    "geraet": "start-api"} for nr, t in starts.items()])  # fmt: skip
    senden_direkt(server, durchfahrten(server, starts))
    ctx = browser.new_context(viewport={"width": 1366, "height": 900})
    a = ctx.new_page()
    a.goto(f"{server.url}/admin")
    a.fill("input[name=token]", TOKENS["admin"])
    a.click("#anmelden button")
    a.click("#veranstaltungen a")
    einstellungen(a, "zielmonitor")
    with ctx.expect_page() as neu:
        a.click("#monitor-vorschau")
    m = neu.value
    expect(m.locator("#mon-unterwegs tr[data-nr='1']")).to_be_visible()
    expect(m.locator("#mon-unterwegs .segment")).to_have_count(0)
    # Abschnittsfeld im Live-Bereich: nach „Speichern“ sofort (vor dem nächsten 5-s-Abruf)
    a.locator("#monitor-live label", has_text="Kreisel→Berg").locator("input").check()
    a.click("#zielmonitor-speichern")
    expect(a.locator(".meldung")).to_contain_text("Zielmonitor gespeichert")
    expect(m.locator("#mon-unterwegs tr[data-nr='1'] .segment .seg-zeit")).to_have_text(
        "4:00", timeout=2_500
    )
    # Seitenfolge und Dauer: Rangliste davor, je 3 s; Live-Bereich nur auf der Ergebnisseite
    a.select_option("select[name=neuer_typ]", "rangliste")
    a.click("#seite-neu")
    a.select_option("select[name=seite_abschnitt_1]", label="Kreisel→Berg")
    a.fill("input[name=seite_dauer_1]", "3")
    a.uncheck("input[name=seite_live_1]")
    a.fill("input[name=seite_dauer_0]", "3")
    a.click("button[value='auf:1']")
    expect(a.locator("input[name=seite_dauer_0]")).to_have_value("3")
    expect(m.locator("#mon-bereich")).to_have_text("Rangliste Kreisel→Berg", timeout=2_500)
    expect(m.locator("#box-unterwegs")).to_be_hidden()
    expect(m.locator("#mon-bereich")).to_have_text("Ergebnis", timeout=4_500)
    expect(m.locator("#box-unterwegs")).to_be_visible()
    # Enter im Feld „Dauer“ speichert (Standardknopf), die Vorschau übernimmt es sofort
    a.uncheck("input[name=seite_an_0]")
    a.locator("input[name=seite_dauer_1]").press("Enter")
    expect(a.locator(".meldung")).to_contain_text("Zielmonitor gespeichert")
    expect(m.locator("#mon-bereich")).to_have_text("Ergebnis", timeout=2_500)
    m.wait_for_timeout(4_000)
    expect(m.locator("#mon-bereich")).to_have_text("Ergebnis")  # Rangliste ist aus
    # der Live-Monitor (ohne Vorschau-Kanal, eigener Browser) übernimmt es beim nächsten Abruf
    live = browser.new_page(viewport={"width": 1366, "height": 768})
    live.goto(f"{server.url}/monitor")
    expect(live.locator("#mon-unterwegs tr[data-nr='1'] .segment .seg-zeit")).to_have_text("4:00")
    expect(live.locator("#mon-bereich")).to_have_text("Ergebnis")
    ctx.close()


@pytest.mark.parametrize("rundkurs", ["messpunkte"])
def test_seitenfolge_im_laufenden_rennen(browser, server, rundkurs):
    """Fehler 11.10.: Neue Zeiten auf einer Ranglisten-Seite setzten den Monitor bei jedem Abruf auf
    die erste Seite zurück – Seitenfolge und Dauer wirkten im laufenden Rennen nicht."""
    import re

    import httpx

    from tests.e2e.conftest import TOKENS

    t0 = int(time.time() * 1000) - 30 * MIN
    starts = {nr: t0 + nr * MIN for nr in range(1, 9)}
    server.senden([{"uuid": str(uuid.uuid4()), "art": "START", "startnummer": nr, "zeit_ms": t, "erstellt_ms": t,
                    "geraet": "start-api"} for nr, t in starts.items()])  # fmt: skip
    kreisel = server.api("/api/stand", rolle="posten")["messpunkt"]["id"]
    berg = server.api("/api/stand", rolle="posten-berg")["messpunkt"]["id"]
    with httpx.Client(base_url=server.url, follow_redirects=True) as c:
        c.post("/admin/anmelden", data={"token": TOKENS["admin"]})
        html = c.get("/admin/v/1/zielmonitor").text
        abschnitt = re.search(r'name="live_abschnitt" value="([^"]+:s1\.0)"', html).group(1)
    # Dauer länger als der Abruf (5 s): vorher kam der Monitor nie über die erste Seite hinaus
    felder = {"seite_an_0": "1", "seite_dauer_0": "7", "seite_live_0": "1", "seite_an_1": "1",
              "seite_dauer_1": "7", "seite_abschnitt_1": abschnitt}  # fmt: skip
    konfig_setzen(server, [{"aktion": "neu", "neuer_typ": "rangliste", "seite_an_0": "1", "seite_dauer_0": "7"},
                           {"aktion": "auf:1", **felder}])  # fmt: skip
    page = browser.new_page(viewport={"width": 1366, "height": 768})
    page.goto(f"{server.url}/monitor")
    expect(page.locator("#mon-bereich")).to_have_text("Rangliste Kreisel→Berg")
    gesehen = []
    for i in range(16):  # laufendes Rennen: alle 2 s eine neue Abschnittszeit in der Rangliste
        nr = i // 2 + 1
        if i % 2 == 0:
            _durchfahrt(server, nr, kreisel, "posten", starts[nr] + 3 * MIN)
        else:
            _durchfahrt(server, nr, berg, "posten-berg", starts[nr] + 8 * MIN + i * 1000)
        page.wait_for_timeout(1000)
        gesehen.append(page.locator("#mon-bereich").inner_text().lower())
    assert "ergebnis" in gesehen and gesehen[0].startswith("rangliste")
