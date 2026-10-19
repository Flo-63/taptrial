import re
import time

from playwright.sync_api import expect

from tests.e2e.conftest import jetzt_synchronisieren, oeffnen, offline, warte_bis


def kachel(page, nr):
    """Kachel in der Sicht „Erfassen“."""
    return page.locator(f"#fahrer button[data-nr='{nr}']")


def auswahl(page, nr):
    """Fahrerauswahl in der Sicht „Rennleitung“ (Zuordnen/Umbuchen)."""
    return page.locator(f"#auswahl button[data-nr='{nr}']")


def erfassen(page):
    page.click("#tab-erfassen")
    expect(page.locator("#sicht-erfassen")).to_be_visible()


def rennleitung(page):
    page.click("#tab-rennleitung")
    expect(page.locator("#sicht-rennleitung")).to_be_visible()


def ziel_ereignisse(server):
    return [e for e in server.ereignisse() if e["art"] == "ZIEL"]


def positionen(page) -> dict[str, tuple[float, float]]:
    return page.evaluate(
        """() => Object.fromEntries([...document.querySelectorAll('#fahrer button')].map(
            b => { const r = b.getBoundingClientRect();
                   return [b.dataset.nr, [r.x, r.y + window.scrollY]]; }))"""
    )


def test_start_und_ziel_parallel_einstufig(server, neues_geraet):
    start = oeffnen(neues_geraet(), server, "start")
    ziel = oeffnen(neues_geraet(), server, "ziel")
    expect(ziel.locator("#sicht-erfassen")).to_be_visible()
    expect(ziel.locator("#sicht-rennleitung")).to_be_hidden()
    for _ in range(3):
        start.click("#btn-start")
        start.wait_for_timeout(1100)
    warte_bis(lambda: server.stand()["gestartet"] == [1, 2, 3], text="3 Starts")
    jetzt_synchronisieren(ziel)
    expect(kachel(ziel, 1)).to_have_class("kachel")

    for nr in (2, 1, 3):  # Überholung: Nr. 2 vor Nr. 1 im Ziel
        kachel(ziel, nr).click()
        expect(kachel(ziel, nr)).to_have_count(0, timeout=3000)
    rennleitung(ziel)
    expect(ziel.locator("#letzte li")).to_have_count(3)
    warte_bis(lambda: server.stand()["im_ziel"] == [1, 2, 3], text="3 im Ziel")
    starts = {e["startnummer"]: e["zeit_ms"] for e in server.ereignisse() if e["art"] == "START"}
    ziele = {e["startnummer"]: e["zeit_ms"] for e in ziel_ereignisse(server)}
    for nr in (1, 2, 3):
        t = server.teilnehmer(nr)
        assert t["status"] == "OK"
        assert t["fahrzeit_ms"] == ziele[nr] - starts[nr] > 0


def test_erfassen_zeigt_nur_kacheln_und_zeit_ohne_nummer(server, neues_geraet):
    server.starten(1)
    ziel = oeffnen(neues_geraet(), server, "ziel")
    expect(ziel.locator("#btn-ohne-nummer")).to_be_visible()
    for bereich in ("#offene-zeiten", "#letzte", "#auswahl"):
        expect(ziel.locator(bereich)).to_be_hidden()
    expect(ziel.locator("#sicht-erfassen button")).to_have_count(10)
    # „Zeit ohne Nummer“ bleibt beim Scrollen sichtbar
    ziel.mouse.wheel(0, 2000)
    ziel.wait_for_timeout(300)
    expect(ziel.locator("#btn-ohne-nummer")).to_be_in_viewport()


def test_nicht_gestartete_ausgegraut_an_ihrem_platz(server, neues_geraet):
    server.starten(3)
    ziel = oeffnen(neues_geraet(), server, "ziel")
    expect(kachel(ziel, 3)).to_have_class("kachel")
    nummern = ziel.locator("#fahrer button").evaluate_all("bs => bs.map(b => b.dataset.nr)")
    assert nummern == [str(n) for n in range(1, 11)]
    expect(kachel(ziel, 1)).to_have_class("kachel nicht-gestartet")


def test_kacheln_verschieben_sich_nicht(server, neues_geraet):
    server.starten(1, 2)
    ziel = oeffnen(neues_geraet(), server, "ziel")
    expect(kachel(ziel, 2)).to_have_class("kachel")
    vorher = positionen(ziel)

    ziel.click("#btn-ohne-nummer")  # offene Zeit entsteht
    expect(ziel.locator("#badge-offen")).to_have_text("(1)")
    server.starten(4)  # weiterer Fahrer startet, Stand wird aktualisiert
    jetzt_synchronisieren(ziel)
    expect(kachel(ziel, 4)).to_have_class("kachel")
    rennleitung(ziel)
    erfassen(ziel)
    assert positionen(ziel) == vorher

    # nur der angetippte Fahrer verschwindet, die folgenden rücken nach
    kachel(ziel, 2).click()
    expect(kachel(ziel, 2)).to_have_count(0, timeout=3000)
    nachher = positionen(ziel)
    assert nachher["1"] == vorher["1"]
    assert nachher["3"] == vorher["2"]


def test_badge_zeigt_offene_zeiten(server, neues_geraet):
    server.starten(1)
    ziel = oeffnen(neues_geraet(), server, "ziel")
    expect(ziel.locator("#badge-offen")).to_be_hidden()
    ziel.click("#btn-ohne-nummer")
    ziel.click("#btn-ohne-nummer")
    expect(ziel.locator("#tab-rennleitung")).to_contain_text("Rennleitung (2)")
    rennleitung(ziel)
    ziel.locator("#offene-zeiten button").first.click()
    auswahl(ziel, 1).click()
    expect(ziel.locator("#tab-rennleitung")).to_contain_text("Rennleitung (1)")


def test_zwei_fahrer_innerhalb_einer_sekunde_ohne_nummer(server, neues_geraet):
    server.starten(1, 2)
    ziel = oeffnen(neues_geraet(), server, "ziel")
    ziel.click("#btn-ohne-nummer")
    ziel.click("#btn-ohne-nummer")
    warte_bis(lambda: len(server.stand()["offene_ziele"]) == 2, text="2 offene Zeiten")
    stempel = sorted(ziel_ereignisse(server), key=lambda e: e["zeit_ms"])
    assert stempel[1]["zeit_ms"] - stempel[0]["zeit_ms"] < 1000

    rennleitung(ziel)
    offene = ziel.locator("#offene-zeiten button")
    expect(offene).to_have_count(2)
    # erste Zeit gehört Nr. 2 (hat überholt), zweite Nr. 1
    offene.first.click()
    expect(ziel.locator("#zuordnen-box")).to_be_visible()
    auswahl(ziel, 2).click()
    expect(offene).to_have_count(1)
    offene.first.click()
    auswahl(ziel, 1).click()
    expect(offene).to_have_count(0)
    expect(ziel.locator("#zuordnen-box")).to_be_hidden()

    warte_bis(lambda: server.stand()["im_ziel"] == [1, 2], text="beide zugeordnet")
    stand = server.stand()
    assert stand["offene_ziele"] == [] and stand["konflikte"] == 0
    starts = {e["startnummer"]: e["zeit_ms"] for e in server.ereignisse() if e["art"] == "START"}
    assert server.teilnehmer(2)["fahrzeit_ms"] == stempel[0]["zeit_ms"] - starts[2]
    assert server.teilnehmer(1)["fahrzeit_ms"] == stempel[1]["zeit_ms"] - starts[1]


def test_zielgeraet_offline_drei_zieleinlaeufe(server, neues_geraet):
    starts = server.starten(1, 2, 3, 4)
    ctx = neues_geraet()
    ziel = oeffnen(ctx, server, "ziel")
    expect(kachel(ziel, 1)).to_have_class("kachel")
    offline(ctx, True)
    expect(ziel.locator("#netz")).to_have_text("offline")
    for nr in (1, 3, 2):
        kachel(ziel, nr).click()
        expect(kachel(ziel, nr)).to_have_count(0, timeout=3000)
    ziel.click("#btn-ohne-nummer")
    expect(ziel.locator("#ausstehend")).to_have_text("4 ausstehend")
    ziel.reload()
    ziel.wait_for_function("window.geraetBereit === true")
    expect(ziel.locator("#ausstehend")).to_have_text("4 ausstehend")
    expect(ziel.locator("#badge-offen")).to_have_text("(1)")

    # Sicht „Rennleitung“ funktioniert offline: offene Zeit zuordnen
    rennleitung(ziel)
    expect(ziel.locator("#letzte li")).to_have_count(4)
    ziel.locator("#offene-zeiten button").first.click()
    auswahl(ziel, 4).click()
    expect(ziel.locator("#offene-zeiten button")).to_have_count(0)
    expect(ziel.locator("#ausstehend")).to_have_text("5 ausstehend")
    assert server.stand()["im_ziel"] == []

    wieder_online_ms = int(time.time() * 1000)
    offline(ctx, False)
    warte_bis(lambda: server.stand()["im_ziel"] == [1, 2, 3, 4], text="Sync nach Funkloch")
    expect(ziel.locator("#ausstehend")).to_have_text("0 ausstehend")
    stempel = ziel_ereignisse(server)
    ziele = {e["startnummer"]: e["zeit_ms"] for e in stempel if e["startnummer"]}
    for nr in (1, 2, 3):
        t = server.teilnehmer(nr)
        assert t["status"] == "OK"
        assert t["fahrzeit_ms"] == ziele[nr] - starts[nr]
    ohne = next(e for e in stempel if e["startnummer"] is None)
    assert server.teilnehmer(4)["fahrzeit_ms"] == ohne["zeit_ms"] - starts[4]
    # Zielzeit ist die Gerätezeit beim Antippen, nicht die Zeit des späteren Syncs
    assert all(e["zeit_ms"] < wieder_online_ms <= e["empfangen_ms"] for e in stempel)


def test_doppeltipp_wird_ignoriert(server, neues_geraet):
    server.starten(1, 2)
    ziel = oeffnen(neues_geraet(), server, "ziel")
    expect(kachel(ziel, 1)).to_have_class("kachel")
    kachel(ziel, 1).click()
    kachel(ziel, 1).click()
    warte_bis(lambda: server.stand()["im_ziel"] == [1], text="Nr. 1 im Ziel")
    ziel.wait_for_timeout(1500)
    assert len(ziel_ereignisse(server)) == 1
    expect(kachel(ziel, 2)).to_have_count(1)
    rennleitung(ziel)
    expect(ziel.locator("#letzte li")).to_have_count(1)


def test_umbuchen_aus_letzten_fuenf(server, neues_geraet):
    server.starten(1, 2)
    ziel = oeffnen(neues_geraet(), server, "ziel")
    kachel(ziel, 1).click()  # versehentlich Nr. 1 statt Nr. 2
    rennleitung(ziel)
    eintrag = ziel.locator("#letzte li").first
    expect(eintrag).to_contain_text("Nr. 1")
    eintrag.locator(".letzte-text").click()
    eintrag.locator(".btn-umbuchen").click()
    expect(ziel.locator("#zuordnen-box")).to_be_visible()
    auswahl(ziel, 2).click()
    expect(eintrag).to_contain_text("Nr. 2")
    erfassen(ziel)
    expect(kachel(ziel, 1)).to_have_count(1)
    expect(kachel(ziel, 2)).to_have_count(0)
    warte_bis(lambda: server.stand()["im_ziel"] == [2], text="umgebucht auf Nr. 2")
    assert server.teilnehmer(1)["status"] == "UNVOLLSTAENDIG"


def test_zuordnen_abbrechen(server, neues_geraet):
    server.starten(1)
    ziel = oeffnen(neues_geraet(), server, "ziel")
    ziel.click("#btn-ohne-nummer")
    rennleitung(ziel)
    ziel.locator("#offene-zeiten button").first.click()
    ziel.click("#btn-zuordnen-abbrechen")
    expect(ziel.locator("#zuordnen-box")).to_be_hidden()
    expect(ziel.locator("#offene-zeiten button")).to_have_count(1)


def test_rueckgaengig_aus_letzten_fuenf(server, neues_geraet):
    server.starten(1)
    ziel = oeffnen(neues_geraet(), server, "ziel")
    kachel(ziel, 1).click()
    warte_bis(lambda: server.stand()["im_ziel"] == [1], text="Nr. 1 im Ziel")
    rennleitung(ziel)
    ziel.locator("#letzte li .letzte-text").first.click()
    knopf = ziel.locator("#letzte li").first.locator(".btn-rueckgaengig")
    knopf.click()
    expect(knopf).to_contain_text("Wirklich")
    knopf.click()
    expect(ziel.locator("#letzte li")).to_have_count(0)
    erfassen(ziel)
    expect(kachel(ziel, 1)).to_have_count(1)
    warte_bis(lambda: server.stand()["im_ziel"] == [], text="Storno auf dem Server")


def test_nur_letzte_fuenf_sichtbar(server, neues_geraet):
    server.starten(*range(1, 8))
    ziel = oeffnen(neues_geraet(), server, "ziel")
    for _ in range(7):
        ziel.click("#btn-ohne-nummer")
    expect(ziel.locator("#badge-offen")).to_have_text("(7)")
    rennleitung(ziel)
    expect(ziel.locator("#offene-zeiten button")).to_have_count(7)
    expect(ziel.locator("#letzte li")).to_have_count(5)


def test_gewaehlte_sicht_bleibt_nach_neuladen(server, neues_geraet):
    ziel = oeffnen(neues_geraet(), server, "ziel")
    rennleitung(ziel)
    ziel.reload()
    ziel.wait_for_function("window.geraetBereit === true")
    expect(ziel.locator("#sicht-rennleitung")).to_be_visible()


def bereich(page, name):
    """Reiter der Rennleitung wählen: offen, letzte oder status (Test 08.10.)."""
    page.click(f"#rl-tab-{name}")
    expect(page.locator(f"#rl-{name}")).to_be_visible()


def status_kachel(page, nr):
    if page.locator("#rl-status").is_hidden():
        bereich(page, "status")
    return page.locator(f"#status-auswahl button[data-nr='{nr}']")


def status_setzen(page, nr, knopf):
    status_kachel(page, nr).click()
    expect(page.locator("#status-box")).to_be_visible()
    page.click(knopf)
    expect(page.locator(knopf)).to_contain_text("Wirklich")
    page.click(knopf)
    expect(page.locator("#status-box")).to_be_hidden()


def test_tab_heisst_rennleitung(server, neues_geraet):
    ziel = oeffnen(neues_geraet(), server, "ziel")
    expect(ziel.locator("#tab-rennleitung")).to_contain_text("Rennleitung")
    expect(ziel.locator("#tab-rennleitung")).not_to_contain_text("Korrigieren")
    expect(ziel.locator("#badge-offen")).to_be_hidden()


def test_dnf_mit_bestaetigung_blendet_aus_und_ok_hebt_auf(server, neues_geraet):
    server.starten(1, 2, 3)
    ziel = oeffnen(neues_geraet(), server, "ziel")
    expect(kachel(ziel, 2)).to_have_class("kachel")
    rennleitung(ziel)
    status_kachel(ziel, 2).click()
    expect(ziel.locator("#btn-status-ok")).to_be_hidden()  # noch kein Status
    ziel.click("#btn-status-dnf")
    expect(ziel.locator("#btn-status-dnf")).to_contain_text("Wirklich DNF?")
    assert server.teilnehmer(2)["status"] == "UNVOLLSTAENDIG"  # erst nach Bestätigung
    ziel.click("#btn-status-dnf")
    expect(status_kachel(ziel, 2)).to_contain_text("DNF")

    erfassen(ziel)  # wirkt sofort, auch offline-fähig ohne Serverantwort
    expect(kachel(ziel, 2)).to_have_count(0)
    expect(ziel.locator("#fahrer")).not_to_contain_text("DNF")  # keine Statusanzeige
    warte_bis(lambda: server.teilnehmer(2)["status"] == "DNF", text="DNF auf dem Server")

    rennleitung(ziel)
    status_setzen(ziel, 2, "#btn-status-ok")
    erfassen(ziel)
    expect(kachel(ziel, 2)).to_have_count(1)
    warte_bis(lambda: server.teilnehmer(2)["status"] == "UNVOLLSTAENDIG", text="Status aufgehoben")


def test_dsq_vorlaeufig_mit_pflichtgrund(server, neues_geraet):
    server.starten(1, 2)
    ziel = oeffnen(neues_geraet(), server, "ziel")
    rennleitung(ziel)
    status_kachel(ziel, 1).click()
    expect(ziel.locator("#btn-status-dsq")).to_have_text("DSQ vorläufig")
    expect(ziel.locator("#btn-status-dsq")).to_be_disabled()  # ohne Grund nicht möglich
    ziel.fill("#status-grund", "Windschatten gefahren")
    expect(ziel.locator("#btn-status-dsq")).to_be_enabled()
    ziel.click("#btn-status-dsq")
    expect(ziel.locator("#btn-status-dsq")).to_contain_text("Wirklich")
    ziel.click("#btn-status-dsq")
    expect(ziel.locator("#status-box")).to_be_hidden()
    expect(status_kachel(ziel, 1)).to_contain_text("DSQ vorläufig")
    expect(status_kachel(ziel, 1)).to_have_class(re.compile("vorbehalt"))
    warte_bis(
        lambda: server.teilnehmer(1)["dsq_vorlaeufig"] == "Windschatten gefahren",
        text="vorläufiger DSQ mit Grund auf dem Server",
    )
    dsq = next(e for e in server.ereignisse() if e["art"] == "STATUS")
    assert (dsq["wert"], dsq["notiz"]) == ("DSQ_VORLAEUFIG", "Windschatten gefahren")

    # bleibt in „Erfassen“ (Zeit und Rang bleiben unter Vorbehalt), ohne Statusanzeige
    erfassen(ziel)
    expect(kachel(ziel, 1)).to_have_count(1)
    expect(ziel.locator("#fahrer")).not_to_contain_text("DSQ")
    kachel(ziel, 1).click()
    warte_bis(lambda: server.teilnehmer(1)["status"] == "OK", text="gewertet unter Vorbehalt")
    rennleitung(ziel)
    expect(ziel.locator("#letzte li").first).to_contain_text("DSQ vorläufig")

    # Rennleitung kann den vorläufigen DSQ aufheben
    status_setzen(ziel, 1, "#btn-status-ok")
    warte_bis(lambda: server.teilnehmer(1)["dsq_vorlaeufig"] is None, text="aufgehoben")


def test_bestaetigter_dsq_nur_im_admin_aenderbar(server, neues_geraet):
    server.starten(1, 2)
    server.senden(
        [{"uuid": "dsq-1", "art": "STATUS", "startnummer": 1, "wert": "DSQ",
          "notiz": "bestätigt", "erstellt_ms": 1, "geraet": "admin"}],
        rolle="admin",
    )  # fmt: skip
    ziel = oeffnen(neues_geraet(), server, "ziel")
    expect(kachel(ziel, 1)).to_have_count(0)  # endgültiger DSQ: ausgeblendet
    rennleitung(ziel)
    expect(status_kachel(ziel, 1)).to_contain_text("DSQ")
    status_kachel(ziel, 1).click()
    expect(ziel.locator("#status-text")).to_contain_text("nur im Admin")
    for knopf in ("#btn-status-dnf", "#btn-status-dsq", "#btn-status-ok", "#status-grund"):
        expect(ziel.locator(knopf)).to_be_hidden()


def test_erfassen_zeigt_ueberblick_und_endzustand(server, neues_geraet):
    ziel = oeffnen(neues_geraet(), server, "ziel")
    info = ziel.locator("#ziel-info")
    expect(info).to_have_text("Noch niemand gestartet")
    server.starten(1, 2)
    jetzt_synchronisieren(ziel)
    expect(info).to_have_text("2 auf der Strecke")
    hoehe = info.bounding_box()["height"]
    kachel(ziel, 1).click()
    expect(info).to_have_text("1 auf der Strecke")
    kachel(ziel, 2).click()
    expect(info).to_have_text("Alle gestarteten Fahrer im Ziel · 8 noch nicht gestartet")
    assert info.bounding_box()["height"] == hoehe  # feste Höhe, verschiebt keine Kacheln


def test_status_abbrechen(server, neues_geraet):
    server.starten(1)
    ziel = oeffnen(neues_geraet(), server, "ziel")
    rennleitung(ziel)
    status_kachel(ziel, 1).click()
    ziel.click("#btn-status-dnf")
    ziel.click("#btn-status-abbrechen")
    expect(ziel.locator("#status-box")).to_be_hidden()
    ziel.wait_for_timeout(500)
    assert server.ereignisse()[-1]["art"] == "START"


def test_dns_vom_start_wird_in_erfassen_ausgeblendet(server, neues_geraet):
    server.starten(1)
    ziel = oeffnen(neues_geraet(), server, "ziel")
    expect(kachel(ziel, 2)).to_have_class("kachel nicht-gestartet")
    server.senden(
        [
            {
                "uuid": "dns-2",
                "art": "STATUS",
                "startnummer": 2,
                "wert": "DNS",
                "erstellt_ms": 1,
                "geraet": "start-api",
            }
        ]
    )
    jetzt_synchronisieren(ziel)
    expect(kachel(ziel, 2)).to_have_count(0)
    expect(kachel(ziel, 3)).to_have_class("kachel nicht-gestartet")


def test_status_wirkt_auf_anderem_zielgeraet(server, neues_geraet):
    server.starten(1, 2)
    richter = oeffnen(neues_geraet(), server, "ziel")
    leitung = oeffnen(neues_geraet(), server, "ziel")
    expect(kachel(richter, 1)).to_have_class("kachel")
    rennleitung(leitung)
    status_setzen(leitung, 1, "#btn-status-dnf")
    warte_bis(lambda: server.teilnehmer(1)["status"] == "DNF", text="DNF auf dem Server")
    jetzt_synchronisieren(richter)
    expect(kachel(richter, 1)).to_have_count(0)


def test_rennleitung_allein_auf_zweitem_geraet(server, neues_geraet):
    server.starten(1, 2, 3)
    richter = oeffnen(neues_geraet(), server, "ziel")
    leitung = neues_geraet().new_page()
    leitung.goto(server.seite("ziel") + "&sicht=rennleitung")
    leitung.wait_for_function("window.geraetBereit === true")
    expect(leitung.locator("#sicht-rennleitung")).to_be_visible()

    kachel(richter, 1).click()  # falsch, war Nr. 3
    richter.click("#btn-ohne-nummer")
    warte_bis(lambda: len(server.stand()["letzte_ziele"]) == 2, text="Stempel auf dem Server")
    jetzt_synchronisieren(leitung)

    # Einläufe des anderen Geräts sind sichtbar und korrigierbar
    expect(leitung.locator("#letzte li")).to_have_count(2)
    expect(leitung.locator("#badge-offen")).to_have_text("(1)")
    bereich(leitung, "letzte")
    eintrag = leitung.locator("#letzte li", has_text="Nr. 1")
    eintrag.locator(".letzte-text").click()
    eintrag.locator(".btn-umbuchen").click()
    auswahl(leitung, 3).click()
    expect(leitung.locator("#letzte li", has_text="Nr. 3")).to_have_count(1)
    bereich(leitung, "offen")
    leitung.locator("#offene-zeiten button").first.click()
    auswahl(leitung, 2).click()
    expect(leitung.locator("#offene-zeiten button")).to_have_count(0)

    warte_bis(lambda: server.stand()["im_ziel"] == [2, 3], text="Korrekturen auf dem Server")
    jetzt_synchronisieren(richter)
    expect(kachel(richter, 1)).to_have_count(1)
    expect(kachel(richter, 3)).to_have_count(0)
    expect(richter.locator("#badge-offen")).to_be_hidden()


def test_ausgeblendeter_fahrer_kommt_doch_ins_ziel(server, neues_geraet):
    server.starten(1, 2)
    ziel = oeffnen(neues_geraet(), server, "ziel")
    rennleitung(ziel)
    status_setzen(ziel, 2, "#btn-status-dnf")
    erfassen(ziel)
    expect(kachel(ziel, 2)).to_have_count(0)
    ziel.click("#btn-ohne-nummer")  # Nr. 2 erscheint doch im Ziel
    rennleitung(ziel)
    bereich(ziel, "offen")
    ziel.locator("#offene-zeiten button").first.click()
    expect(auswahl(ziel, 2)).to_contain_text("DNF")  # auch ausgeblendete sind wählbar
    auswahl(ziel, 2).click()
    status_setzen(ziel, 2, "#btn-status-ok")
    warte_bis(lambda: server.teilnehmer(2)["status"] == "OK", text="Nr. 2 gewertet")
