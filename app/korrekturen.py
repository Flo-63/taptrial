"""Fachschicht des Admin.

Alle Korrekturen an Zeitdaten sind neue Ereignisse (append-only) mit Pflichtnotiz.
Stammdaten (Veranstaltung, Teilnehmer, Tokens, Lebenszyklus) sind normale Tabellenzeilen.
"""

import re
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from app import saetze, strecken
from app.auswertung import (
    Art,
    AuswertungsConfig,
    Ergebnis,
    Status,
    auswerten,
    gueltige_ereignisse,
    korrektur_text,
)
from app.klassen import GESCHLECHTER, KeineKlasseFehler, Klasse, klassifizieren
from app.models import (
    Ereignis,
    GeloeschterToken,
    GeraetUhr,
    ImportEntwurf,
    Messpunkt,
    Teilnehmer,
    Veranstaltung,
    aktive_veranstaltung,
    freigabe_text,
    neues_token,
    token_fingerabdruck,
)
from app.wertung import zehntel_km

ZEITZONE = ZoneInfo("Europe/Berlin")
GERAET_ADMIN = "admin"


class KorrekturFehler(ValueError):
    pass


def jetzt_ms() -> int:
    return time.time_ns() // 1_000_000


# --- Lesen -----------------------------------------------------------------


def ereignisse_von(session: Session, v: Veranstaltung) -> list[Ereignis]:
    return list(session.scalars(select(Ereignis).where(Ereignis.veranstaltung_id == v.id)))


def teilnehmer_von(session: Session, v: Veranstaltung) -> list[Teilnehmer]:
    return list(
        session.scalars(
            select(Teilnehmer)
            .where(Teilnehmer.veranstaltung_id == v.id)
            .order_by(Teilnehmer.startnummer)
        )
    )


def ergebnis(session: Session, v: Veranstaltung) -> Ergebnis:
    """Auswertung mit den Regeln der Veranstaltung (Klassensatz/Wertungssatz bzw. eingefroren)."""
    r = saetze.regeln(session, v)
    liste = strecken.auswertungs_strecken(session, v)  # leer: Altbestand mit einer Strecke
    if liste:
        mit_laenge = any(s.zehntel_km for s in liste)
    else:
        mit_laenge = bool(v.strecke_km)
    wertung = r.wertung if r.wertung and mit_laenge else None
    config = AuswertungsConfig(
        klassen=r.klassen,
        stichtag=v.stichtag_alter,
        wertung=wertung,
        strecke_zehntel_km=zehntel_km(v.strecke_km) if wertung and v.strecke_km else None,
        strecken=liste,
    )
    return auswerten(teilnehmer_von(session, v), ereignisse_von(session, v), config)


def klassen_von(session: Session, v: Veranstaltung) -> list[Klasse]:
    return saetze.regeln(session, v).klassen


def heute() -> date:
    return datetime.now(ZEITZONE).date()


def datum_text(ms: int | None) -> str:
    return "" if ms is None else f"{datetime.fromtimestamp(ms / 1000, ZEITZONE):%d.%m.%Y}"


def uhrzeit_text(ms: int | None) -> str:
    if ms is None:
        return "–"
    d = datetime.fromtimestamp(ms / 1000, ZEITZONE)
    return f"{d:%H:%M:%S},{d.microsecond // 100_000}"


@dataclass
class Stempel:
    uuid: str
    art: str  # START, ZIEL, MANUELLE_ZEIT
    zeit_ms: int
    startnummer: int | None  # effektive Nummer
    geraet: str
    korrektur_ms: int = 0  # Schnellkorrektur am Ziel (zeit_ms ist bereits korrigiert)

    @property
    def uhrzeit(self) -> str:
        return uhrzeit_text(self.zeit_ms)

    @property
    def beschriftung(self) -> str:
        nr = f"Nr. {self.startnummer}" if self.startnummer is not None else "ohne Nummer"
        art = {"START": "Start", "ZIEL": "Ziel", "MANUELLE_ZEIT": "manuell"}[self.art]
        korr = f" (korrigiert {korrektur_text(self.korrektur_ms)})" if self.korrektur_ms else ""
        return f"{self.uhrzeit}{korr} · {art} · {nr} · {self.geraet}"


def stempel(session: Session, v: Veranstaltung) -> list[Stempel]:
    """Gültige (nicht stornierte) Zeitstempel mit effektiver Startnummer, chronologisch."""
    erg = ergebnis(session, v)
    effektiv = {s.uuid: s for s in [*erg.startstempel, *erg.zielstempel]}
    liste = []
    for e in gueltige_ereignisse(ereignisse_von(session, v)):
        if e.art in (Art.START, Art.ZIEL):
            s = effektiv.get(e.uuid)
            liste.append(Stempel(e.uuid, e.art, s.zeit_ms if s else e.zeit_ms, s.startnummer if s else None,
                                 e.geraet, s.korrektur_ms if s else 0))  # fmt: skip
        elif e.art == Art.MANUELLE_ZEIT:
            liste.append(Stempel(e.uuid, e.art, e.zeit_ms, e.startnummer, e.geraet))
    return sorted(liste, key=lambda s: s.zeit_ms)


# --- Hilfen ----------------------------------------------------------------


def _schreibbar(v: Veranstaltung) -> None:
    if not v.laufend:
        raise KorrekturFehler(f"Veranstaltung ist {v.zustand} und schreibgeschützt.")


def _notiz(notiz: str | None) -> str:
    notiz = (notiz or "").strip()
    if not notiz:
        raise KorrekturFehler("Notiz ist Pflicht.")
    return notiz[:500]


def _startnummer_bekannt(session: Session, v: Veranstaltung, nr: int) -> None:
    if nr not in {t.startnummer for t in teilnehmer_von(session, v)}:
        raise KorrekturFehler(f"Startnummer {nr} ist nicht in der Teilnehmerliste.")


# Diese Ereignisse gibt es erst nach der Freigabe (Merkmal, Ergebnis-Erklärung nicht)
RENN_ARTEN = {Art.START, Art.ZIEL, Art.DURCHFAHRT, Art.STATUS, Art.RUNDE, Art.ZUORDNUNG, Art.STORNO,
              Art.ZEITKORREKTUR, Art.MANUELLE_ZEIT}  # fmt: skip


def freigabe_pruefen(v: Veranstaltung) -> None:
    if v.freigabe_fehlt:
        raise KorrekturFehler(freigabe_text(v))


def _admin_ereignis(session: Session, v: Veranstaltung, art: str, notiz: str, **felder) -> Ereignis:
    _schreibbar(v)
    if art in RENN_ARTEN:
        freigabe_pruefen(v)
    jetzt = jetzt_ms()
    # Eine Korrektur gilt für den Stand, den der Admin sieht: sie wird nach allen bekannten
    # Ereignissen einsortiert, auch wenn die Uhr eines Geräts vorgeht.
    letztes = session.scalar(
        select(func.max(Ereignis.erstellt_ms)).where(Ereignis.veranstaltung_id == v.id)
    )
    erstellt = max(jetzt, (letztes or 0) + 1)
    e = Ereignis(
        uuid=str(uuid.uuid4()),
        veranstaltung_id=v.id,
        art=art,
        geraet=GERAET_ADMIN,
        erstellt_ms=erstellt,
        empfangen_ms=jetzt,
        notiz=_notiz(notiz),
        **felder,
    )
    session.add(e)
    session.flush()
    saetze.einfrieren(session, v)  # ab dem ersten Ereignis gelten die Sätze als Kopie
    session.commit()
    return e


def _stempel_ereignis(session: Session, v: Veranstaltung, stempel_uuid: str) -> Ereignis:
    e = session.get(Ereignis, stempel_uuid)
    if (
        e is None
        or e.veranstaltung_id != v.id
        or e.art not in (Art.START, Art.ZIEL, Art.MANUELLE_ZEIT, Art.DURCHFAHRT)
    ):
        raise KorrekturFehler("Zeitstempel nicht gefunden.")
    return e


_UHRZEIT = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})(?:[,.](\d{1,3}))?$")
_DAUER = re.compile(r"^(?:(\d{1,2}):)?(\d{1,3}):(\d{2})(?:[,.](\d{1,3}))?$")


def _bruchteil_ms(text: str | None) -> int:
    return int((text or "0").ljust(3, "0")[:3])


def uhrzeit_zu_ms(tag: date, eingabe: str) -> int:
    """„hh:mm:ss,z“ am Veranstaltungstag (Europe/Berlin) → Epoch-ms."""
    m = _UHRZEIT.match((eingabe or "").strip())
    if not m:
        raise KorrekturFehler(f"Uhrzeit „{eingabe}“ ungültig (Format hh:mm:ss,z).")
    h, mi, s = int(m[1]), int(m[2]), int(m[3])
    if h > 23 or mi > 59 or s > 59:
        raise KorrekturFehler(f"Uhrzeit „{eingabe}“ ungültig.")
    d = datetime(tag.year, tag.month, tag.day, h, mi, s, tzinfo=ZEITZONE)
    return int(d.timestamp()) * 1000 + _bruchteil_ms(m[4])


def dauer_zu_ms(eingabe: str) -> int:
    """„[h:]mm:ss,z“ → Millisekunden."""
    m = _DAUER.match((eingabe or "").strip())
    if not m or int(m[3]) > 59:
        raise KorrekturFehler(f"Fahrzeit „{eingabe}“ ungültig (Format mm:ss,z oder h:mm:ss,z).")
    return ((int(m[1] or 0) * 60 + int(m[2])) * 60 + int(m[3])) * 1000 + _bruchteil_ms(m[4])


# --- Korrekturen (Ereignisse) ----------------------------------------------


def umbuchen(
    session: Session, v: Veranstaltung, stempel_uuid: str, neue_nr: int | None, notiz: str
):
    e = _stempel_ereignis(session, v, stempel_uuid)
    if e.art == Art.MANUELLE_ZEIT:
        raise KorrekturFehler("Manuelle Zeiten bitte stornieren und neu eintragen.")
    if neue_nr is not None:
        _startnummer_bekannt(session, v, neue_nr)
    return _admin_ereignis(session, v, Art.ZUORDNUNG, notiz, bezug_uuid=e.uuid, startnummer=neue_nr)


def tauschen(session, v, nr_a: int, nr_b: int, art: str, notiz: str):
    """Tauscht die Start- bzw. Zielstempel zweier Fahrer (zwei ZUORDNUNG-Ereignisse)."""
    if art not in ("START", "ZIEL") or nr_a == nr_b:
        raise KorrekturFehler("Zwei verschiedene Startnummern und Start oder Ziel wählen.")
    _notiz(notiz)
    erg = ergebnis(session, v)
    liste = erg.startstempel if art == "START" else erg.zielstempel
    gefunden = {}
    for nr in (nr_a, nr_b):
        treffer = [s for s in liste if s.startnummer == nr]
        if len(treffer) != 1:
            raise KorrekturFehler(
                f"Nr. {nr} hat {len(treffer)} gültige {art}-Stempel, genau 1 nötig."
            )
        gefunden[nr] = treffer[0]
    _admin_ereignis(
        session, v, Art.ZUORDNUNG, notiz, bezug_uuid=gefunden[nr_a].uuid, startnummer=nr_b
    )
    _admin_ereignis(
        session, v, Art.ZUORDNUNG, notiz, bezug_uuid=gefunden[nr_b].uuid, startnummer=nr_a
    )


def stornieren(session: Session, v: Veranstaltung, stempel_uuid: str, notiz: str):
    e = _stempel_ereignis(session, v, stempel_uuid)
    return _admin_ereignis(
        session, v, Art.STORNO, notiz, bezug_uuid=e.uuid, startnummer=e.startnummer
    )


def manuelle_zeit(session, v, nr: int, art: str, uhrzeit: str | None, fahrzeit: str | None,
                  notiz: str):  # fmt: skip
    """Manuelle Start- oder Zielzeit, als Uhrzeit oder (nur Ziel) als Fahrzeit ab Startzeit."""
    _schreibbar(v)
    if art not in ("START", "ZIEL"):
        raise KorrekturFehler("Start oder Ziel wählen.")
    _startnummer_bekannt(session, v, nr)
    if uhrzeit and uhrzeit.strip():
        zeit_ms = uhrzeit_zu_ms(v.datum, uhrzeit)
    elif fahrzeit and fahrzeit.strip() and art == "ZIEL":
        start = ergebnis(session, v).zeile(nr).start_ms
        if start is None:
            raise KorrekturFehler(f"Nr. {nr} hat keine eindeutige Startzeit; Uhrzeit angeben.")
        zeit_ms = start + dauer_zu_ms(fahrzeit)
    else:
        raise KorrekturFehler("Uhrzeit (oder bei Ziel eine Fahrzeit) angeben.")
    return _admin_ereignis(session, v, Art.MANUELLE_ZEIT, notiz, startnummer=nr, zeit_ms=zeit_ms,
                           wert=art, ort_id=_ort_der_zeit(session, v, nr, art))  # fmt: skip


def _ort_der_zeit(session: Session, v: Veranstaltung, nr: int, art: str) -> int | None:
    """Messpunkt einer manuellen Start-/Zielzeit: der des bisherigen Stempels dieses
    Fahrers, sonst Start bzw. Ziel seiner Strecke; None = Standard-Start/-Ziel."""
    erg = ergebnis(session, v)
    bisher = [s for s in (erg.startstempel if art == "START" else erg.zielstempel)
              if s.startnummer == nr]  # fmt: skip
    if bisher:
        return int(bisher[-1].ort) if bisher[-1].ort else None
    z = erg.zeile(nr)
    if z is None or z.strecke >= len(erg.strecken):
        return None
    ort = erg.strecken[z.strecke].startort if art == "START" else erg.strecken[z.strecke].zielort
    standard = strecken.standard_ids(session, v)
    return (
        int(ort.id)
        if ort and ort.id.isdigit() and int(ort.id) != standard.get(art.lower())
        else None
    )


def status_setzen(session: Session, v: Veranstaltung, nr: int, wert: str, notiz: str):
    _schreibbar(v)
    if wert not in ("DNS", "DNF", "DSQ", "OK"):
        raise KorrekturFehler("Status muss DNS, DNF, DSQ oder OK sein.")
    _startnummer_bekannt(session, v, nr)
    return _admin_ereignis(session, v, Art.STATUS, notiz, startnummer=nr, wert=wert)


def _offener_dsq(session, v, nr):
    z = ergebnis(session, v).zeile(nr)
    if z is None or z.dsq_vorlaeufig is None:
        raise KorrekturFehler(f"Nr. {nr} hat keinen offenen vorläufigen DSQ.")


def dsq_bestaetigen(session, v, nr: int, notiz: str):
    _offener_dsq(session, v, nr)
    return _admin_ereignis(session, v, Art.STATUS, notiz, startnummer=nr, wert="DSQ")


def dsq_verwerfen(session, v, nr: int, notiz: str):
    _offener_dsq(session, v, nr)
    return _admin_ereignis(session, v, Art.STATUS, notiz, startnummer=nr, wert="OK")


def auf_der_strecke(erg: Ergebnis) -> list:
    """Gestartet, ohne Zielzeit und ohne DNS/DNF/DSQ (wie die Kennzahl „auf der Strecke“)."""
    return [z for z in erg.zeilen if z.start_ms is not None and z.ziel_ms is None
            and z.status not in (Status.DNF, Status.DSQ, Status.DNS)]  # fmt: skip


def strecke_text(n: int) -> str:
    return f"Noch {n} Fahrer auf der Strecke"


def offiziell_sperrgruende(erg: Ergebnis) -> list[str]:
    gruende = []
    unterwegs = auf_der_strecke(erg)  # Erst, wenn alle Gestarteten durch sind
    if unterwegs:
        gruende.append(strecke_text(len(unterwegs)))
    if erg.vorlaeufige_dsq:
        nummern = ", ".join(str(z.startnummer) for z in erg.vorlaeufige_dsq)
        gruende.append(f"Offene vorläufige DSQ: Nr. {nummern}")
    if erg.blockierende_konflikte:
        gruende.append(f"{len(erg.blockierende_konflikte)} offene(r) Konflikt(e)")
    return gruende


def ergebnis_offiziell(session, v, notiz: str):
    gruende = offiziell_sperrgruende(ergebnis(session, v))
    if gruende:
        raise KorrekturFehler("Ergebnis kann nicht offiziell werden: " + "; ".join(gruende))
    return _admin_ereignis(session, v, Art.ERGEBNIS, notiz, wert="OFFIZIELL")


def ergebnis_vorlaeufig(session, v, notiz: str):
    return _admin_ereignis(session, v, Art.ERGEBNIS, notiz, wert="VORLAEUFIG")


# --- Veranstaltung, Tokens, Lebenszyklus -----------------------------------


def veranstaltung_anlegen(
    session: Session, name: str, datum: date, aktivieren: bool, klassensatz_id: int | None = None
) -> Veranstaltung:
    name = (name or "").strip()
    if not name:
        raise KorrekturFehler("Name fehlt.")
    if aktivieren and (laufend := laufende_andere(session)) is not None:
        raise KorrekturFehler(verdraengen_text(laufend))
    if klassensatz_id is None:
        klassensatz_id = saetze.standard_klassensatz(session).id
    v = Veranstaltung(name=name[:200], datum=datum, aktiv=False, klassensatz_id=klassensatz_id)
    session.add(v)
    session.flush()
    strecken.sicherstellen(session, v)  # eine Strecke mit allen Klassen
    session.commit()
    if aktivieren:
        aktiv_setzen(session, v)
    return v


def laufende_andere(session: Session, v: Veranstaltung | None = None) -> Veranstaltung | None:
    """Die aktive Veranstaltung, wenn sie läuft und Startzeiten hat (und nicht v ist)."""
    a = aktive_veranstaltung(session)
    if a is None or (v is not None and a.id == v.id) or not a.laufend:
        return None
    return a if startzeiten_vorhanden(session, a) else None


def verdraengen_text(a: Veranstaltung) -> str:
    return f"‚{a.name}‘ läuft (Startzeiten vorhanden). Erst abschließen."


def aktiv_sperrgrund(v: Veranstaltung) -> str:
    return f"Erst die Einrichtung abschließen (Schritt {v.einrichtung_schritt} von 5 ist offen)."


def aktiv_beenden(session: Session, v: Veranstaltung) -> None:
    """„Nicht mehr aktiv setzen“: nur, solange es keine Startzeit gibt; danach sind
    die Geräte wieder gesperrt. Eine abgeschlossene Veranstaltung darf immer zurücktreten
    (die Demo bleibt nicht aktiv)."""
    if not v.aktiv:
        raise KorrekturFehler("Die Veranstaltung ist nicht aktiv.")
    if v.laufend and startzeiten_vorhanden(session, v):
        raise KorrekturFehler("Es gibt bereits Startzeiten.")
    v.aktiv = False
    session.commit()


def startzeiten_vorhanden(session: Session, v: Veranstaltung) -> bool:
    return any(e.art == Art.START or (e.art == Art.MANUELLE_ZEIT and e.wert == "START")
               for e in gueltige_ereignisse(ereignisse_von(session, v)))  # fmt: skip


def aktiv_setzen(session: Session, v: Veranstaltung) -> None:
    """In einer Transaktion: alle anderen deaktivieren, dann diese aktivieren.

    Die Datenbank erzwingt zusätzlich höchstens eine aktive Veranstaltung (partieller Unique-Index).
    Erst nach abgeschlossener Einrichtung.
    """
    if v.einrichtung_schritt:
        raise KorrekturFehler(aktiv_sperrgrund(v))
    laufend = laufende_andere(session, v)
    if laufend is not None:  # Keine laufende Veranstaltung verdrängen
        raise KorrekturFehler(verdraengen_text(laufend))
    try:
        session.execute(update(Veranstaltung).where(Veranstaltung.id != v.id).values(aktiv=False))
        session.flush()
        v.aktiv = True
        session.commit()
    except Exception:
        session.rollback()
        raise


def stammdaten_speichern(session, v, name: str, datum: date, anonymisieren_ab: date) -> None:
    if v.zustand == "anonymisiert":
        raise KorrekturFehler("Veranstaltung ist anonymisiert.")
    if not (name or "").strip():
        raise KorrekturFehler("Name fehlt.")
    v.name = name.strip()[:200]
    if datum != v.datum:
        _schreibbar(v)
        v.datum = datum
        v.stichtag_alter = date(datum.year, 12, 31)
    v.anonymisieren_ab = anonymisieren_ab
    session.commit()


def token_erneuern(session: Session, v: Veranstaltung, rolle: str) -> str:
    """Start-, Ziel-, Rennleitungs- oder Posten-Token („posten-<Messpunkt-ID>“) erneuern; gibt die Bezeichnung zurück."""
    _schreibbar(v)
    if rolle.startswith("posten-"):
        mid = rolle.removeprefix("posten-")
        m = next((m for m in strecken.messpunkte_von(session, v) if str(m.id) == mid), None)
        if m is None:
            raise KorrekturFehler("Messpunkt nicht gefunden.")
        m.token = neues_token()
        session.commit()
        return f"Posten-Token „{m.name}“"
    art, _, oid = rolle.partition("-")
    if oid and art in ("start", "ziel", "rennleitung"):  # Weiterer Start-/Zielort
        m = next((m for m in strecken.orte_von(session, v) if str(m.id) == oid and not m.standard
                  and m.art == ("start" if art == "start" else "ziel")), None)  # fmt: skip
        if m is None:
            raise KorrekturFehler("Messpunkt nicht gefunden.")
        if art == "rennleitung":
            m.token_rennleitung = neues_token()
        else:
            m.token = neues_token()
        session.commit()
        return f"{art.capitalize()}-Token „{m.name}“"
    if rolle not in ("start", "ziel", "rennleitung"):
        raise KorrekturFehler("Nur Start-, Ziel-, Rennleitungs- und Posten-Token sind erneuerbar.")
    setattr(v, f"token_{rolle}", neues_token())
    session.commit()
    return f"{rolle.capitalize()}-Token"


def rennleitung_umschalten(session: Session, v: Veranstaltung, zusammen: bool,
                           bestaetigt: bool, ort: str = "") -> str:  # fmt: skip
    """Ziel und Rennleitung auf einem Gerät (zusammen) oder getrennte Links; je
    Zielort (ort = ID eines weiteren Zielorts, leer = Standard-Zielort)."""
    _schreibbar(v)
    ziel = v
    if ort:
        ziel = next((m for m in strecken.orte_von(session, v, "ziel") if str(m.id) == ort and not m.standard),
                    None)  # fmt: skip
        if ziel is None:
            raise KorrekturFehler("Ziel nicht gefunden.")
    if ziel.rennleitung_getrennt == (not zusammen):
        return "Keine Änderung."
    hat_zeiten = session.scalar(
        select(func.count()).select_from(Ereignis).where(Ereignis.veranstaltung_id == v.id)
    )
    if hat_zeiten and not bestaetigt:
        raise KorrekturFehler("Es gibt schon Zeiten: Nach dem Umschalten brauchen vorhandene "
                              "Geräte neue Links. Bitte bestätigen.")  # fmt: skip
    ziel.rennleitung_getrennt = not zusammen
    session.commit()
    if zusammen:
        return ("Ziel und Rennleitung wieder auf einem Gerät umschaltbar. "
                "Der Rennleitungs-Link gilt nicht mehr.")  # fmt: skip
    return ("Ziel und Rennleitung getrennt: Rennleitung bekommt einen eigenen Link, "
            "der Ziel-Link erfasst nur noch Einläufe.")  # fmt: skip


def abschliessen(session: Session, v: Veranstaltung) -> None:
    _schreibbar(v)
    v.zustand = "abgeschlossen"
    session.commit()


def anonymisieren(session: Session, v: Veranstaltung) -> None:
    klassen = klassen_von(session, v)
    if v.zustand != "abgeschlossen":
        raise KorrekturFehler("Nur abgeschlossene Veranstaltungen können anonymisiert werden.")
    for t in teilnehmer_von(session, v):
        if not t.klasse_fest:
            try:
                t.klasse_fest = klassifizieren(t, v.stichtag_alter, klassen)
            except KeineKlasseFehler:
                t.klasse_fest = "ohne Klasse"
        t.vorname, t.nachname, t.verein, t.geburtsjahr = "", "", None, 0
    v.zustand = "anonymisiert"
    session.commit()


def faellige(session: Session, heute: date) -> list[Veranstaltung]:
    """Veranstaltungen, deren Anonymisierungsfrist erreicht ist (nur Anzeige, nichts automatisch)."""
    return list(
        session.scalars(
            select(Veranstaltung)
            .where(Veranstaltung.zustand != "anonymisiert", Veranstaltung.anonymisieren_ab <= heute)
            .order_by(Veranstaltung.anonymisieren_ab)
        )
    )


def loeschen(session: Session, v: Veranstaltung) -> None:
    """Löscht Veranstaltung samt Teilnehmern, Ereignissen, Strecken und Import-Entwurf.

    Eine aktive oder laufende Veranstaltung wird dabei beendet (danach ist keine aktiv). Übrig
    bleiben nur die Fingerabdrücke der Gerätetokens (410 „Veranstaltung gelöscht“).

    Der append-only-Trigger wird dafür nur innerhalb dieser Transaktion entfernt und
    sofort wieder angelegt.
    """
    try:
        # Gerätetokens nur als Fingerabdruck merken: alte Geräte bekommen 410 und räumen auf
        posten = [t for m in strecken.orte_von(session, v) if not m.standard
                  for t in (m.token, m.token_rennleitung)]  # fmt: skip
        for token in (v.token_start, v.token_ziel, v.token_rennleitung, *posten):
            abdruck = token_fingerabdruck(token)
            if token and not session.scalar(
                select(GeloeschterToken.id).where(GeloeschterToken.fingerabdruck == abdruck)
            ):
                session.add(GeloeschterToken(fingerabdruck=abdruck))
        v.aktiv = False  # aktive/laufende Veranstaltung wird beendet
        session.flush()
        session.execute(text("DROP TRIGGER IF EXISTS ereignis_kein_delete"))
        session.execute(delete(Ereignis).where(Ereignis.veranstaltung_id == v.id))
        session.execute(delete(ImportEntwurf).where(ImportEntwurf.veranstaltung_id == v.id))
        session.execute(delete(GeraetUhr).where(GeraetUhr.veranstaltung_id == v.id))
        session.execute(delete(Teilnehmer).where(Teilnehmer.veranstaltung_id == v.id))
        for s in strecken.strecken_von(session, v):
            session.delete(s)
        session.flush()  # Strecken zuerst: sie verweisen auf ihren Start-/Zielort
        session.execute(delete(Messpunkt).where(Messpunkt.veranstaltung_id == v.id))
        session.delete(v)
        session.execute(
            text(
                "CREATE TRIGGER ereignis_kein_delete BEFORE DELETE ON ereignis "
                "BEGIN SELECT RAISE(ABORT, 'ereignis ist append-only'); END"
            )
        )
        session.commit()
    except Exception:
        session.rollback()
        raise


# --- Teilnehmer --------------------------------------------------------------


def _hat_ereignisse(session: Session, v: Veranstaltung, nr: int) -> bool:
    return (
        session.scalars(
            select(Ereignis.uuid).where(
                Ereignis.veranstaltung_id == v.id, Ereignis.startnummer == nr
            )
        ).first()
        is not None
    )


def teilnehmer_speichern(session, v, tid: int | None, daten: dict) -> Teilnehmer:
    klassen = klassen_von(session, v)
    _schreibbar(v)
    feld = {k: str(daten.get(k) or "").strip() for k in
            ("startnummer", "vorname", "nachname", "geschlecht", "geburtsjahr", "verein")}  # fmt: skip
    if not feld["startnummer"].isdigit() or int(feld["startnummer"]) <= 0:
        raise KorrekturFehler(f"Startnummer „{feld['startnummer']}“ ungültig.")
    nr = int(feld["startnummer"])
    for name in ("vorname", "nachname"):
        if not feld[name]:
            raise KorrekturFehler(f"{name.capitalize()} fehlt.")
    geschlecht = feld["geschlecht"].lower()
    if geschlecht not in GESCHLECHTER:
        raise KorrekturFehler("Geschlecht muss m, w oder d sein.")
    if not feld["geburtsjahr"].isdigit() or not 1900 <= int(feld["geburtsjahr"]) <= v.datum.year:
        raise KorrekturFehler(f"Geburtsjahr „{feld['geburtsjahr']}“ ungültig.")
    jahr = int(feld["geburtsjahr"])
    try:
        klassifizieren(SimpleNamespace(geschlecht=geschlecht, geburtsjahr=jahr), v.stichtag_alter,
                       klassen)  # fmt: skip
    except KeineKlasseFehler as e:
        raise KorrekturFehler(str(e).replace("keine", "Keine", 1)) from e

    t = session.get(Teilnehmer, tid) if tid else None
    if tid and (t is None or t.veranstaltung_id != v.id):
        raise KorrekturFehler("Teilnehmer nicht gefunden.")
    andere = {x.startnummer for x in teilnehmer_von(session, v) if t is None or x.id != t.id}
    if nr in andere:
        raise KorrekturFehler(f"Startnummer {nr} ist bereits vergeben.")
    if t is not None and nr != t.startnummer and _hat_ereignisse(session, v, t.startnummer):
        raise KorrekturFehler(
            f"Startnummer {t.startnummer} hat bereits Ereignisse und kann nicht geändert werden."
        )
    neu = t is None
    if neu:
        t = Teilnehmer(veranstaltung_id=v.id)
        session.add(t)
    t.startnummer, t.vorname, t.nachname = nr, feld["vorname"][:100], feld["nachname"][:100]
    t.geschlecht, t.geburtsjahr, t.verein = geschlecht, jahr, feld["verein"][:200] or None
    t.geaendert_ms = jetzt_ms()  # für die Warnung beim Import „ersetzen“
    geaendert = []
    for merkmal in MERKMAL_NAMEN:
        if merkmal in daten:  # Formular ohne Feld (z. B. Abzeichen bei Wertung aus): unverändert
            wert = bool(daten.get(merkmal))
            if not neu and bool(getattr(t, merkmal)) != wert:
                geaendert.append((merkmal, wert))
            setattr(t, merkmal, wert)
    session.commit()
    for merkmal, wert in geaendert:
        _merkmal_protokollieren(session, v, t, merkmal, wert)
    return t


MERKMAL_NAMEN = {"abzeichen": "Abzeichen", "ausser_konkurrenz": "Außer Konkurrenz"}


def _merkmal_protokollieren(session, v, t: Teilnehmer, merkmal: str, wert: bool) -> None:
    text = f"{MERKMAL_NAMEN[merkmal]}: {'ja' if wert else 'nein'} (Nr. {t.startnummer})"
    _admin_ereignis(session, v, Art.MERKMAL, text, startnummer=t.startnummer,
                    wert=f"{merkmal.upper()}_{'JA' if wert else 'NEIN'}")  # fmt: skip


def merkmal_setzen(session, v, tid: int, merkmal: str, wert: bool) -> Teilnehmer:
    """Abzeichen/außer Konkurrenz umschalten; jede Änderung als Ereignis im Protokoll."""
    _schreibbar(v)
    if merkmal not in MERKMAL_NAMEN:
        raise KorrekturFehler("Unbekanntes Merkmal.")
    t = session.get(Teilnehmer, tid)
    if t is None or t.veranstaltung_id != v.id:
        raise KorrekturFehler("Teilnehmer nicht gefunden.")
    if getattr(t, merkmal) != wert:
        setattr(t, merkmal, wert)
        t.geaendert_ms = jetzt_ms()
        _merkmal_protokollieren(session, v, t, merkmal, wert)  # committet auch die Änderung
    return t


def doppeltipp_werten(session: Session, v: Veranstaltung, uuid_: str, notiz: str):
    """Einen automatisch verworfenen Doppeltipp doch als Durchfahrt werten (Ereignis RUNDE „GILT“)."""
    erg = ergebnis(session, v)
    if erg.nicht_gezaehlt.get(uuid_) != "Doppeltipp":
        raise KorrekturFehler("Diese Durchfahrt ist kein verworfener Doppeltipp.")
    nr = next(d.startnummer for d in erg.durchfahrtstempel if d.uuid == uuid_)
    return _admin_ereignis(
        session, v, Art.RUNDE, _notiz(notiz), bezug_uuid=uuid_, wert="GILT", startnummer=nr
    )


def seit_import_geaendert(session: Session, v: Veranstaltung) -> int:
    """Teilnehmer, die seit dem letzten CSV-Import im Admin angelegt oder geändert wurden."""
    abfrage = (
        select(func.count())
        .select_from(Teilnehmer)
        .where(Teilnehmer.veranstaltung_id == v.id, Teilnehmer.geaendert_ms.is_not(None))
    )
    if v.letzter_import_ms is not None:
        abfrage = abfrage.where(Teilnehmer.geaendert_ms > v.letzter_import_ms)
    return session.scalar(abfrage) or 0


def teilnehmer_loeschen(session: Session, v: Veranstaltung, tid: int) -> None:
    _schreibbar(v)
    t = session.get(Teilnehmer, tid)
    if t is None or t.veranstaltung_id != v.id:
        raise KorrekturFehler("Teilnehmer nicht gefunden.")
    if _hat_ereignisse(session, v, t.startnummer):
        raise KorrekturFehler(
            f"Nr. {t.startnummer} hat bereits Ereignisse und kann nicht gelöscht werden."
        )
    session.delete(t)
    session.commit()
