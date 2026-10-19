"""Zielmonitor übersichtlich (Feedback 10/2026): Raster, Reihenfolge, Farben, Spalten, Blättern."""

import re
import uuid

import pytest
from playwright.sync_api import expect

from app.simulation import rennplan

PLAN = rennplan(seed=2026)  # 50 Fahrer, davon 5 Jugendliche (5 km), viele Klassen


@pytest.fixture
def teilnehmer_liste():
    return PLAN.fahrer


@pytest.fixture
def zwei_strecken():
    return True


def rennen(server):
    """Alle Starts und Zieleinläufe des Rennplans (ohne Uhr), über die API."""
    starts = [{"uuid": str(uuid.uuid4()), "art": "START", "startnummer": nr, "zeit_ms": t,
               "erstellt_ms": t, "geraet": "start-api"} for nr, t in PLAN.start_ms.items()]  # fmt: skip
    ziele = [{"uuid": str(uuid.uuid4()), "art": "ZIEL", "startnummer": nr, "zeit_ms": t,
              "erstellt_ms": t, "geraet": "ziel-api"} for nr, t in PLAN.ziel_ms.items() if nr != 40]  # fmt: skip
    server.senden(starts, rolle="start")
    server.senden(ziele, rolle="ziel")


def monitor(browser, server, breite, hoehe):
    page = browser.new_page(viewport={"width": breite, "height": hoehe})
    page.goto(f"{server.url}/monitor")
    expect(page.locator("#mon-klassen tr[data-nr]").first).to_be_visible()
    return page


def alle_klassen(page) -> list[str]:
    """Klassen in Anzeige-Reihenfolge über alle Seiten (blättert einmal ganz durch)."""
    namen, seiten = [], 1
    m = re.match(r"Seite 1/(\d+)", page.locator("#mon-seite").inner_text() or "")
    if m:
        seiten = int(m.group(1))
    for _ in range(seiten):
        for n in page.locator(".mon-klasse-kopf").evaluate_all(
            "ks => ks.map(k => k.dataset.klasse)"
        ):
            if not namen or namen[-1] != n:
                namen.append(n)
        page.evaluate("window.monitorBlaettern()")
    return namen


def test_raster_reihenfolge_farben_quer(browser, server):
    rennen(server)
    page = monitor(browser, server, 1366, 768)
    # Segmente 72: ein Spaltenkopf, das Ergebnis als eine Liste in voller Breite im gemeinsamen Raster
    expect(page.locator("#mon-spaltenkopf")).to_have_count(1)
    assert page.locator("#mon-klassen table").count() == 1
    assert page.locator("#mon-klassen").bounding_box()["width"] >= 1366 - 2
    namen_x = {round(b) for b in page.locator(".mon-liste tr[data-nr] td.name").evaluate_all(
        "ts => ts.map(t => t.getBoundingClientRect().left)")}  # fmt: skip
    assert len(namen_x) == 1  # Name in allen Bereichen an derselben Stelle
    # feste Reihenfolge: Strecke (5 km zuerst), Damen vor Herren, Alter aufsteigend
    namen = alle_klassen(page)
    jugend = [n for n in namen if n.startswith(("Mädchen", "Jungen", "Jugend"))]
    assert namen[: len(jugend)] == jugend  # 5-km-Strecke zuerst
    erw = namen[len(jugend) :]
    damen = [n for n in erw if n.startswith("Damen")]
    herren = [n for n in erw if n.startswith("Herren")]
    assert erw.index(herren[0]) > erw.index(damen[-1])
    reihenfolge = ["bis 30", "31-40", "41-50", "51-60", "61-70", "71-75", "ü 75"]
    for gruppe in (damen, herren):
        idx = [next(i for i, r in enumerate(reihenfolge) if n.endswith(r)) for n in gruppe]
        assert idx == sorted(idx), gruppe
    # Strecke nur in der Klassenüberschrift, neutral; nicht in jeder Zeile
    assert page.locator(".mon-klasse-kopf .mon-strecke").count() >= 1
    assert page.locator("#mon-klassen tr[data-nr] .mon-strecke").count() == 0
    farbe = page.locator(".mon-strecke").first.evaluate("e => getComputedStyle(e).color")
    assert farbe not in ("rgb(255, 0, 0)", "rgb(198, 40, 40)")
    # Farben: Gelb nur für Startnummern (gelbes Feld)
    gelb = "rgb(255, 212, 0)"
    nr = page.locator("#mon-klassen td.nr .nr-feld").first
    assert nr.evaluate("e => getComputedStyle(e).backgroundColor") == gelb
    for sel in (".mon-klasse-kopf th", "#mon-status", "#mon-titel"):
        assert page.locator(sel).first.evaluate("e => getComputedStyle(e).color") != gelb, sel
    # „Letzte Zieleinläufe“ und „Auf der Strecke“ bleiben beim Blättern sichtbar
    page.evaluate("window.monitorBlaettern()")
    expect(page.locator("#box-letzte")).to_be_in_viewport()
    expect(page.locator("#box-unterwegs")).to_be_in_viewport()
    assert page.evaluate("document.documentElement.scrollWidth") <= 1366


def test_hochkant_untereinander_ohne_waagerechtes_scrollen(browser, server):
    rennen(server)
    page = monitor(browser, server, 768, 1024)
    assert page.locator("#mon-klassen table").count() == 1
    unterwegs, letzte, ergebnis = (
        page.locator(s).bounding_box() for s in ("#box-unterwegs", "#box-letzte", "#box-ergebnis")
    )
    assert unterwegs["y"] < letzte["y"] < ergebnis["y"]  # untereinander
    assert page.evaluate("document.documentElement.scrollWidth") <= 768
    expect(page.locator("#mon-seite")).to_have_text(re.compile(r"^Seite 1/\d+$"))


def test_abzeichen_farben_unterscheidbar(browser, server):
    """Gold, Silber, Bronze in eigenen Farben, keine davon das Gelb der Startnummern."""
    page = browser.new_page()
    page.set_content(f"""<link rel="stylesheet" href="{server.url}/static/css/basis.css">
        <link rel="stylesheet" href="{server.url}/static/css/oeffentlich.css">
        <body class="monitor"><table><tr><td class="nr">1</td><td class="stufe"><span class="stufe-gold">Gold</span></td>
        <td class="stufe"><span class="stufe-silber">Silber</span></td><td class="stufe"><span class="stufe-bronze">Bronze</span></td>
        </tr></table></body>""")  # fmt: skip
    page.wait_for_function(
        "getComputedStyle(document.querySelector('.stufe-gold')).backgroundColor !== 'rgba(0, 0, 0, 0)'"
    )
    farben = [page.locator(f".stufe-{s}").evaluate("e => getComputedStyle(e).backgroundColor")
              for s in ("gold", "silber", "bronze")]  # fmt: skip
    assert len(set(farben)) == 3 and "rgb(255, 212, 0)" not in farben


@pytest.mark.parametrize("groesse", [(1366, 768), (768, 1024)], ids=["quer", "hochkant"])
def test_qr_code_zu_den_ergebnissen(browser, server, groesse):
    rennen(server)
    page = monitor(browser, server, *groesse)
    qr = page.locator("#mon-qr")
    expect(qr).to_be_in_viewport()
    assert qr.get_attribute("href").endswith("/ergebnisse")
    box = qr.locator("svg").bounding_box()
    assert box["width"] >= 60  # aus 1–3 m scanbar
    ergebnis = page.locator(".mon-ergebnis").bounding_box()
    assert box["y"] + box["height"] <= ergebnis["y"]  # überdeckt das Ergebnis nicht
    assert page.evaluate("document.documentElement.scrollWidth") <= groesse[0]
    vorher = page.locator("#mon-seite").inner_text()
    page.evaluate("window.monitorBlaettern()")
    if vorher:
        expect(page.locator("#mon-seite")).not_to_have_text(vorher)


def test_ergebnisseite_nur_eine_strecke_ohne_abzeichen(browser, server):
    """Segmente 14: Optionen der Ergebnisseite – Strecke(n) und Abzeichen-Spalte."""
    import httpx

    from tests.e2e.conftest import TOKENS

    rennen(server)
    with httpx.Client(base_url=server.url, follow_redirects=True) as c:
        c.post("/admin/anmelden", data={"token": TOKENS["admin"]})
        html = c.get("/admin/v/1/zielmonitor").text
        csrf = re.search(r'name="csrf" value="([0-9a-f]+)"', html).group(1)
        kurz = re.search(r'name="seite_strecken_0" value="(\d+)"[^>]*> 5 km', html).group(1)
        r = c.post("/admin/v/1/zielmonitor", data={"csrf": csrf, "aktion": "speichern", "seite_an_0": "1",
                                                   "seite_dauer_0": "10", "seite_live_0": "1",
                                                   "seite_strecken_0": kurz})  # fmt: skip
        assert "gespeichert" in r.text
    page = monitor(browser, server, 1366, 768)
    kopf = page.locator(".mon-klasse-kopf").evaluate_all("ks => ks.map(k => k.textContent)")
    assert kopf and all("5 km" in k for k in kopf)
    expect(page.locator("#mon-kopf-status")).to_have_text("Status")  # ohne „Abz.“
    expect(page.locator("#mon-klassen .abzeichen")).to_have_count(0)
