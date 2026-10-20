"""Im Browser: Schnellkorrektur ±0,2 s am Ziel (Erfassen und Rennleitung)."""

import uuid

from playwright.sync_api import expect

from tests.e2e.conftest import jetzt_synchronisieren, oeffnen, offline, warte_bis
from tests.e2e.test_ziel import erfassen, kachel, positionen, rennleitung


def fahrzeit(server, nr):
    return server.teilnehmer(nr)["fahrzeit_ms"]


def test_leiste_korrigiert_letzten_einlauf_auch_offline(server, neues_geraet):
    starts = server.starten(1, 2, 3)
    ctx = neues_geraet()
    ziel = oeffnen(ctx, server, "ziel")
    jetzt_synchronisieren(ziel)
    leiste, text = ziel.locator("#letzter-einlauf"), ziel.locator("#letzter-text")
    expect(text).to_have_text("Noch kein Einlauf erfasst")
    expect(ziel.locator("#btn-plus")).to_be_disabled()
    hoehe = leiste.bounding_box()["height"]
    kachel_y = kachel(ziel, 1).bounding_box()["y"]

    kachel(ziel, 1).click()
    expect(text).to_contain_text("Nr. 1 Nachname1")
    expect(kachel(ziel, 1)).to_have_count(0, timeout=3000)
    assert kachel(ziel, 2).bounding_box()["y"] == kachel_y  # Leiste hat sich nicht vergrößert
    vorher = positionen(ziel)
    ziel.click("#btn-plus")
    ziel.click("#btn-plus")
    expect(text).to_contain_text("korrigiert +2,0 s")
    # Kacheln und Leiste bleiben an ihrem Platz
    assert positionen(ziel) == vorher
    assert leiste.bounding_box()["height"] == hoehe
    jetzt_synchronisieren(ziel)
    warte_bis(lambda: fahrzeit(server, 1) is not None, text="Ziel Nr. 1 auf dem Server")
    roh = next(e for e in server.ereignisse() if e["art"] == "ZIEL")["zeit_ms"]
    warte_bis(lambda: fahrzeit(server, 1) == roh + 2000 - starts[1], text="+2 s auf dem Server")

    # offline: Korrektur sofort sichtbar, nach dem Sync auf dem Server
    offline(ctx, True)
    ziel.click("#btn-minus")
    expect(text).to_contain_text("korrigiert +1,0 s")
    expect(ziel.locator("#ausstehend")).to_have_text("1 ausstehend")
    offline(ctx, False)
    jetzt_synchronisieren(ziel)
    warte_bis(lambda: fahrzeit(server, 1) == roh + 1000 - starts[1], text="+1 s nach dem Sync")
    korrekturen = [e for e in server.ereignisse() if e["art"] == "ZEITKORREKTUR"]
    assert [e["delta_ms"] for e in korrekturen] == [1000, 1000, -1000]

    # Grenze ±5 s: danach nur noch Hinweis, kein Ereignis
    for _ in range(5):
        ziel.click("#btn-plus")
    expect(ziel.locator("#korrektur-hinweis")).to_have_text(
        "Mehr als ±5 s – bitte über Rennleitung korrigieren"
    )
    expect(text).to_contain_text("korrigiert +5,0 s")
    jetzt_synchronisieren(ziel)
    warte_bis(lambda: len([e for e in server.ereignisse() if e["art"] == "ZEITKORREKTUR"]) == 7,
              text="4 weitere Korrekturen")  # fmt: skip
    assert leiste.bounding_box()["height"] == hoehe  # auch mit Hinweis feste Höhe


def test_rennleitung_korrigiert_einlauf_eines_anderen_geraets(server, neues_geraet):
    starts = server.starten(1, 2)
    zeit = starts[2] + 20 * 60_000
    server.senden([{"uuid": str(uuid.uuid4()), "art": "ZIEL", "startnummer": 2, "zeit_ms": zeit,
                    "erstellt_ms": zeit, "geraet": "ziel-anderes"}], rolle="ziel")  # fmt: skip
    ziel = oeffnen(neues_geraet(), server, "ziel")
    jetzt_synchronisieren(ziel)
    rennleitung(ziel)
    eintrag = ziel.locator("#letzte li").first
    expect(eintrag).to_contain_text("Nr. 2")
    eintrag.locator(".letzte-text").click()  # Zeile aufklappen
    eintrag.locator(".btn-korrektur-minus").click()
    expect(ziel.locator("#letzte li").first).to_contain_text("korrigiert −1,0 s")
    jetzt_synchronisieren(ziel)
    warte_bis(lambda: fahrzeit(server, 2) == 20 * 60_000 - 1000, text="−1 s auf dem Server")
    # nach dem Umbuchen bleibt die Korrektur am Stempel
    ziel.locator("#letzte li").first.locator(".btn-umbuchen").click()
    ziel.locator("#auswahl button[data-nr='1']").click()
    jetzt_synchronisieren(ziel)
    warte_bis(
        lambda: fahrzeit(server, 1) == zeit - 1000 - starts[1], text="Korrektur nach Umbuchen"
    )
    expect(ziel.locator("#letzte li").first).to_contain_text("korrigiert −1,0 s")
    erfassen(ziel)
    expect(ziel.locator("#letzter-text")).to_have_text(
        "Noch kein Einlauf erfasst"
    )  # eigener Einlauf


def test_leiste_bleibt_bis_zum_naechsten_einlauf(server, neues_geraet):
    """Nicht zeitgesteuert, nicht durch Sync oder Neuladen geleert; erst der nächste Einlauf ersetzt sie."""
    server.starten(1, 2)
    ziel = oeffnen(neues_geraet(), server, "ziel")
    jetzt_synchronisieren(ziel)
    text = ziel.locator("#letzter-text")
    kachel(ziel, 1).click()
    expect(text).to_contain_text("Nr. 1 Nachname1")
    ziel.click("#btn-plus")
    ziel.clock.install()
    ziel.clock.fast_forward(61_000)  # über eine Minute später
    expect(text).to_contain_text("Nr. 1 Nachname1")
    jetzt_synchronisieren(ziel)
    expect(ziel.locator("#ausstehend")).to_have_text("0 ausstehend", timeout=10_000)
    expect(text).to_contain_text("Nr. 1 Nachname1")
    ziel.reload()
    ziel.wait_for_function("window.geraetBereit === true")
    expect(ziel.locator("#letzter-text")).to_contain_text("Nr. 1 Nachname1")
    expect(ziel.locator("#letzter-text")).to_contain_text("korrigiert +1,0 s")
    expect(ziel.locator("#btn-plus")).to_be_enabled()
    kachel(ziel, 2).click()
    expect(ziel.locator("#letzter-text")).to_contain_text("Nr. 2 Nachname2")
    expect(ziel.locator("#letzter-text")).not_to_contain_text("Nr. 1")


def test_rueckgaengig_in_der_leiste(server, neues_geraet):
    """Rückgängig nimmt den angezeigten Einlauf zurück (zweiter Tipp); Fahrer wieder als Kachel, Leiste leer."""
    server.starten(1, 2, 3)
    ziel = oeffnen(neues_geraet(), server, "ziel")
    jetzt_synchronisieren(ziel)
    leiste, text = ziel.locator("#letzter-einlauf"), ziel.locator("#letzter-text")
    knopf = ziel.locator("#btn-einlauf-zurueck")
    expect(knopf).to_be_disabled()
    hoehe = leiste.bounding_box()["height"]
    vorher = positionen(ziel)
    kachel(ziel, 2).click()
    expect(kachel(ziel, 2)).to_have_count(0, timeout=3000)
    knopf.click()
    expect(knopf).to_have_text("Wirklich?")
    expect(text).to_contain_text("Nr. 2")  # erst der zweite Tipp nimmt zurück
    knopf.click()
    expect(text).to_have_text("Noch kein Einlauf erfasst")
    expect(knopf).to_be_disabled()
    expect(kachel(ziel, 2)).to_have_count(1)
    assert positionen(ziel) == vorher and leiste.bounding_box()["height"] == hoehe
    jetzt_synchronisieren(ziel)
    expect(ziel.locator("#ausstehend")).to_have_text("0 ausstehend", timeout=10_000)
    warte_bis(lambda: server.stand()["im_ziel"] == [], text="Einlauf auf dem Server storniert")
    arten = [e["art"] for e in server.ereignisse() if e["art"] in ("ZIEL", "STORNO")]
    assert arten == ["ZIEL", "STORNO"]
    # nach dem Rücknehmen ohne zweiten Tipp verfällt die Rückfrage
    kachel(ziel, 3).click()
    knopf.click()
    ziel.wait_for_timeout(3_300)
    expect(knopf).to_have_text("Rückgängig")
    expect(text).to_contain_text("Nr. 3")
