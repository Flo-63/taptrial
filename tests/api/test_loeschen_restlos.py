"""Löschen einer Veranstaltung restlos – keine Zeile in irgendeiner Tabelle verweist
danach noch auf sie; eine zweite Veranstaltung bleibt unverändert."""

import json
import uuid
from datetime import date

from sqlalchemy import inspect, select, text

from app import korrekturen as k
from app import segmente as sg
from app import strecken as st
from app.models import Basis, ImportEntwurf, Messpunkt, Teilnehmer, Veranstaltung
from app.simulation import beispiel_teile
from tests.api import test_admin
from tests.api.test_admin import MIN, T0, korr, post
from tests.api.test_orte import orte_speichern

admin, v, keine_sperre = test_admin.admin, test_admin.v, test_admin.keine_sperre  # Fixtures

# Tabellen ohne Bezug zu einer Veranstaltung – bewusst nicht gelöscht, mit Grund
AUSNAHMEN = {
    "geloeschter_token": "Fingerabdrücke der Gerätetokens: alte Links bekommen 410 „gibt es nicht mehr“",
    "klassensatz": "Vorlage, gilt für alle Veranstaltungen (die Veranstaltung hat eine Kopie in regeln_json)",
    "klassensatz_klasse": "Teil der Vorlage Klassensatz",
    "wertungssatz": "Vorlage, gilt für alle Veranstaltungen",
    "wertungssatz_stufe": "Teil der Vorlage Wertungssatz",
    "app_einstellung": "globale Einstellungen der Installation (Vereinsname, Impressum, Datenschutz), "
    "ohne Bezug zu einer Veranstaltung",
}


def eigene_zeilen(conn, vid: int) -> dict[str, set]:
    """Je Tabelle die Primärschlüssel aller Zeilen, die (über Fremdschlüssel, auch mehrstufig, oder
    die Orts-ID der Ereignisse) zur Veranstaltung vid gehören."""
    tabellen = Basis.metadata.tables
    eigen: dict[str, set] = {"veranstaltung": {vid}}
    geaendert = True
    while geaendert:
        geaendert = False
        for name, t in tabellen.items():
            pk = next(iter(t.primary_key.columns)).name
            for fk in t.foreign_keys:
                eltern = fk.column.table.name
                if eltern not in eigen or not eigen[eltern]:
                    continue
                werte = ",".join(f"'{x}'" if isinstance(x, str) else str(x) for x in eigen[eltern])
                ids = {z[0] for z in conn.execute(text(
                    f"SELECT {pk} FROM {name} WHERE {fk.parent.name} IN ({werte})"))}  # fmt: skip
                if not ids <= eigen.setdefault(name, set()):
                    eigen[name] |= ids
                    geaendert = True
    return eigen


def ev(art, **kw):
    return {"uuid": str(uuid.uuid4()), "art": art, "geraet": "test", "erstellt_ms": T0, **kw}


def senden(admin, token, *ereignisse, geraet="test"):
    r = admin.post("/api/ereignisse", json={"ereignisse": list(ereignisse)},
                   headers={"Authorization": f"Bearer {token}", "X-Geraet": geraet,
                            "X-Uhr-Abweichung": "30"})  # fmt: skip
    assert r.status_code == 200, r.text


def alles_anlegen(admin, v):
    """Strecken, Messpunkte aller Arten, Klassen, Teilnehmer, alle Ereignisarten, Korrekturen,
    Geräte/Uhren, Monitor-Konfiguration, Abschnitte, Importentwurf."""
    orte_speichern(
        admin, v, ("Nord", "start"), ("Kreisel", "punkt"), ("Berg", "punkt"), ("Hof", "ziel")
    )
    with admin.app.state.sessionmaker() as s:
        w = s.get(Veranstaltung, v.id)
        o = {m.name: m for m in st.orte_von(s, w)}
        strecke = st.strecken_von(s, w)[0]
        strecke.teile_json = sg.nach_json(
            beispiel_teile({"P1": str(o["Kreisel"].id), "P2": str(o["Berg"].id)})
        )
        k_id = str(o["Kreisel"].id)
        strecke.abschnitte_json = json.dumps({"eigene": [{"id": "e1", "name": "Runde", "von": k_id, "nach": k_id,
                                                          "art": "wiederkehrend"}]})  # fmt: skip
        w.monitor_json = json.dumps({"seiten": [{"art": "ergebnis", "an": True}]})
        s.add(ImportEntwurf(veranstaltung_id=w.id, zeilen_json="[]", geaendert_ms=T0))
        s.commit()
        posten = o["Kreisel"].token
        tokens = [w.token_start, w.token_ziel, w.token_rennleitung, o["Nord"].token, o["Hof"].token]
    start = ev("START", startnummer=1, zeit_ms=T0)
    senden(
        admin, v.token_start, start, ev("START", startnummer=2, zeit_ms=T0 + MIN), geraet="start-1"
    )
    ziel = ev("ZIEL", startnummer=1, zeit_ms=T0 + 20 * MIN)
    offen = ev("ZIEL", zeit_ms=T0 + 21 * MIN)
    senden(admin, v.token_ziel, ziel, offen, ev("ZEITKORREKTUR", bezug_uuid=ziel["uuid"], delta_ms=1000),
           ev("ZUORDNUNG", bezug_uuid=offen["uuid"], startnummer=2), geraet="ziel-1")  # fmt: skip
    d = ev("DURCHFAHRT", startnummer=1, zeit_ms=T0 + 5 * MIN, wert=str(o["Kreisel"].id))
    senden(admin, posten, d, geraet="posten-1")
    senden(
        admin,
        "tok-admin",
        ev("RUNDE", bezug_uuid=d["uuid"], wert="GILT", veranstaltung_id=v.id, notiz="gilt"),
    )
    senden(admin, v.token_start, ev("STATUS", startnummer=3, wert="DNS"))
    korr(admin, v, "manuell", startnummer="3", art="START", uhrzeit="10:00:00,0", notiz="Hand")
    korr(admin, v, "stornieren", stempel=start["uuid"], notiz="Fehlstart")
    korr(admin, v, "status", startnummer="3", wert="DNF", notiz="Sturz")
    with admin.app.state.sessionmaker() as s:
        t = s.scalars(select(Teilnehmer).where(Teilnehmer.veranstaltung_id == v.id)).first()
    post(
        admin, f"/admin/v/{v.id}/teilnehmer/{t.id}/merkmal", merkmal="ausser_konkurrenz", wert="ja"
    )
    return tokens + [posten]


def zweite(admin):
    with admin.app.state.sessionmaker() as s:
        w = k.veranstaltung_anlegen(s, "Zweite", date(2026, 10, 11), aktivieren=False)
        for nr in (1, 2):
            s.add(Teilnehmer(veranstaltung_id=w.id, startnummer=nr, vorname=f"V{nr}", nachname="Z",
                             geschlecht="m", geburtsjahr=1980))  # fmt: skip
        s.add(Messpunkt(veranstaltung_id=w.id, position=1, name="Kreisel", art="punkt"))
        s.commit()
        k.aktiv_setzen(s, w)
        wid = w.id
    return wid


def stand(admin, wid):
    with admin.app.state.sessionmaker() as s:
        w = s.get(Veranstaltung, wid)
        erg = k.ergebnis(s, w)
        return ([(z.startnummer, z.start_ms, z.ziel_ms, z.fahrzeit_ms, z.status) for z in erg.zeilen],
                [(m.name, m.art, m.token) for m in st.orte_von(s, w)], w.token_start, w.name)  # fmt: skip


def test_loeschen_restlos(admin, v):
    # zweite Veranstaltung mit eigenen Zeiten (zuerst: eine laufende verdrängt man nicht)
    wid = zweite(admin)
    with admin.app.state.sessionmaker() as s:
        w = s.get(Veranstaltung, wid)
        t_start, t_ziel = w.token_start, w.token_ziel
    senden(admin, t_start, ev("START", startnummer=1, zeit_ms=T0))
    senden(admin, t_ziel, ev("ZIEL", startnummer=1, zeit_ms=T0 + 30 * MIN))
    with admin.app.state.sessionmaker() as s:
        s.get(Veranstaltung, wid).zustand = "abgeschlossen"
        s.commit()
        k.aktiv_setzen(s, s.get(Veranstaltung, v.id))
    vorher = stand(admin, wid)
    tokens = alles_anlegen(admin, v)
    engine = admin.app.state.sessionmaker.kw["bind"]
    with engine.connect() as conn:
        eigen = eigene_zeilen(conn, v.id)
    tabellen = set(inspect(engine).get_table_names())
    assert tabellen == set(Basis.metadata.tables), "Tabelle außerhalb des Modells"
    ungeprueft = tabellen - set(AUSNAHMEN) - {n for n, ids in eigen.items() if ids}
    assert not ungeprueft, (
        f"neue Tabelle ohne Prüfung (Testdaten ergänzen oder Ausnahme begründen): {ungeprueft}"
    )
    assert all(eigen.get(n) for n in ("ereignis", "teilnehmer", "strecke", "strecke_klasse", "messpunkt",
                                      "geraet_uhr", "import_entwurf"))  # fmt: skip
    with engine.connect() as conn:
        arten = {
            z[0]
            for z in conn.execute(text(f"SELECT art FROM ereignis WHERE veranstaltung_id = {v.id}"))
        }
    assert arten == {"START", "ZIEL", "ZEITKORREKTUR", "ZUORDNUNG", "DURCHFAHRT", "RUNDE", "STATUS",
                     "MANUELLE_ZEIT", "STORNO", "MERKMAL"}  # fmt: skip
    ortsids = {m for m in eigen["messpunkt"]}

    # Löschdialog nennt die Sicherungen
    status = admin.get(f"/admin/v/{v.id}/status").text
    assert "Vorher angelegte Sicherungen enthalten die Veranstaltung weiterhin." in status
    r = post(admin, f"/admin/v/{v.id}/loeschen", bestaetigung="1", name="Kottenforst")
    assert "komplett gelöscht" in r.text

    with engine.connect() as conn:
        for name, ids in eigen.items():
            if not ids:
                continue
            pk = next(iter(Basis.metadata.tables[name].primary_key.columns)).name
            werte = ",".join(f"'{x}'" if isinstance(x, str) else str(x) for x in ids)
            rest = conn.execute(
                text(f"SELECT COUNT(*) FROM {name} WHERE {pk} IN ({werte})")
            ).scalar()
            assert rest == 0, name
        for (
            name,
            t,
        ) in Basis.metadata.tables.items():  # kein Verweis über veranstaltung_id oder Orts-ID
            if "veranstaltung_id" in t.c:
                assert (
                    conn.execute(
                        text(f"SELECT COUNT(*) FROM {name} WHERE veranstaltung_id = {v.id}")
                    ).scalar()
                    == 0
                )
        werte = ",".join(str(x) for x in ortsids)
        assert (
            conn.execute(text(f"SELECT COUNT(*) FROM ereignis WHERE ort_id IN ({werte})")).scalar()
            == 0
        )
        # append-only gilt weiter
        trigger = {
            z[0] for z in conn.execute(text("SELECT name FROM sqlite_master WHERE type='trigger'"))
        }
        assert {"ereignis_kein_update", "ereignis_kein_delete"} <= trigger
    # Gerätelinks: „gibt es nicht mehr“ statt Fehler
    for token in tokens:
        r = admin.get("/api/stand", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 410 and r.json()["detail"] == "Veranstaltung gelöscht"
    # die zweite Veranstaltung ist unverändert
    assert stand(admin, wid) == vorher
