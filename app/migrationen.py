"""Versionierte, atomare Migrationen der Datenbank.

Der Stand steht in `PRAGMA user_version`; jeder Schritt läuft genau einmal in einer Transaktion
samt Änderung der Version. SQLite kann das auch für CREATE, ALTER und DROP, nur nimmt pysqlite solche
Befehle sonst aus der Transaktion heraus: deshalb eine eigene Verbindung mit explizitem BEGIN. Bricht
ein Schritt ab, ist die Datei wie vorher, und der nächste Start versucht es erneut.

Neue Schritte kommen ans Ende von SCHRITTE; bestehende werden nicht mehr geändert. Der Append-only-
Schutz der Ereignisse wird nach jeder Migration und bei jedem Start geprüft.
"""

import logging
from collections.abc import Callable

from sqlalchemy import Connection, Engine, create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.einstellungen import STANDARD_KLASSEN_DATEI, STANDARD_WERTUNGEN

log = logging.getLogger(__name__)

TRIGGER = {
    "ereignis_kein_update": "UPDATE",
    "ereignis_kein_delete": "DELETE",
}


class MigrationsFehler(RuntimeError):
    pass


def _trigger_anlegen(conn: Connection) -> None:
    for name, aktion in TRIGGER.items():
        conn.exec_driver_sql(
            f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {aktion} ON ereignis "
            "BEGIN SELECT RAISE(ABORT, 'ereignis ist append-only'); END"
        )


def _schema(conn: Connection, _klassen_datei: str, _wertungen: str) -> None:
    """Tabellen anlegen, ältere Dateien nachrüsten, verlorene Trigger wiederherstellen."""
    from app import db, models  # noqa: F401  (registriert Modelle an Basis.metadata)

    db.Basis.metadata.create_all(conn)
    db.schema_nachruesten(conn)
    _trigger_anlegen(conn)


def _daten(conn: Connection, klassen_datei: str, wertungen: str) -> None:
    """Standardsätze beim ersten Start, Sätze und Strecken älterer Veranstaltungen. Läuft nur noch
    einmal: bei jedem Start hätte es entfernte Segmente und Positionen zurückgeholt."""
    from app import saetze, strecken

    with Session(bind=conn, join_transaction_mode="create_savepoint") as session:
        saetze.standardsaetze_anlegen(session, klassen_datei, wertungen)
        saetze.veranstaltungen_zuordnen(session)
        strecken.migrieren(session)
        session.commit()


def _benutzer(conn: Connection, _klassen_datei: str, _wertungen: str) -> None:
    """Verein, Benutzer, Rollen, Protokoll der Admin-Aktionen. Alle Veranstaltungen
    gehören dem einen Verein (Name aus der Installation)."""
    from app import db, models

    db.Basis.metadata.create_all(conn)  # neue Tabellen
    db.schema_nachruesten(conn)  # neue Spalten (veranstaltung.verein_id, ereignis.benutzer)
    verein = conn.exec_driver_sql("SELECT MIN(id) FROM verein").scalar()
    if verein is None:
        name = conn.exec_driver_sql(
            "SELECT wert FROM app_einstellung WHERE schluessel = 'vereinsname'"
        ).scalar()
        verein = conn.exec_driver_sql(
            "INSERT INTO verein (name) VALUES (?)",
            ((name or "").strip() or models.STANDARD_VEREIN,),
        ).lastrowid
    conn.exec_driver_sql(
        "UPDATE veranstaltung SET verein_id = ? WHERE verein_id IS NULL", (verein,)
    )


SCHEMA = 1  # bis hier reicht tabellen_anlegen(): nur Tabellen, ohne Einstellungen
SCHRITTE: list[tuple[int, str, Callable[[Connection, str, str], None]]] = [
    (SCHEMA, "Tabellen und Spalten", _schema),
    (2, "Sätze und Strecken", _daten),
    (3, "Verein, Benutzer und Rollen", _benutzer),
]
AKTUELL = SCHRITTE[-1][0]


def version(conn: Connection) -> int:
    return conn.exec_driver_sql("PRAGMA user_version").scalar_one()


def _fehlende_trigger(conn: Connection) -> list[str]:
    vorhanden = {
        z[0] for z in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'trigger'")
    }
    return [t for t in TRIGGER if t not in vorhanden]


def _trigger_pruefen(conn: Connection) -> None:
    if fehlt := _fehlende_trigger(conn):
        raise MigrationsFehler(
            f"Der Schutz der Ereignisse fehlt in der Datenbank ({', '.join(fehlt)}). App startet "
            "nicht. Bitte die Datei prüfen oder eine Sicherung zurückspielen (Betriebshandbuch)."
        )


def _atomare_engine(engine: Engine) -> Engine:
    """Eigene Verbindung, die DDL wirklich in der Transaktion ausführt."""
    atomar = create_engine(
        engine.url, poolclass=NullPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(atomar, "connect")
    def _verbunden(dbapi_conn, _record):
        dbapi_conn.isolation_level = None  # BEGIN selbst, siehe unten
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    @event.listens_for(atomar, "begin")
    def _begin(conn):
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    return atomar


def migrieren(
    engine: Engine,
    klassen_datei: str = STANDARD_KLASSEN_DATEI,
    wertungen: str = STANDARD_WERTUNGEN,
    bis: int | None = None,
) -> None:
    """Bringt die Datenbank auf den aktuellen Stand; wirft MigrationsFehler, dann bleibt die Datei
    auf dem letzten vollständigen Schritt."""
    atomar = _atomare_engine(engine)
    try:
        for nr, name, schritt in SCHRITTE:
            if bis is not None and nr > bis:
                continue
            try:
                with atomar.begin() as conn:
                    if version(conn) >= nr:  # erneut unter der Sperre: ein zweiter Prozess
                        continue
                    schritt(conn, klassen_datei, wertungen)
                    conn.exec_driver_sql(f"PRAGMA user_version = {nr}")
                    _trigger_pruefen(conn)
            except MigrationsFehler:
                raise
            except Exception as e:
                raise MigrationsFehler(
                    f"Migration {nr} ({name}) abgebrochen, die Datenbank bleibt auf dem Stand davor. "
                    f"App startet nicht: {e}"
                ) from e
            log.info("Datenbank auf Stand %d: %s", nr, name)
        with atomar.connect() as conn:
            stand = version(conn)
            _trigger_pruefen(conn)
        if stand > AKTUELL:  # kein Abbruch: Ein Rollback auf diese Version muss starten können
            log.warning("Datenbank ist neuer (Stand %d) als diese Version (%d).", stand, AKTUELL)
    finally:
        atomar.dispose()
