"""S 3: Daten nicht verlieren: Import „ersetzen“, Gerätedatei mit Durchfahrten, unplausible Zeiten."""

import json
import uuid

import pytest
from sqlalchemy import select

from app.models import Ereignis, Messpunkt, Teilnehmer
from tests.api import test_admin
from tests.api.test_admin import KOPF, MIN, T0, geraet, post
from tests.api.test_geraetedatei import _datei, _eintrag, _ereignisse, _hochladen, _inhalt

admin, v, keine_sperre = test_admin.admin, test_admin.v, test_admin.keine_sperre  # Fixtures


def _entwurf(admin, v, zeilen):
    admin.post(f"/admin/v/{v.id}/import", data={"csrf": admin.csrf},
               files={"datei": ("t.csv", (KOPF + zeilen).encode())})  # fmt: skip


def _namen(admin):
    with admin.app.state.sessionmaker() as s:
        return {t.startnummer: t.vorname for t in s.scalars(select(Teilnehmer))}


def test_ersetzen_mit_zeiten_verweigert_auch_mit_bestaetigung(admin, v):
    geraet(admin, v, "START", startnummer=1, zeit_ms=T0)
    vorher = _namen(admin)
    _entwurf(admin, v, "1;Neu;Name;m;1990;\n")
    html = admin.get(f"/admin/v/{v.id}/import/vorschau?modus=ersetzen").text
    assert 'id="ersetzen-gesperrt"' in html and "schon Zeiten" in html
    assert 'value="ersetzen"  disabled' in html and 'value="ergaenzen" checked' in html
    r = post(admin, f"/admin/v/{v.id}/import/uebernehmen", modus="ersetzen", bestaetigung="1")
    assert "Ersetzen geht nicht mehr" in r.text
    assert _namen(admin) == vorher


def test_ersetzen_ohne_zeiten_geht_weiter(admin, v):
    _entwurf(admin, v, "1;Neu;Name;m;1990;\n")
    html = admin.get(f"/admin/v/{v.id}/import/vorschau?modus=ersetzen").text
    assert 'id="ersetzen-gesperrt"' not in html
    post(admin, f"/admin/v/{v.id}/import/uebernehmen", modus="ersetzen", bestaetigung="1")
    assert _namen(admin) == {1: "Neu"}


def test_aendern_per_startnummer_im_admin_mit_protokoll(admin, v):
    geraet(admin, v, "START", startnummer=1, zeit_ms=T0)
    anzahl = len(_namen(admin))
    admin.post(f"/admin/v/{v.id}/import", data={"csrf": admin.csrf}, files={"datei": ("t.csv", (
        b"startnummer;vorname;nachname;geschlecht;geburtsjahr;abzeichen\n"
        b"1;Korrigiert;Name;m;1990;ja\n99;Dazu;Name;w;1991;\n"))})  # fmt: skip
    html = admin.get(f"/admin/v/{v.id}/import/vorschau?modus=aendern").text
    assert 'value="aendern" checked' in html and "bereits vergeben" not in html
    r = post(admin, f"/admin/v/{v.id}/import/uebernehmen", modus="aendern")
    assert "2 Teilnehmer importiert" in r.text
    namen = _namen(admin)
    assert namen[1] == "Korrigiert" and namen[99] == "Dazu" and len(namen) == anzahl + 1
    with admin.app.state.sessionmaker() as s:
        assert [e.wert for e in s.scalars(select(Ereignis).where(Ereignis.art == "MERKMAL"))] == [
            "ABZEICHEN_JA"
        ]
        # die Zeit bleibt bei Startnummer 1
        assert s.scalars(select(Ereignis).where(Ereignis.art == "START")).one().startnummer == 1


# --- Gerätedatei ------------------------------------------------------------------------------


def _messpunkt(admin, v, name="Kreisel"):
    with admin.app.state.sessionmaker() as s:
        m = Messpunkt(veranstaltung_id=v.id, position=9, name=name, token=f"tok-{uuid.uuid4()}")
        s.add(m)
        s.commit()
        return m.id


def _uebernehmen(admin, v, inhalt):
    html = _hochladen(admin, v, inhalt).text
    return html, post(admin, f"/admin/v/{v.id}/geraetedatei/uebernehmen", inhalt=_inhalt(html))


def test_durchfahrten_eines_postens_werden_eingelesen(admin, v):
    mid = _messpunkt(admin, v)
    d = _eintrag("DURCHFAHRT", startnummer=1, wert=str(mid), geraet="posten-ab")
    k = _eintrag(
        "ZEITKORREKTUR",
        bezug_uuid=d["uuid"],
        delta_ms=1000,
        geraet="posten-ab",
        zeit_ms=None,
        erstellt_ms=T0 + 40 * MIN,
    )
    s = _eintrag("DURCHFAHRT", startnummer=2, wert=str(mid), geraet="posten-ab")
    r = _eintrag(
        "STORNO", bezug_uuid=s["uuid"], geraet="posten-ab", zeit_ms=None, erstellt_ms=T0 + 40 * MIN
    )
    html, _ = _uebernehmen(admin, v, _datei(v, [d, k, s, r]))
    assert (
        '<strong id="gd-neu">4</strong>' in html and '<strong id="gd-ungueltig">0</strong>' in html
    )
    alle = _ereignisse(admin)
    assert alle[d["uuid"]].art == "DURCHFAHRT" and alle[d["uuid"]].quelle == "datei"
    assert alle[d["uuid"]].zugang == "datei"
    # zweites Einlesen: Duplikate werden ignoriert
    html, _ = _uebernehmen(admin, v, _datei(v, [d, k, s, r]))
    assert (
        '<strong id="gd-vorhanden">4</strong>' in html and '<strong id="gd-neu">0</strong>' in html
    )
    assert len(_ereignisse(admin)) == len(alle)


def test_durchfahrt_an_fremdem_messpunkt_ist_ungueltig(admin, v):
    d = _eintrag("DURCHFAHRT", startnummer=1, wert="999999")
    html = _hochladen(admin, v, _datei(v, [d])).text
    assert '<strong id="gd-ungueltig">1</strong>' in html


def test_geraetedatei_hebt_endgueltigen_dsq_nicht_auf_und_nennt_den_grund(admin, v):
    from app import korrekturen as k

    with admin.app.state.sessionmaker() as s:
        k.status_setzen(s, s.get(type(v), v.id), 1, "DSQ", "Regel")
    e = _eintrag("STATUS", startnummer=1, wert="OK", zeit_ms=None, erstellt_ms=T0 + 40 * MIN)
    html = _hochladen(admin, v, _datei(v, [e])).text
    assert '<strong id="gd-ungueltig">1</strong>' in html and "endgültig disqualifiziert" in html


def test_geraetedatei_bei_offiziellem_ergebnis_erst_nach_dem_zuruecksetzen(admin, v):
    from app import korrekturen as k

    e = _eintrag("ZIEL", startnummer=1)
    with admin.app.state.sessionmaker() as s:
        k.ergebnis_offiziell(s, s.get(type(v), v.id), "fertig")
    html = _hochladen(admin, v, _datei(v, [e])).text
    r = post(admin, f"/admin/v/{v.id}/geraetedatei/uebernehmen", inhalt=_inhalt(html))
    assert "Das Ergebnis ist offiziell" in r.text and e["uuid"] not in _ereignisse(admin)
    with admin.app.state.sessionmaker() as s:
        k.ergebnis_vorlaeufig(s, s.get(type(v), v.id), "zurück")
    post(admin, f"/admin/v/{v.id}/geraetedatei/uebernehmen", inhalt=_inhalt(html))
    assert e["uuid"] in _ereignisse(admin)


def test_grosse_geraetedatei_wird_in_bloecken_gespeichert(admin, v):
    eintraege = [_eintrag("ZIEL", zeit_ms=T0 + i) for i in range(3000)]
    _uebernehmen(admin, v, _datei(v, eintraege))
    assert len(_ereignisse(admin)) == 3000


def test_datei_nahe_der_groessengrenze_wird_wie_im_browser_uebernommen(admin, v):
    """Das Formular der Vorschau sendet urlencoded, ohne die Feldgrenze von Multipart."""
    eintraege = [_eintrag("ZIEL", zeit_ms=T0 + i) for i in range(7000)]
    html, r = _uebernehmen(admin, v, _datei(v, eintraege))
    assert len(_inhalt(html)) > 1_100_000 and r.status_code == 200
    assert len(_ereignisse(admin)) == 7000


@pytest.mark.parametrize(
    "inhalt",
    ["{}".encode("utf-16"), b"[" * 200_000, b"\xff\xfe\x00"],
    ids=["utf16", "tief", "binaer"],
)
def test_kaputte_datei_ergibt_eine_meldung(admin, v, inhalt):
    r = _hochladen(admin, v, inhalt)
    assert r.status_code in (200, 303) and "Datei nicht lesbar" in admin.get(
        f"/admin/v/{v.id}/geraetedatei", follow_redirects=True
    ).text + r.text + r.headers.get("location", "")


def test_datei_mit_bom_laesst_sich_uebernehmen(admin, v):
    e = _eintrag("ZIEL", startnummer=1)
    daten = b"\xef\xbb\xbf" + json.dumps(_datei(v, [e])).encode()
    html = _hochladen(admin, v, daten).text
    post(admin, f"/admin/v/{v.id}/geraetedatei/uebernehmen", inhalt=_inhalt(html))
    assert e["uuid"] in _ereignisse(admin)


@pytest.mark.parametrize("nr", ["²", "99999999999999999999", "3000000000"])
def test_ungewoehnliche_startnummer_im_import_ist_ein_zellenfehler(admin, v, nr):
    _entwurf(admin, v, f"{nr};A;B;m;1990;\n")
    r = admin.get(f"/admin/v/{v.id}/import/vorschau")
    assert r.status_code == 200 and "ungültige Startnummer" in r.text
    r = post(admin, f"/admin/v/{v.id}/import/uebernehmen", modus="ergaenzen")
    assert r.status_code == 200 and "Import nicht möglich" in r.text


def test_hochgestelltes_geburtsjahr_ist_ein_zellenfehler(admin, v):
    _entwurf(admin, v, "77;A;B;m;²;\n")
    assert "ungültiges Geburtsjahr" in admin.get(f"/admin/v/{v.id}/import/vorschau").text


# --- Unplausible Zeiten -----------------------------------------------------------------------


@pytest.mark.parametrize("feld", ["zeit_ms", "erstellt_ms"])
@pytest.mark.parametrize("wert", [10**17, -1, 0, 946_684_799_999, 4_102_444_800_001])
def test_server_weist_unplausible_zeiten_ab(client, v, feld, wert):
    e = {"uuid": str(uuid.uuid4()), "art": "START", "startnummer": 1, "geraet": "g",
         "zeit_ms": T0, "erstellt_ms": T0, feld: wert}  # fmt: skip
    r = client.post("/api/ereignisse", json={"ereignisse": [e]},
                    headers={"Authorization": f"Bearer {v.token_start}"})  # fmt: skip
    assert r.status_code == 422 and "unplausibel" in r.text


@pytest.mark.parametrize("feld", ["zeit_ms", "erstellt_ms"])
def test_mehr_als_ein_tag_in_der_zukunft_wird_abgewiesen(client, v, feld):
    import time

    jetzt = int(time.time() * 1000)
    e = {"uuid": str(uuid.uuid4()), "art": "START", "startnummer": 1, "geraet": "g",
         "zeit_ms": jetzt, "erstellt_ms": jetzt, feld: jetzt + 2 * 86_400_000}  # fmt: skip
    kopf = {"Authorization": f"Bearer {v.token_start}"}
    r = client.post("/api/ereignisse", json={"ereignisse": [e]}, headers=kopf)
    assert r.status_code == 422 and "Uhr des Geräts" in r.json()["detail"]
    e |= {"uuid": str(uuid.uuid4()), feld: jetzt + 3_600_000}  # Uhr etwas falsch: bleibt gültig
    assert client.post("/api/ereignisse", json={"ereignisse": [e]}, headers=kopf).status_code == 200


def test_uebergrosse_startnummer_ergibt_422(client, v):
    e = {
        "uuid": "u",
        "art": "START",
        "startnummer": 10**30,
        "geraet": "g",
        "zeit_ms": T0,
        "erstellt_ms": T0,
    }
    r = client.post("/api/ereignisse", json={"ereignisse": [e]},
                    headers={"Authorization": f"Bearer {v.token_start}"})  # fmt: skip
    assert r.status_code == 422


@pytest.mark.parametrize("zeit", [10**17, -(10**15), 253_402_300_800_000])
def test_alle_ansichten_vertragen_altdaten_mit_unplausibler_zeit(admin, v, zeit):
    geraet(admin, v, "START", startnummer=2, zeit_ms=T0)
    geraet(admin, v, "ZIEL", startnummer=2, zeit_ms=T0 + 10 * MIN)
    with admin.app.state.sessionmaker() as s:  # am Server vorbei, wie vorhandene Altdaten
        for art, nr in (("START", 1), ("ZIEL", 1), ("ZIEL", None)):
            s.add(Ereignis(uuid=str(uuid.uuid4()), veranstaltung_id=v.id, art=art, startnummer=nr,
                           zeit_ms=zeit, geraet="alt", erstellt_ms=zeit, empfangen_ms=zeit))  # fmt: skip
        s.commit()
    for pfad in (
        f"/admin/v/{v.id}", f"/admin/v/{v.id}/protokoll", f"/admin/v/{v.id}/export/protokoll.csv",
        f"/admin/v/{v.id}/export/ergebnis.csv", f"/admin/v/{v.id}/aufgaben", f"/admin/v/{v.id}/teilnehmer",
        f"/admin/v/{v.id}/geraete", f"/admin/v/{v.id}/drucken",
        "/ergebnisse", "/ergebnisse/inhalt", "/ergebnisse/druck", "/ergebnisse.csv",
        "/api/oeffentlich", "/monitor",
    ):  # fmt: skip
        r = admin.get(pfad, follow_redirects=True)
        assert r.status_code == 200, pfad
    assert "unplausibel" in admin.get(f"/admin/v/{v.id}/protokoll").text
    assert (
        admin.get("/api/stand", headers={"Authorization": f"Bearer {v.token_ziel}"}).status_code
        == 200
    )
