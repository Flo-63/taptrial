"""Datenbankanbindung (SQLite, WAL-Modus)."""

import os
from datetime import date
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Basis(DeclarativeBase):
    pass


def erzeuge_engine(db_pfad: str) -> Engine:
    Path(db_pfad).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_pfad}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    return engine


def erzeuge_sessionmaker(engine: Engine) -> sessionmaker:
    return sessionmaker(engine, expire_on_commit=False)


# Spalten, die nach der ersten Version dazugekommen sind: (Tabelle, Spalte, SQL-Typ inkl. Default)
NACHGERUESTETE_SPALTEN = [
    ("veranstaltung", "token_start", "VARCHAR(64)"),
    ("veranstaltung", "token_ziel", "VARCHAR(64)"),
    ("veranstaltung", "zustand", "VARCHAR(20) NOT NULL DEFAULT 'laufend'"),
    ("veranstaltung", "anonymisieren_ab", "DATE"),
    ("teilnehmer", "klasse_fest", "VARCHAR(100)"),
    ("veranstaltung", "wertung", "VARCHAR(100)"),
    ("veranstaltung", "strecke_km", "FLOAT"),
    ("teilnehmer", "abzeichen", "BOOLEAN NOT NULL DEFAULT 0"),
    ("teilnehmer", "ausser_konkurrenz", "BOOLEAN NOT NULL DEFAULT 0"),
    ("veranstaltung", "klassensatz_id", "INTEGER REFERENCES klassensatz(id)"),
    ("veranstaltung", "wertungssatz_id", "INTEGER REFERENCES wertungssatz(id)"),
    ("veranstaltung", "regeln_json", "TEXT"),
    ("veranstaltung", "eingefroren_ms", "BIGINT"),
    ("veranstaltung", "einrichtung_schritt", "INTEGER"),
    ("veranstaltung", "letzter_import_ms", "BIGINT"),
    ("teilnehmer", "geaendert_ms", "BIGINT"),
    ("ereignis", "delta_ms", "INTEGER"),
    ("ereignis", "quelle", "VARCHAR(20)"),
    ("veranstaltung", "rennleitung_getrennt", "BOOLEAN NOT NULL DEFAULT 0"),
    ("veranstaltung", "token_rennleitung", "VARCHAR(64)"),
    ("strecke", "runden", "INTEGER NOT NULL DEFAULT 1"),
    ("strecke", "rundenlinie", "BOOLEAN NOT NULL DEFAULT 0"),
    ("veranstaltung", "zwischenzeiten_oeffentlich", "BOOLEAN NOT NULL DEFAULT 1"),
    ("veranstaltung", "mindestrundenzeit_s", "INTEGER NOT NULL DEFAULT 60"),
    ("strecke", "teile_json", "TEXT"),
    ("veranstaltung", "segmente_monitor", "BOOLEAN NOT NULL DEFAULT 0"),
    ("strecke", "abschnitte_json", "TEXT"),
    ("veranstaltung", "monitor_json", "TEXT"),
    ("strecke", "beschreibung", "TEXT"),
    ("strecke", "komoot_url", "VARCHAR(500)"),
    ("messpunkt", "art", "VARCHAR(10) NOT NULL DEFAULT 'punkt'"),
    ("messpunkt", "standard", "BOOLEAN NOT NULL DEFAULT 0"),
    ("messpunkt", "rennleitung_getrennt", "BOOLEAN NOT NULL DEFAULT 0"),
    ("messpunkt", "token_rennleitung", "VARCHAR(64)"),
    ("strecke", "startort_id", "INTEGER REFERENCES messpunkt(id)"),
    ("strecke", "zielort_id", "INTEGER REFERENCES messpunkt(id)"),
    ("ereignis", "ort_id", "INTEGER"),
    ("veranstaltung", "einrichtung_json", "TEXT"),
    ("messpunkt", "entfernt", "BOOLEAN NOT NULL DEFAULT 0"),
]


def _ort_id_aus_wert(conn) -> None:
    """Segmente 43: Bisher stand der Ort eines Start-/Zielgeräts in „wert“ (A144). Einmalig beim
    Nachrüsten der Spalte nach „ort_id“ übernehmen – nur wenn „wert“ die ID eines weiteren Orts
    gleicher Art derselben Veranstaltung ist. „wert“ bleibt unverändert; der append-only-Trigger
    wird dafür nur innerhalb dieser Transaktion entfernt (wie beim Löschen, A14)."""
    conn.exec_driver_sql("DROP TRIGGER IF EXISTS ereignis_kein_update")
    conn.exec_driver_sql(
        "UPDATE ereignis SET ort_id = (SELECT m.id FROM messpunkt m WHERE CAST(m.id AS TEXT) = "
        "ereignis.wert AND m.veranstaltung_id = ereignis.veranstaltung_id AND m.standard = 0 AND "
        "m.art = lower(ereignis.art)) WHERE art IN ('START', 'ZIEL') AND wert IS NOT NULL"
    )
    conn.exec_driver_sql(
        "CREATE TRIGGER IF NOT EXISTS ereignis_kein_update BEFORE UPDATE ON ereignis "
        "BEGIN SELECT RAISE(ABORT, 'ereignis ist append-only'); END"
    )


def _messpunkte_zur_veranstaltung(conn) -> None:
    """Segmente 12: Messpunkte gehören zur Veranstaltung statt zur Strecke. Ältere Tabellen
    (strecke_id NOT NULL) werden umgebaut; IDs und Tokens bleiben, strecke_id bleibt als Herkunft."""
    spalten = {z[1] for z in conn.exec_driver_sql("PRAGMA table_info(messpunkt)")}
    if not spalten or "veranstaltung_id" in spalten:
        return
    conn.exec_driver_sql(
        "CREATE TABLE messpunkt_neu (id INTEGER NOT NULL PRIMARY KEY, "
        "veranstaltung_id INTEGER NOT NULL REFERENCES veranstaltung(id), position INTEGER NOT NULL, "
        "name VARCHAR(100) NOT NULL, km FLOAT, abschnitt VARCHAR(10) NOT NULL DEFAULT '', "
        "token VARCHAR(64) NOT NULL UNIQUE, strecke_id INTEGER REFERENCES strecke(id))"
    )
    conn.exec_driver_sql(
        "INSERT INTO messpunkt_neu (id, veranstaltung_id, position, name, km, abschnitt, token, strecke_id) "
        "SELECT m.id, s.veranstaltung_id, m.position, m.name, m.km, m.abschnitt, m.token, m.strecke_id "
        "FROM messpunkt m JOIN strecke s ON s.id = m.strecke_id"
    )
    conn.exec_driver_sql("DROP TABLE messpunkt")
    conn.exec_driver_sql("ALTER TABLE messpunkt_neu RENAME TO messpunkt")
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_messpunkt_veranstaltung_id ON messpunkt (veranstaltung_id)"
    )


def tabellen_anlegen(engine: Engine) -> None:
    from app import models  # noqa: F401  (registriert Modelle an Basis.metadata)

    Basis.metadata.create_all(engine)
    schema_aktualisieren(engine)


def schema_aktualisieren(engine: Engine) -> None:
    """Rüstet ältere SQLite-Dateien nach (kein Migrationstool bei dieser Größe, PLAN Phase 2)."""
    from app.models import ANONYMISIEREN_NACH_MONATEN, neues_token, plus_monate

    with engine.begin() as conn:
        _messpunkte_zur_veranstaltung(conn)
        for tabelle, spalte, typ in NACHGERUESTETE_SPALTEN:
            vorhanden = {z[1] for z in conn.exec_driver_sql(f"PRAGMA table_info({tabelle})")}
            if spalte not in vorhanden:
                conn.exec_driver_sql(f"ALTER TABLE {tabelle} ADD COLUMN {spalte} {typ}")
                if (tabelle, spalte) == ("ereignis", "ort_id") and "art" in vorhanden:
                    _ort_id_aus_wert(conn)
        # Zuerst (vor der Token-Vergabe): genau eine aktive Veranstaltung (E7) auch in der
        # Datenbank erzwingen. Ältere Dateien mit mehreren aktiven: die zuletzt angelegte bleibt.
        conn.exec_driver_sql(
            "UPDATE veranstaltung SET aktiv = 0 WHERE aktiv = 1 AND id < "
            "(SELECT MAX(id) FROM veranstaltung WHERE aktiv = 1)"
        )
        conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_veranstaltung_eine_aktive "
            "ON veranstaltung (aktiv) WHERE aktiv = 1"
        )
        # Tokens für Veranstaltungen ohne Token: die aktive übernimmt die bisherigen
        # Umgebungs-Tokens (alte Gerätelinks bleiben gültig), alle anderen bekommen neue.
        zeilen = conn.exec_driver_sql(
            "SELECT id, aktiv, datum, token_start, token_ziel, anonymisieren_ab FROM veranstaltung"
        ).fetchall()
        for vid, aktiv, datum, t_start, t_ziel, anon in zeilen:
            if t_start is None or t_ziel is None:
                alt_start = os.environ.get("RCB_TOKEN_START") if aktiv else None
                alt_ziel = os.environ.get("RCB_TOKEN_ZIEL") if aktiv else None
                conn.exec_driver_sql(
                    "UPDATE veranstaltung SET token_start = ?, token_ziel = ? WHERE id = ?",
                    (
                        t_start or alt_start or neues_token(),
                        t_ziel or alt_ziel or neues_token(),
                        vid,
                    ),
                )
            if anon is None:
                frist = plus_monate(date.fromisoformat(str(datum)), ANONYMISIEREN_NACH_MONATEN)
                conn.exec_driver_sql(
                    "UPDATE veranstaltung SET anonymisieren_ab = ? WHERE id = ?",
                    (frist.isoformat(), vid),
                )
        for (vid,) in conn.exec_driver_sql(
            "SELECT id FROM veranstaltung WHERE token_rennleitung IS NULL"
        ).fetchall():
            conn.exec_driver_sql(
                "UPDATE veranstaltung SET token_rennleitung = ? WHERE id = ?", (neues_token(), vid)
            )
        for (mid,) in conn.exec_driver_sql(
            "SELECT id FROM messpunkt WHERE token_rennleitung IS NULL"
        ).fetchall():
            conn.exec_driver_sql(
                "UPDATE messpunkt SET token_rennleitung = ? WHERE id = ?", (neues_token(), mid)
            )
        for spalte in ("token_start", "token_ziel", "token_rennleitung"):
            conn.exec_driver_sql(
                f"CREATE UNIQUE INDEX IF NOT EXISTS ix_veranstaltung_{spalte} "
                f"ON veranstaltung ({spalte})"
            )
