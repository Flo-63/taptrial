"""Gerätedaten gehören zur Veranstaltung (und zum Token). Veranstaltung A mit Starts,
Zieleinlauf und Durchfahrt → A gelöscht, während die Geräte geschlossen sind → Veranstaltung B mit
denselben Startnummern → derselbe Browser öffnet die Links von B: alles beginnt leer; ein nicht
übertragener Eintrag aus A bleibt erhalten, wird gemeldet und nicht an B gesendet."""

import json
import secrets
import time
import uuid
from datetime import date
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import jetzt_synchronisieren, oeffnen, warte_bis
from tests.e2e.test_ziel import kachel


@pytest.fixture
def rundkurs():
    return "messpunkte"


def veranstaltung_b(db_pfad, anzahl):
    """B direkt in der Datenbank: aktiv, neue Tokens, dieselben Startnummern, Rundkurs Kreisel/Berg."""
    from app import saetze, segmente, strecken
    from app.db import erzeuge_engine, erzeuge_sessionmaker
    from app.models import Messpunkt, Teilnehmer, Veranstaltung
    from app.simulation import beispiel_teile

    engine = erzeuge_engine(str(db_pfad))
    with erzeuge_sessionmaker(engine)() as s:
        v = Veranstaltung(name="B-Zeitfahren", datum=date(2026, 10, 11), aktiv=True,
                          token_start=secrets.token_urlsafe(16), token_ziel=secrets.token_urlsafe(16),
                          klassensatz_id=saetze.standard_klassensatz(s).id)  # fmt: skip
        s.add(v)
        s.flush()
        mps = [Messpunkt(veranstaltung_id=v.id, position=i, name=n, token=secrets.token_urlsafe(16))
               for i, n in enumerate(("Kreisel", "Berg"))]  # fmt: skip
        s.add_all(mps)
        s.flush()
        strecken.sicherstellen(s, v)
        strecken.strecken_von(s, v)[-1].teile_json = segmente.nach_json(
            beispiel_teile({"P1": str(mps[0].id), "P2": str(mps[1].id)})
        )
        for nr in range(1, anzahl + 1):
            s.add(Teilnehmer(veranstaltung_id=v.id, startnummer=nr, vorname=f"Vorname{nr}",
                             nachname=f"Nachname{nr}", geschlecht="w" if nr % 2 else "m",
                             geburtsjahr=1960 + nr))  # fmt: skip
        s.commit()
        tokens = {"start": v.token_start, "ziel": v.token_ziel, "posten": mps[0].token}
    engine.dispose()
    return tokens


def senden_b(server, token, *ereignisse):
    r = httpx.post(f"{server.url}/api/ereignisse", json={"ereignisse": list(ereignisse)},
                   headers={"Authorization": f"Bearer {token}"})  # fmt: skip
    r.raise_for_status()


def ereignisse_b(server, token):
    r = httpx.get(f"{server.url}/api/stand", headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    return r.json()


def test_neue_veranstaltung_beginnt_leer_fruehere_eintraege_bleiben(
    browser, server, tmp_path, teilnehmer_anzahl
):
    ctx = browser.new_context(viewport={"width": 400, "height": 860}, accept_downloads=True)
    # --- Veranstaltung A: zwei Starts, ein Zieleinlauf, eine Durchfahrt, ein Start nicht übertragen
    st = oeffnen(ctx, server, "start")
    st.click("#btn-start")
    st.wait_for_timeout(1100)  # Entprellung am Start
    st.click("#btn-start")
    jetzt_synchronisieren(st)
    warte_bis(
        lambda: len([e for e in server.ereignisse() if e["art"] == "START"]) == 2,
        text="2 Starts in A",
    )
    expect(st.locator("#letzter-start")).to_contain_text("Zuletzt gestartet: Nr. 2")
    ctx.set_offline(True)
    st.wait_for_timeout(1100)
    st.click("#btn-start")  # Nr. 3 – bleibt auf dem Gerät
    expect(st.locator("#ausstehend")).to_have_text("1 ausstehend")
    st.close()
    ctx.set_offline(False)
    zi = oeffnen(ctx, server, "ziel")
    kachel(zi, 1).click()
    jetzt_synchronisieren(zi)
    warte_bis(lambda: any(e["art"] == "ZIEL" for e in server.ereignisse()), text="Ziel in A")
    expect(zi.locator("#letzter-text")).to_contain_text("Nr. 1")
    zi.close()
    po = oeffnen(ctx, server, "posten")
    kachel(po, 2).click()
    jetzt_synchronisieren(po)
    warte_bis(
        lambda: any(e["art"] == "DURCHFAHRT" for e in server.ereignisse()), text="Durchfahrt in A"
    )
    po.close()

    # --- A löschen (alle Geräte geschlossen: sie sehen die 410 nie), B anlegen
    assert "komplett gelöscht" in server.admin_post(
        "/admin/v/1/loeschen", bestaetigung="1", name="E2E-Zeitfahren"
    )
    b = veranstaltung_b(tmp_path / "daten" / "zeitfahren.sqlite3", teilnehmer_anzahl)

    # --- Start von B im selben Browser (Profil „start“): alle offen, „Zuletzt gestartet“ leer
    st = ctx.new_page()
    st.goto(f"{server.url}/start?token={b['start']}")
    st.wait_for_function("window.geraetBereit === true")
    jetzt_synchronisieren(st)
    expect(st.locator("#fortschritt")).to_have_text(f"Gestartet: 0 von {teilnehmer_anzahl}")
    expect(st.locator("#letzter-start")).to_have_text("")
    expect(st.locator("#ausstehend")).to_have_text("0 ausstehend")
    # der nicht übertragene Start aus A: gemeldet, als Datei zu sichern, nicht an B gesendet
    expect(st.locator("#frueher-text")).to_have_text(
        "1 Eintrag einer früheren Veranstaltung – als Datei sichern"
    )
    with st.expect_download() as dl:
        st.click("#btn-frueher-exportieren")
    datei = json.loads(Path(dl.value.path()).read_text(encoding="utf-8"))
    assert [(e["art"], e["startnummer"]) for e in datei["eintraege"]] == [("START", 3)]
    assert datei["veranstaltung"]["id"] == 1 and "bindung" not in datei["eintraege"][0]
    time.sleep(6)  # mindestens ein weiterer Sync-Durchlauf
    assert ereignisse_b(server, b["start"])["gestartet"] == []
    # ein Start in B geht ganz normal durch; der frühere bleibt getrennt
    st.click("#btn-start")
    jetzt_synchronisieren(st)
    warte_bis(
        lambda: ereignisse_b(server, b["start"])["gestartet"] == [1], text="Nr. 1 in B gestartet"
    )
    st.reload()
    st.wait_for_function("window.geraetBereit === true")
    expect(st.locator("#frueher-text")).to_have_text(
        "1 Eintrag einer früheren Veranstaltung – als Datei sichern"
    )
    expect(st.locator("#letzter-start")).to_contain_text("Zuletzt gestartet: Nr. 1")
    st.close()

    # --- Ziel und Posten von B: kein Zieleinlauf, keine Durchfahrt aus A
    t = int(time.time() * 1000) - 60_000
    senden_b(server, b["start"], {"uuid": str(uuid.uuid4()), "art": "START", "startnummer": 2, "zeit_ms": t,
                                  "erstellt_ms": t, "geraet": "start-api"})  # fmt: skip
    zi = ctx.new_page()
    zi.goto(f"{server.url}/ziel?token={b['ziel']}")
    zi.wait_for_function("window.geraetBereit === true")
    jetzt_synchronisieren(zi)
    expect(kachel(zi, 1)).to_be_visible()  # in B unterwegs, das Ziel aus A zählt nicht
    expect(zi.locator("#letzter-text")).to_have_text("Noch kein Einlauf erfasst")
    expect(zi.locator("#frueher-box")).to_be_hidden()
    zi.close()
    po = ctx.new_page()
    po.goto(f"{server.url}/posten?token={b['posten']}")
    po.wait_for_function("window.geraetBereit === true")
    jetzt_synchronisieren(po)
    expect(kachel(po, 2).locator(".kachel-runde")).to_have_text("1. von 3 Durchfahrten")
    expect(po.locator("#letzter-text")).to_have_text("Noch keine Durchfahrt erfasst")
    po.close()
    assert ereignisse_b(server, b["start"])["gestartet"] == [1, 2]
