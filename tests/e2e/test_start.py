import re

from playwright.sync_api import expect

from tests.e2e.conftest import jetzt_synchronisieren, oeffnen, offline, warte_bis

ENTPRELLUNG_MS = 1100


def starten(page, anzahl: int) -> None:
    for _ in range(anzahl):
        page.click("#btn-start")
        page.wait_for_timeout(ENTPRELLUNG_MS)


def gestartet(server) -> list[int]:
    return server.stand()["gestartet"]


def test_seite_zeigt_naechsten_und_status(server, neues_geraet):
    page = oeffnen(neues_geraet(), server, "start")
    expect(page.locator("#naechster-nr")).to_have_text("1")
    expect(page.locator("#naechster-name")).to_have_text("Vorname1 Nachname1")
    expect(page.locator("#netz")).to_have_text("online")
    expect(page.locator("#ausstehend")).to_have_text("0 ausstehend")
    expect(page.locator("#letzter-sync")).not_to_have_text("–")


def test_ohne_token_hinweis(server, neues_geraet):
    page = neues_geraet().new_page()
    page.goto(f"{server.url}/start")
    expect(page.locator("#fehler")).to_contain_text("Gerätelink")


def test_manifest_verlinkt(server, neues_geraet):
    page = oeffnen(neues_geraet(), server, "start")
    href = page.locator("link[rel=manifest]").get_attribute("href")
    manifest = page.request.get(server.url + href).json()
    assert manifest["display"] == "standalone"
    assert manifest["start_url"].startswith("/start")


def test_online_offline_neuladen_sync(server, neues_geraet):
    ctx = neues_geraet()
    page = oeffnen(ctx, server, "start")

    starten(page, 5)
    warte_bis(lambda: gestartet(server) == [1, 2, 3, 4, 5], text="5 Starts online")
    expect(page.locator("#naechster-nr")).to_have_text("6")

    offline(ctx, True)
    expect(page.locator("#netz")).to_have_text("offline")
    starten(page, 5)
    expect(page.locator("#ausstehend")).to_have_text("5 ausstehend")
    expect(page.locator("#alle-gestartet")).to_have_text("Alle Fahrer gestartet")
    expect(page.locator("#naechster-box")).to_be_hidden()

    page.reload()  # offline neu laden: App-Shell aus dem Service Worker, Daten aus IndexedDB
    page.wait_for_function("window.geraetBereit === true")
    expect(page.locator("#ausstehend")).to_have_text("5 ausstehend")
    expect(page.locator("#alle-gestartet")).to_have_text("Alle Fahrer gestartet")
    expect(page.locator("#naechster-box")).to_be_hidden()
    assert gestartet(server) == [1, 2, 3, 4, 5]

    offline(ctx, False)
    warte_bis(lambda: gestartet(server) == list(range(1, 11)), text="alle 10 auf dem Server")
    expect(page.locator("#ausstehend")).to_have_text("0 ausstehend")
    expect(page.locator("#netz")).to_have_text("online")
    starts = [e for e in server.ereignisse() if e["art"] == "START"]
    assert len(starts) == 10
    assert all(e["geraet"].startswith("start-") for e in starts)
    zeiten = [e["zeit_ms"] for e in sorted(starts, key=lambda e: e["startnummer"])]
    assert zeiten == sorted(zeiten)


def test_ueberspringen(server, neues_geraet):
    page = oeffnen(neues_geraet(), server, "start")
    page.click("#btn-ueberspringen")
    expect(page.locator("#naechster-nr")).to_have_text("2")
    expect(page.locator("#warteliste li").last).to_contain_text("1")
    starten(page, 1)
    warte_bis(lambda: gestartet(server) == [2], text="Nr. 2 gestartet")
    expect(page.locator("#naechster-nr")).to_have_text("3")
    # Übersprungene Nr. 1 bleibt am Ende und kommt nach Nr. 10
    for _ in range(8):
        starten(page, 1)
    expect(page.locator("#naechster-nr")).to_have_text("1")


def test_dns(server, neues_geraet):
    page = oeffnen(neues_geraet(), server, "start")
    page.click("#btn-dns")
    expect(page.locator("#btn-dns")).to_contain_text("Wirklich")
    expect(page.locator("#naechster-nr")).to_have_text("1")  # erst nach Bestätigung
    page.click("#btn-dns")
    expect(page.locator("#naechster-nr")).to_have_text("2")

    def status1():
        return next(t for t in server.stand()["teilnehmer"] if t["startnummer"] == 1)["status"]

    warte_bis(lambda: status1() == "DNS", text="DNS auf dem Server")
    assert gestartet(server) == []


def test_rueckgaengig(server, neues_geraet):
    page = oeffnen(neues_geraet(), server, "start")
    expect(page.locator("#btn-rueckgaengig")).to_be_disabled()
    starten(page, 1)
    expect(page.locator("#naechster-nr")).to_have_text("2")
    expect(page.locator("#letzter-start")).to_contain_text("Nr. 1")
    expect(page.locator("#btn-rueckgaengig")).to_be_enabled()
    page.click("#btn-rueckgaengig")
    expect(page.locator("#naechster-nr")).to_have_text("1")
    expect(page.locator("#btn-rueckgaengig")).to_be_disabled()
    warte_bis(
        lambda: [e["art"] for e in server.ereignisse()] == ["START", "STORNO"],
        text="Storno auf dem Server",
    )
    assert gestartet(server) == []


def test_doppeltipp_startet_nur_einmal(server, neues_geraet):
    page = oeffnen(neues_geraet(), server, "start")
    page.click("#btn-start")
    page.click("#btn-start")
    expect(page.locator("#naechster-nr")).to_have_text("2")
    warte_bis(lambda: gestartet(server) == [1], text="ein Start")
    page.wait_for_timeout(500)
    assert len(server.ereignisse()) == 1


def test_nummer_manuell_waehlen(server, neues_geraet):
    """„Nummer wählen“ ist sofort an: Kachel → Bestätigung → START; danach normale Reihenfolge."""
    page = oeffnen(neues_geraet(), server, "start")
    expect(page.locator("#toggle-nummer")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#auswahl")).to_be_visible()  # Kacheln ohne weiteres Antippen sichtbar
    page.click("#auswahl button[data-nr='4']")
    bestaetigung = page.locator("#auswahl-bestaetigung")
    expect(bestaetigung).to_contain_text("Nr. 4 – Vorname4 Nachname4")
    expect(page.locator("#naechster-nr")).to_have_text("1")  # erst nach Bestätigung
    page.click("#btn-auswahl-abbrechen")
    expect(bestaetigung).to_be_hidden()
    expect(page.locator("#auswahl")).to_be_visible()  # Kacheln bleiben
    expect(page.locator("#naechster-nr")).to_have_text("1")
    page.click("#auswahl button[data-nr='4']")
    page.click("#btn-auswahl-bestaetigen")
    expect(page.locator("#naechster-nr")).to_have_text("4")
    expect(page.locator("#naechster-box")).to_have_class(re.compile(r"\bgewaehlt\b"))
    starten(page, 1)
    expect(page.locator("#naechster-nr")).to_have_text("1")
    expect(page.locator("#naechster-box")).not_to_have_class(re.compile(r"\bgewaehlt\b"))
    warte_bis(lambda: gestartet(server) == [4], text="Nr. 4 gestartet")
    expect(page.locator("#auswahl button[data-nr='4']")).to_have_count(0)  # gestartet: keine Kachel


def test_warteliste_startet_niemanden(server, neues_geraet):
    page = oeffnen(neues_geraet(), server, "start")
    page.click("#toggle-warteliste")
    eintrag = page.locator("#warteliste li").nth(2)
    expect(eintrag).to_contain_text("Nr. 3")
    eintrag.click()
    page.wait_for_timeout(1_200)
    expect(page.locator("#naechster-nr")).to_have_text("1")
    expect(page.locator("#ausstehend")).to_have_text("0 ausstehend")
    assert page.locator("#warteliste button, #warteliste a").count() == 0
    assert server.ereignisse() == []


def test_umschalter_standard_merken_und_zuruecksetzen(server, neues_geraet):
    page = oeffnen(neues_geraet(), server, "start")
    nummer, liste = page.locator("#toggle-nummer"), page.locator("#toggle-warteliste")
    expect(nummer).to_have_attribute("aria-pressed", "true")
    expect(liste).to_have_attribute("aria-pressed", "false")
    expect(page.locator("#auswahl")).to_be_visible()
    expect(page.locator(".warteliste-box")).to_be_hidden()
    # beide an
    liste.click()
    expect(page.locator(".warteliste-box")).to_be_visible()
    expect(page.locator("#auswahl")).to_be_visible()
    # beide aus: START mit fester Reihenfolge geht weiter
    nummer.click()
    liste.click()
    expect(page.locator("#auswahl")).to_be_hidden()
    expect(page.locator(".warteliste-box")).to_be_hidden()
    expect(page.locator("#btn-start")).to_be_enabled()
    # Stellung bleibt nach Neuladen (auch offline aus dem Gerätespeicher)
    page.reload()
    page.wait_for_function("window.geraetBereit === true")
    expect(page.locator("#toggle-nummer")).to_have_attribute("aria-pressed", "false")
    expect(page.locator("#toggle-warteliste")).to_have_attribute("aria-pressed", "false")
    expect(page.locator("#auswahl")).to_be_hidden()
    page.locator("#toggle-warteliste").click()
    page.reload()
    page.wait_for_function("window.geraetBereit === true")
    expect(page.locator(".warteliste-box")).to_be_visible()
    # „Gerät zurücksetzen“ stellt den Standard wieder her
    page.click("#btn-zuruecksetzen")
    page.click("#btn-zuruecksetzen")
    expect(page.locator("#ende-text")).to_contain_text("Gerät zurückgesetzt")
    page.goto(server.seite("start"))
    page.wait_for_function("window.geraetBereit === true")
    expect(page.locator("#toggle-nummer")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#toggle-warteliste")).to_have_attribute("aria-pressed", "false")
    assert page.locator("#einst-nummer-waehlen-aus").count() == 0  # altes Opt-out entfällt


def test_start_bei_390_px_ohne_waagerechtes_scrollen(server, browser):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, service_workers="block")
    page = ctx.new_page()
    page.goto(server.seite("start"))
    page.wait_for_function("window.geraetBereit === true")
    page.click("#toggle-warteliste")  # beide an: Kacheln und Warteliste
    expect(page.locator(".warteliste-box")).to_be_visible()
    page.click("#auswahl button[data-nr='3']")
    expect(page.locator("#auswahl-bestaetigung")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    ctx.close()


def test_uhr_ist_als_uhrzeit_beschriftet(server, neues_geraet):
    """Laufende Uhr nur hh:mm:ss (keine Sekundenbruchteile); gemessene Zeiten bleiben in Zehnteln."""
    page = oeffnen(neues_geraet(), server, "start")
    expect(page.locator(".uhr-box")).to_contain_text("Uhrzeit")
    expect(page.locator("#uhr")).to_have_text(re.compile(r"^\d{2}:\d{2}:\d{2}$"))
    ziel = oeffnen(neues_geraet(), server, "ziel")
    expect(ziel.locator("#uhr")).to_have_text(re.compile(r"^\d{2}:\d{2}:\d{2}$"))
    starten(page, 1)
    expect(page.locator("#letzter-start")).to_contain_text(re.compile(r"\d{2}:\d{2}:\d{2},\d"))


def test_endzustand_alle_fahrer_gestartet(server, neues_geraet):
    page = oeffnen(neues_geraet(), server, "start")
    expect(page.locator("#alle-gestartet")).to_be_hidden()
    server.starten(*range(1, 11))
    jetzt_synchronisieren(page)
    expect(page.locator("#alle-gestartet")).to_be_visible()
    expect(page.locator("#alle-gestartet")).to_have_text("Alle Fahrer gestartet")
    expect(page.locator("#btn-start")).to_be_disabled()


def waehlen(page, nr):
    page.click(f"#auswahl button[data-nr='{nr}']")
    page.click("#btn-auswahl-bestaetigen")
    expect(page.locator("#naechster-nr")).to_have_text(str(nr))


def neu_laden(page):
    page.reload()
    page.wait_for_function("window.geraetBereit === true")


def test_gewaehlte_nummer_bleibt_nach_neuladen_und_offline(server, neues_geraet):
    ctx = neues_geraet()
    page = oeffnen(ctx, server, "start")
    waehlen(page, 5)
    neu_laden(page)
    expect(page.locator("#naechster-nr")).to_have_text("5")
    expect(page.locator("#naechster-box")).to_have_class(re.compile(r"\bgewaehlt\b"))
    offline(ctx, True)
    neu_laden(page)
    expect(page.locator("#naechster-nr")).to_have_text("5")
    # START verbraucht die Wahl, danach normale Reihenfolge – auch nach Neuladen
    page.click("#btn-start")
    expect(page.locator("#naechster-nr")).to_have_text("1")
    neu_laden(page)
    expect(page.locator("#naechster-nr")).to_have_text("1")
    offline(ctx, False)


def test_gewaehlte_nummer_aufheben_dns_und_extern_gestartet(server, neues_geraet):
    page = oeffnen(neues_geraet(), server, "start")
    waehlen(page, 4)
    page.click("#btn-wahl-aufheben")  # Abbrechen
    expect(page.locator("#naechster-nr")).to_have_text("1")
    neu_laden(page)
    expect(page.locator("#naechster-nr")).to_have_text("1")
    # DNS verbraucht die Wahl
    waehlen(page, 4)
    page.click("#btn-dns")
    page.click("#btn-dns")
    expect(page.locator("#naechster-nr")).to_have_text("1")
    neu_laden(page)
    expect(page.locator("#naechster-nr")).to_have_text("1")
    # gewählter Fahrer wurde inzwischen gestartet (anderes Gerät): Wahl wird verworfen
    waehlen(page, 6)
    server.starten(6)
    jetzt_synchronisieren(page)
    expect(page.locator("#naechster-nr")).to_have_text("1")
    neu_laden(page)
    expect(page.locator("#naechster-nr")).to_have_text("1")
    assert page.evaluate("localStorage.getItem('rcb_start_gewaehlt')") is None


def test_zuruecksetzen_loescht_wahl(server, neues_geraet):
    page = oeffnen(neues_geraet(), server, "start")
    waehlen(page, 3)
    page.click("#btn-zuruecksetzen")
    page.click("#btn-zuruecksetzen")
    expect(page.locator("#ende-text")).to_contain_text("Gerät zurückgesetzt")
    assert page.evaluate("localStorage.getItem('rcb_start_gewaehlt')") is None


def test_dns_am_startgeraet_aufheben(server, neues_geraet):
    page = oeffnen(neues_geraet(), server, "start")
    page.click("#btn-dns")
    page.click("#btn-dns")  # Nr. 1 DNS
    expect(page.locator("#naechster-nr")).to_have_text("2")
    kachel = page.locator("#auswahl button[data-nr='1']")
    expect(kachel).to_have_class(re.compile(r"\bdns\b"))  # grau an ihrem Platz
    expect(kachel).to_contain_text("DNS")
    assert page.locator("#auswahl button").first.get_attribute("data-nr") == "1"
    kachel.click()
    expect(kachel).to_contain_text("DNS aufheben?")
    expect(page.locator("#auswahl-bestaetigung")).to_be_hidden()  # keine Auswahl als Nächster
    page.wait_for_timeout(3_300)  # ohne zweiten Tipp verfällt die Rückfrage
    expect(kachel).not_to_contain_text("aufheben")
    kachel.click()
    kachel.click()
    expect(kachel).not_to_have_class(re.compile(r"\bdns\b"))
    warte_bis(lambda: server.teilnehmer(1)["status"] != "DNS", text="DNS aufgehoben auf dem Server")
    werte = [e["wert"] for e in server.ereignisse() if e["art"] == "STATUS"]
    assert werte == ["DNS", "OK"]
    # danach wieder normal wählbar und startbar
    waehlen(page, 1)
    page.wait_for_timeout(1_100)
    page.click("#btn-start")
    warte_bis(lambda: 1 in gestartet(server), text="Nr. 1 gestartet")


def test_dnf_dsq_am_start_nicht_aenderbar(server, neues_geraet):
    import uuid

    server.starten(2)
    server.senden([{"uuid": str(uuid.uuid4()), "art": "STATUS", "startnummer": 2, "wert": "DNF",
                    "erstellt_ms": 1, "geraet": "ziel"}], rolle="ziel")  # fmt: skip
    page = oeffnen(neues_geraet(), server, "start")
    expect(page.locator("#auswahl button[data-nr='1']")).to_be_visible()
    expect(page.locator("#auswahl button[data-nr='2']")).to_have_count(0)


def test_endzustand_mit_dns_bleibt_aufhebbar(server, neues_geraet):
    """Segmente 35: Alle gestartet oder DNS – die DNS-Kacheln bleiben unter „Nummer wählen“
    sichtbar; nach „DNS aufheben“ ist der Fahrer wieder „Nächster“ und startet. Auch offline."""
    ctx = neues_geraet()
    page = oeffnen(ctx, server, "start")
    server.starten(*range(1, 9))
    jetzt_synchronisieren(page)
    expect(page.locator("#naechster-nr")).to_have_text("9")
    for _ in range(2):  # Nr. 9 und Nr. 10 DNS
        page.click("#btn-dns")
        page.click("#btn-dns")
        page.wait_for_timeout(300)
    expect(page.locator("#alle-gestartet")).to_be_visible()
    expect(page.locator("#dns-hinweis")).to_have_text(
        "2 Fahrer mit DNS – zum Aufheben unten die Kachel zweimal antippen."
    )
    # auch mit ausgeschaltetem „Nummer wählen“ (sonst stünde nur „Alle Fahrer gestartet“ da)
    page.click("#toggle-nummer")
    expect(page.locator("#toggle-nummer")).to_have_attribute("aria-pressed", "false")
    expect(page.locator("#auswahl")).to_be_visible()
    kachel = page.locator("#auswahl button[data-nr='10']")
    expect(kachel).to_be_visible()
    expect(kachel).to_contain_text("DNS")
    expect(page.locator("#auswahl button")).to_have_count(2)  # nur die DNS-Fahrer
    offline(ctx, True)
    kachel.click()
    expect(kachel).to_contain_text("DNS aufheben?")
    kachel.click()
    expect(page.locator("#naechster-nr")).to_have_text("10")
    expect(page.locator("#alle-gestartet")).to_be_hidden()
    expect(page.locator("#dns-hinweis")).to_be_hidden()
    expect(page.locator("#auswahl")).to_be_hidden()  # „Nummer wählen“ ist aus, kein Endzustand
    page.click("#btn-start")
    expect(page.locator("#alle-gestartet")).to_be_visible()
    expect(page.locator("#auswahl button[data-nr='9']")).to_contain_text("DNS")
    expect(page.locator("#dns-hinweis")).to_contain_text("1 Fahrer mit DNS")
    offline(ctx, False)
    warte_bis(lambda: 10 in gestartet(server), text="Nr. 10 gestartet")
