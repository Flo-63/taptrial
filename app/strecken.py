"""Mehrere Strecken je Veranstaltung (Phase 13, KONZEPT 3/6).

- Eine Veranstaltung hat 1..n Strecken (Name, gemessene Länge in km). Jede Klasse fährt genau
  eine Strecke; zugeordnet werden ganze Klassen, nicht einzelne Fahrer.
- Längen-Plätze aus dem Wertungssatz (Art „gesamtzeit“): je verschiedener `strecke_km` ein Platz,
  sonst einer für alle Klassen. Strecken entstehen nie still (Segmente 49): im Assistenten je Platz
  „aus Datei laden“ oder „Platzhalter anlegen“; ein Satzwechsel lässt die Strecken stehen.
- Die Zuordnung friert mit der ersten Zeit ein (wie der Klassensatz); Name und Länge bleiben
  änderbar, die Länge bis „Ergebnis offiziell“.
- Veranstaltungen ohne Strecken in der Datenbank (Altbestand, direkt angelegte Datensätze)
  werten wie bisher mit einer Strecke aus `veranstaltung.strecke_km`.
"""

import json
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import segmente as sg
from app.auswertung import MesspunktDef, StreckenDef
from app.klassen import OFFENE_KLASSE, Klasse
from app.models import Messpunkt, Strecke, StreckeKlasse, Veranstaltung
from app.saetze import SatzFehler, regeln, strecke_parsen
from app.wertung import Wertung, zehntel_km

FARBEN = 6  # Kennzeichen-Farben strecke-farbe-0 … 5 (zusätzlich immer der Name als Text)


class StreckenFehler(SatzFehler):
    """Prüffehler; bereich (Segmente 27): Reiter des Strecken-Editors, z. B. „verlauf“."""

    def __init__(self, text: str, bereich: str | None = None):
        super().__init__(text)
        self.bereich = bereich


def _im_bereich(bereich: str, schritt, *args):
    """Führt schritt(*args) aus und ordnet einen Prüffehler dem Reiter bereich zu."""
    try:
        return schritt(*args)
    except SatzFehler as e:
        if getattr(e, "bereich", None):
            raise
        raise StreckenFehler(str(e), bereich) from e


@dataclass
class Vorschlag:
    name: str
    laenge_km: float | None
    klassen: list[str]


def km_text(km: float | None) -> str:
    """20.0 → „20,0“ (Anzeige der gemessenen Länge)."""
    return "" if km is None else f"{km:.1f}".replace(".", ",")


def zuordnungsklassen(klassen: list[Klasse]) -> list[str]:
    """Alle Klassen, die eine Strecke brauchen – inkl. „Offen“ für d, falls der Satz keine d-Klasse hat."""
    namen = [k.name for k in klassen]
    if not any(k.geschlecht == "d" for k in klassen):
        namen.append(OFFENE_KLASSE)
    return namen


def vorschlag(klassen: list[Klasse], wertung: Wertung | None) -> list[Vorschlag]:
    """Strecken aus dem Wertungssatz: je verschiedener Länge eine, aufsteigend sortiert.

    Klassen ohne Längenangabe (kein Wertungseintrag, „Offen“) fahren die längste Strecke.
    Ohne Längen (pro_km, keine Wertung): eine Strecke für alle Klassen.
    """
    namen = zuordnungsklassen(klassen)
    laengen: dict[str, int] = {}
    if wertung is not None and wertung.art == "gesamtzeit":
        laengen = {kl: st.strecke_zehntel_km for kl, st in wertung.stufen.items()
                   if st.strecke_zehntel_km and kl in namen}  # fmt: skip
    if not laengen:
        return [Vorschlag("Strecke 1", None, namen)]
    stufen = sorted(set(laengen.values()))
    liste = [Vorschlag(f"{z / 10:g} km".replace(".", ","), z / 10, []) for z in stufen]
    for kl in namen:
        liste[stufen.index(laengen[kl]) if kl in laengen else -1].klassen.append(kl)
    return liste


# --- Lesen --------------------------------------------------------------------------------


def strecken_von(session: Session, v: Veranstaltung) -> list[Strecke]:
    return list(session.scalars(
        select(Strecke).where(Strecke.veranstaltung_id == v.id).order_by(Strecke.position)
    ))  # fmt: skip


def zuordnung(strecken: list[Strecke]) -> dict[str, int]:
    """Klasse → Index der Strecke."""
    return {sk.klasse: i for i, s in enumerate(strecken) for sk in s.klassen}


def messpunkte_von(session: Session, v: Veranstaltung) -> list[Messpunkt]:
    """Messpunkte der Veranstaltung (Segmente 12: gemeinsam für alle Strecken), ohne Start-/Zielorte."""
    return list(session.scalars(
        select(Messpunkt).where(Messpunkt.veranstaltung_id == v.id, Messpunkt.art == "punkt")
        .order_by(Messpunkt.position, Messpunkt.id)
    ))  # fmt: skip


# --- Orte (Segmente 37): Startorte, Messpunkte, Zielorte ------------------------------------

ORTSARTEN = {"start": "Start", "punkt": "Zwischenzeit", "ziel": "Ziel"}  # Segmente 42
_ART_REIHE = {"start": 0, "punkt": 1, "ziel": 2}


def orte_sicherstellen(session: Session, v: Veranstaltung) -> None:
    """Je Veranstaltung ein Standard-Startort „Start“ und ein Standard-Zielort „Ziel“ (ohne commit).
    Ihre Geräte nutzen die Tokens der Veranstaltung, bisherige Links bleiben gültig."""
    if v.id is None:
        session.flush()
    da = set(session.scalars(select(Messpunkt.art).where(
        Messpunkt.veranstaltung_id == v.id, Messpunkt.standard.is_(True))))  # fmt: skip
    for art, name in (("start", "Start"), ("ziel", "Ziel")):
        if art not in da:
            session.add(
                Messpunkt(veranstaltung_id=v.id, position=0, name=name, art=art, standard=True)
            )
    if len(da) < 2:
        session.flush()


def orte_von(session: Session, v: Veranstaltung, art: str | None = None,
            entfernte: bool = False) -> list[Messpunkt]:  # fmt: skip
    """Alle Orte der Veranstaltung: Startorte, Messpunkte, Zielorte (je Art Standard zuerst).

    Liest nur: fehlt ein Standardort (noch nicht gespeichert), steht ein ungespeicherter da.
    Entfernte Standardorte (Segmente 65) nur mit entfernte=True.
    """
    liste = list(session.scalars(select(Messpunkt).where(Messpunkt.veranstaltung_id == v.id)))
    for a, name in (("start", "Start"), ("ziel", "Ziel")):
        if not any(m.art == a and m.standard for m in liste):
            liste.append(
                Messpunkt(veranstaltung_id=v.id, position=0, name=name, art=a, standard=True)
            )
    liste = sorted(
        liste, key=lambda m: (_ART_REIHE.get(m.art, 1), not m.standard, m.position, m.id or 0)
    )
    return [m for m in liste if (art is None or m.art == art) and (entfernte or not m.entfernt)]


def standard_ort(session: Session, v: Veranstaltung, art: str) -> Messpunkt:
    """Standard-Start/-Ziel (auch entfernt: der bisherige Gerätelink bleibt gültig)."""
    return next(m for m in orte_von(session, v, art, entfernte=True) if m.standard)


def standard_ids(session: Session, v: Veranstaltung) -> dict[str, int | None]:
    """Art → ID des Standardorts (auch entfernt); für „ort_id leer = Standard“."""
    return {m.art: m.id for m in orte_von(session, v, entfernte=True) if m.standard}


def neue_strecke_orte(session: Session, v: Veranstaltung, s: Strecke) -> None:
    """Segmente 65: Eine neue Strecke beginnt am ersten Start und endet am ersten Ziel. Ist der
    Standard entfernt, ist das der erste übrige; gibt es keinen, kommt der Standard zurück."""
    with session.no_autoflush:
        _neue_strecke_orte(session, v, s)


def _neue_strecke_orte(session: Session, v: Veranstaltung, s: Strecke) -> None:
    for art, feld in (("start", "startort_id"), ("ziel", "zielort_id")):
        if getattr(s, feld) is not None:
            continue
        std = next((m for m in orte_von(session, v, art, entfernte=True) if m.standard), None)
        if std is None or not std.entfernt:
            continue
        uebrige = orte_von(session, v, art)
        if uebrige:
            setattr(s, feld, uebrige[0].id)
        else:
            std.entfernt = False


def startort_von(s: Strecke, orte: list[Messpunkt]) -> Messpunkt:
    """Startort der Strecke (ohne Angabe: Standard-Startort)."""
    return _ort_von(s.startort_id, "start", orte)


def zielort_von(s: Strecke, orte: list[Messpunkt]) -> Messpunkt:
    return _ort_von(s.zielort_id, "ziel", orte)


def _ort_von(oid: int | None, art: str, orte: list[Messpunkt]) -> Messpunkt:
    passend = [m for m in orte if m.art == art]
    return (next((m for m in passend if m.id == oid), None)
            or next((m for m in passend if m.standard), None) or passend[0])  # fmt: skip


def ort_bezeichnung(m: Messpunkt) -> str:
    """„Start“ bzw. „Start Nord“ für Gerätekarten und Meldungen (Segmente 38)."""
    wort = {"start": "Start", "ziel": "Ziel", "punkt": "Posten"}[m.art]
    return wort if m.name.lower() == wort.lower() else f"{wort} {m.name}"


def messpunkt_titel(m: Messpunkt) -> str:
    """„Start“, „Start Nord“, „Zwischenzeit Kreisel“, „Ziel Hof“ (Segmente 42: Gruppen unter „Geräte“)."""
    wort = ORTSARTEN[m.art]
    return wort if m.name.lower() == wort.lower() else f"{wort} {m.name}"


def orte_der_strecken(session: Session, v: Veranstaltung) -> list[tuple[Messpunkt, Messpunkt]]:
    """(Startort, Zielort) je Strecke in Streckenreihenfolge."""
    orte = orte_von(session, v)
    return [(startort_von(s, orte), zielort_von(s, orte)) for s in strecken_von(session, v)]


def strecken_am_ort(session: Session, v: Veranstaltung, ort: Messpunkt) -> list[int]:
    """Indizes der Strecken, die an diesem Startort beginnen bzw. an diesem Zielort enden."""
    paare = orte_der_strecken(session, v)
    if ort.art == "start":
        return [i for i, (a, _) in enumerate(paare) if a.id == ort.id]
    if ort.art == "ziel":
        return [i for i, (_, b) in enumerate(paare) if b.id == ort.id]
    return nutzende_strecken(strecken_von(session, v), ort.id)


def genutzt(session: Session, v: Veranstaltung, m: Messpunkt) -> bool:
    """Segmente 70: Nutzt eine Strecke diesen Messpunkt (Start, Zwischenzeit oder Ziel)? Nur dann
    hat er einen Gerätelink."""
    return bool(strecken_am_ort(session, v, m))


def unbenutzte_standards_ausblenden(session: Session, v: Veranstaltung) -> None:
    """Segmente 70: Standard-Start/-Ziel, den keine Strecke nutzt, wird ausgeblendet wie beim
    Entfernen (Segmente 65) – solange es keine Zeiten gibt. Ohne commit."""
    if v.regeln_json or not strecken_von(session, v):
        return
    session.flush()
    for m in orte_von(session, v):
        if m.standard and m.art != "punkt" and m.id is not None and not genutzt(session, v, m):
            m.entfernt = True
    session.flush()


def nutzende_strecken(liste: list[Strecke], messpunkt_id) -> list[int]:
    """Indizes der Strecken, deren Segmente am Messpunkt vorbeiführen."""
    return [i for i, s in enumerate(liste) if str(messpunkt_id) in sg.innere_punkte(teile_von(s))]


def auswertungs_strecken(session: Session, v: Veranstaltung) -> tuple[StreckenDef, ...]:
    """Strecken für die Auswertung; leer, wenn die Veranstaltung (noch) keine Strecken hat."""
    mps = messpunkte_von(session, v)
    orte = orte_von(session, v)
    liste = []
    for s in strecken_von(session, v):
        teile = sg.aus_json(s.teile_json)
        genutzt = set(sg.innere_punkte(teile))
        start, ziel = startort_von(s, orte), zielort_von(s, orte)
        liste.append(StreckenDef(s.name, zehntel_km(s.laenge_km) if s.laenge_km else None,
                                 frozenset(sk.klasse for sk in s.klassen),
                                 abschnitte=tuple(abschnitte_von(s)),
                                 messpunkte=tuple(MesspunktDef(str(m.id), m.name) for m in mps if str(m.id) in genutzt),
                                 teile=teile, startort=MesspunktDef(str(start.id), start.name),
                                 zielort=MesspunktDef(str(ziel.id), ziel.name)))  # fmt: skip
    return tuple(liste)


def teile_von(s: Strecke) -> tuple:
    return sg.aus_json(s.teile_json)


def abschnitte_von(s: Strecke) -> list:
    """Auswertungsabschnitte der Strecke (Segment-Abschnitte und eigene, Segmente 13)."""
    import json

    return sg.abschnitte(teile_von(s), json.loads(s.abschnitte_json) if s.abschnitte_json else None)


def abschnitt_hinweise(s: Strecke) -> list[str]:
    """Hinweise zu Rundenblöcken, die ein eigener Abschnitt schon abdeckt (Segmente 32)."""
    import json

    return sg.abschnitt_hinweise(
        teile_von(s), json.loads(s.abschnitte_json) if s.abschnitte_json else None
    )


def messpunkt_fuer_token(session: Session, token: str):
    """(Ort, Veranstaltung, Rolle) zu einem Orts-Token oder None (zeitkonstant verglichen).

    Messpunkt → „posten“, Startort → „start“, Zielort → „ziel“ bzw. mit eigenem Rennleitungs-Link
    „rennleitung“ (Segmente 38). Standardorte nutzen die Tokens der Veranstaltung, nicht diese.
    """
    import secrets

    def gleich(a: str | None) -> bool:
        return a is not None and secrets.compare_digest(token.encode(), a.encode())

    for m in session.scalars(select(Messpunkt)):
        if m.standard:
            continue
        rolle = None
        if gleich(m.token):
            rolle = {"punkt": "posten", "start": "start", "ziel": "ziel"}.get(m.art)
        elif m.art == "ziel" and m.rennleitung_getrennt and gleich(m.token_rennleitung):
            rolle = "rennleitung"
        if rolle:
            return m, session.get(Veranstaltung, m.veranstaltung_id), rolle
    return None


# --- Anlegen, Vorschlagen, Migration ----------------------------------------------------


def anlegen(session: Session, v: Veranstaltung, liste: list[Vorschlag]) -> None:
    """Strecken der Veranstaltung durch die Liste ersetzen (ohne commit)."""
    _anlegen(session, v, liste)


def _anlegen(session: Session, v: Veranstaltung, liste: list[Vorschlag]) -> None:
    session.execute(delete(StreckeKlasse).where(StreckeKlasse.strecke_id.in_(
        select(Strecke.id).where(Strecke.veranstaltung_id == v.id))))  # fmt: skip
    session.execute(delete(Strecke).where(Strecke.veranstaltung_id == v.id))
    for i, x in enumerate(liste):
        s = Strecke(veranstaltung_id=v.id, position=i, name=x.name, laenge_km=x.laenge_km,
                    klassen=[StreckeKlasse(klasse=kl) for kl in x.klassen])  # fmt: skip
        session.add(s)
        neue_strecke_orte(session, v, s)
    session.flush()


def sicherstellen(session: Session, v: Veranstaltung) -> None:
    """Veranstaltung ohne Strecken: genau eine Strecke aus `strecke_km` mit allen Klassen."""
    if v.id is None:
        session.flush()
    orte_sicherstellen(session, v)
    if session.scalars(select(Strecke.id).where(Strecke.veranstaltung_id == v.id)).first():
        return
    km = round(v.strecke_km, 1) if v.strecke_km else None
    name = f"{km:g} km".replace(".", ",") if km else "Strecke 1"
    _anlegen(session, v, [Vorschlag(name, km, zuordnungsklassen(regeln(session, v).klassen))])


def anzeigen_speichern(session: Session, v: Veranstaltung, sichtbar: dict[int, set[str]]) -> None:
    """„Ergebnistabelle“ (Segmente 52): je Strecke (Schlüssel in sichtbar) die Abschnitte,
    die in Ergebnis, Druck und CSV erscheinen. Auch nach dem Einfrieren änderbar (ohne commit)."""
    import json

    if v.zustand == "anonymisiert":
        raise StreckenFehler("Veranstaltung ist anonymisiert.")
    for s in strecken_von(session, v):
        if s.id not in sichtbar:
            continue
        e = json.loads(s.abschnitte_json) if s.abschnitte_json else {}
        for a in abschnitte_von(s):
            aus = a.id not in sichtbar[s.id]
            if a.segment:
                e.setdefault("segmente", {}).setdefault(a.segment, {})["aus"] = aus
            elif a.block:
                e.setdefault("bloecke", {}).setdefault(a.block, {})["aus"] = aus
            else:
                for x in e.get("eigene") or []:
                    if str(x.get("id")) == a.id:
                        x["aus"] = aus
        s.abschnitte_json = json.dumps(e, ensure_ascii=False) if e else None
    session.flush()


# --- Längen-Plätze im Assistenten (Segmente 49) ---------------------------------------------


@dataclass
class Platz:
    """Ein Längen-Platz aus Klassen- und Wertungssatz, z. B. „5 km“ mit den Klassen U13, U15 …

    Segmente 59: Was der Platz zeigt, kommt aus der tatsächlichen Klassenzuordnung (gruppen); offen
    ist er, solange eine seiner Klassen noch an der vorläufigen Strecke hängt."""

    schluessel: str
    name: str
    laenge_km: float | None
    klassen: list[str] = field(default_factory=list)
    strecke: Strecke | None = None  # alle Klassen an genau einer (gewählten) Strecke
    hinweis: str = ""
    gruppen: list = field(default_factory=list)  # [(Strecke, [Klassen])] laut Klassenzuordnung
    offen: bool = True


def _einrichtung(v: Veranstaltung) -> dict:
    try:
        return dict(json.loads(v.einrichtung_json or "{}") or {})
    except (ValueError, TypeError):
        return {}


def _entscheidungen(v: Veranstaltung) -> dict[str, int]:
    return dict(_einrichtung(v).get("plaetze") or {})


def _entscheidungen_setzen(v: Veranstaltung, ent: dict[str, int], **mehr) -> None:
    daten = _einrichtung(v) | {"plaetze": ent} | mehr
    v.einrichtung_json = json.dumps(daten, ensure_ascii=False)


def vorlaeufige_strecke(session: Session, v: Veranstaltung) -> Strecke | None:
    """Die vorläufige Strecke der neuen Veranstaltung (Invariante „mindestens eine Strecke“), solange
    sie nicht gewählt wurde. Ältere Einträge ohne Angabe: die erste nie gewählte Strecke."""
    daten = _einrichtung(v)
    liste = strecken_von(session, v)
    if "vorlaeufig" in daten:
        return next((x for x in liste if x.id == daten["vorlaeufig"]), None)
    gewaehlt = set((daten.get("plaetze") or {}).values())
    return min((x for x in liste if x.id not in gewaehlt), key=lambda x: x.id, default=None)


def plaetze(session: Session, v: Veranstaltung) -> list[Platz]:
    """Plätze aus dem Vorschlag (je Länge des Wertungssatzes einer, sonst einer für alle Klassen)
    mit der tatsächlichen Klassenzuordnung (Segmente 59: eine Quelle der Wahrheit)."""
    r = regeln(session, v)
    liste = strecken_von(session, v)
    je_klasse = {sk.klasse: x for x in liste for sk in x.klassen}
    vorlaeufig = vorlaeufige_strecke(session, v)
    ergebnis = []
    for x in vorschlag(r.klassen, r.wertung):
        gruppen: dict[int, tuple] = {}
        for kl in x.klassen:
            st = je_klasse.get(kl)
            if st is not None:
                gruppen.setdefault(st.id, (st, []))[1].append(kl)
        offen = any(kl not in je_klasse or (vorlaeufig is not None and je_klasse[kl].id == vorlaeufig.id)
                    for kl in x.klassen)  # fmt: skip
        s = next(iter(gruppen.values()))[0] if len(gruppen) == 1 and not offen else None
        hinweis = ""
        if (
            s is not None
            and x.laenge_km
            and s.laenge_km
            and round(s.laenge_km, 1) != round(x.laenge_km, 1)
        ):
            hinweis = f"Die Datei gibt {km_text(s.laenge_km)} km an (Platz {km_text(x.laenge_km)} km) – es gilt die Datei."
        ergebnis.append(Platz(x.name, x.name, x.laenge_km, list(x.klassen), s, hinweis,
                              list(gruppen.values()), offen))  # fmt: skip
    return ergebnis


def plaetze_offen(session: Session, v: Veranstaltung) -> list[Platz]:
    return [p for p in plaetze(session, v) if p.offen]


def platz_entscheiden(session: Session, v: Veranstaltung, schluessel: str, s: Strecke) -> list[str]:
    """Strecke s übernimmt die Klassen des Platzes (ohne commit) – erstmals oder bei „Ändern“
    (Segmente 59). Die bisher gewählte Strecke dieses Platzes und die vorläufige Strecke fallen weg,
    wenn sie danach keine Klassen mehr haben; sonst bleiben sie mit Hinweis. Gibt Hinweise zurück."""
    if v.regeln_json:
        raise StreckenFehler("Die Streckenzuordnung ist eingefroren (es gibt bereits Zeiten).")
    p = next((p for p in plaetze(session, v) if p.schluessel == schluessel), None)
    if p is None:
        raise StreckenFehler(f"Den Platz „{schluessel}“ gibt es nicht (mehr).")
    vorlaeufig = vorlaeufige_strecke(session, v)
    bisher = {st.id for st, _ in p.gruppen if st.id != s.id}
    _klassen_zu(session, v, p.klassen, s)
    ent = _entscheidungen(v)
    ent[schluessel] = s.id
    neu_vorlaeufig = None if vorlaeufig is None or vorlaeufig.id == s.id else vorlaeufig.id
    hinweise = []
    liste = strecken_von(session, v)
    for x in list(liste):
        if x is s or x.id not in bisher | {neu_vorlaeufig}:
            continue
        if not x.klassen and len(liste) > 1:
            session.delete(x)
            liste.remove(x)
            if x.id == neu_vorlaeufig:
                neu_vorlaeufig = None
        elif x.id in bisher:
            hinweise.append(
                f"Strecke „{x.name}“ bleibt – sie fährt noch {', '.join(sk.klasse for sk in x.klassen)}."
            )
    _entscheidungen_setzen(v, {k: sid for k, sid in ent.items() if any(x.id == sid for x in liste)},
                           vorlaeufig=neu_vorlaeufig)  # fmt: skip
    session.flush()
    for i, x in enumerate(strecken_von(session, v)):
        x.position = i
    unbenutzte_standards_ausblenden(session, v)
    return hinweise


def _klassen_zu(session: Session, v: Veranstaltung, klassen: list[str], s: Strecke) -> None:
    for x in strecken_von(session, v):
        if x is not s:
            x.klassen = [sk for sk in x.klassen if sk.klasse not in klassen]
    da = {sk.klasse for sk in s.klassen}
    s.klassen = [*s.klassen, *(StreckeKlasse(klasse=kl) for kl in klassen if kl not in da)]
    session.flush()


def platzhalter_anlegen(session: Session, v: Veranstaltung, schluessel: str, name: str,
                        laenge: str) -> tuple[Strecke, list[str]]:  # fmt: skip
    """„Platzhalter-Strecke anlegen“ (Name und Länge vorbelegt, ohne commit). Die vorläufige Strecke
    wird dafür verwendet, wenn sie gleich heißt. Gibt (Strecke, Hinweise) zurück."""
    if v.regeln_json:
        raise StreckenFehler("Die Streckenzuordnung ist eingefroren (es gibt bereits Zeiten).")
    name = (name or "").strip()[:100]
    if not name:
        raise StreckenFehler(f"Platz „{schluessel}“: Name der Strecke fehlt.")
    km = strecke_parsen(laenge)
    vorlaeufig = vorlaeufige_strecke(session, v)
    liste = strecken_von(session, v)
    gleich = next((x for x in liste if x.name.lower() == name.lower()), None)
    if gleich is not None and not (
        vorlaeufig is not None and gleich.id == vorlaeufig.id and not gleich.teile_json
    ):
        raise StreckenFehler(
            f"Eine Strecke „{name}“ gibt es schon – bitte einen anderen Namen wählen."
        )
    s = gleich or Strecke(veranstaltung_id=v.id, position=len(liste), name=name)
    s.laenge_km = km
    if gleich is None:
        session.add(s)
        neue_strecke_orte(session, v, s)
        session.flush()
    return s, platz_entscheiden(session, v, schluessel, s)


def zuordnung_anpassen(session: Session, v: Veranstaltung) -> list[str]:
    """Klassen- oder Wertungssatz gewechselt (Segmente 49, statt Strecken neu vorzuschlagen):
    Strecken bleiben. Klassen, die es nicht mehr gibt, fallen aus der Zuordnung; neue Klassen
    gehen im Assistenten an die gewählte Strecke ihres Platzes, sonst an die Strecke gleicher Länge
    bzw. die letzte. Entscheidungen für Plätze, die es nicht mehr gibt, verfallen. Ohne commit."""
    if v.regeln_json:
        raise StreckenFehler("Die Streckenzuordnung ist eingefroren (es gibt bereits Zeiten).")
    sicherstellen(session, v)
    r = regeln(session, v)
    namen = zuordnungsklassen(r.klassen)
    liste = strecken_von(session, v)
    for s in liste:
        s.klassen = [sk for sk in s.klassen if sk.klasse in namen]
    session.flush()
    vorschlaege = vorschlag(r.klassen, r.wertung)
    ent = {k: sid for k, sid in _entscheidungen(v).items()
           if k in {x.name for x in vorschlaege} and any(s.id == sid for s in liste)}  # fmt: skip
    if v.einrichtung_json is not None:
        _entscheidungen_setzen(v, ent)
    je_id = {s.id: s for s in liste}
    for x in vorschlaege:
        if x.name in ent:
            _klassen_zu(session, v, x.klassen, je_id[ent[x.name]])
    zugeordnet = {sk.klasse for s in liste for sk in s.klassen}
    neu = [kl for kl in namen if kl not in zugeordnet]
    laenge_von = {kl: x.laenge_km for x in vorschlaege for kl in x.klassen}
    for kl in neu:
        passend = next((s for s in liste if s.laenge_km and laenge_von.get(kl)
                        and round(s.laenge_km) == round(laenge_von[kl])), liste[-1])  # fmt: skip
        passend.klassen = [*passend.klassen, StreckeKlasse(klasse=kl)]
    session.flush()
    if neu and len(liste) > 1:
        return [
            f"Strecken unverändert; neu zugeordnet: {', '.join(neu)} – bitte die Klassenzuordnung prüfen."
        ]
    return []


def migrieren(session: Session) -> None:
    """Beim Start: jede Veranstaltung ohne Strecken bekommt genau eine (Ergebnisse unverändert);
    Segment-Daten im Modell von phase-14 werden ins Segment-Modell übertragen."""
    mit = set(session.scalars(select(Strecke.veranstaltung_id)))
    for v in session.scalars(select(Veranstaltung)):
        if v.id not in mit:
            sicherstellen(session, v)
        orte_sicherstellen(session, v)  # Segmente 37: Standard-Start- und -Zielort
    segmente_migrieren(session)
    messpunkte_eindeutig(session)
    session.commit()


def segmente_migrieren(session: Session) -> None:
    """Strecken mit Messpunkten oder Runden (phase-14) ohne Teile → Teile mit gleicher Punktfolge.

    „Ziellinie ist Rundenlinie“ ist nicht abbildbar (das Ziel tippt nur noch den Zieleinlauf):
    solche Strecken bekommen keine Segmente (FRAGEN A118).
    """
    for s in session.scalars(select(Strecke).where(Strecke.teile_json.is_(None))):
        mps = session.scalars(
            select(Messpunkt).where(Messpunkt.strecke_id == s.id).order_by(Messpunkt.position)
        )
        alt = [(str(m.id), m.name, m.abschnitt or "runde", m.km) for m in mps]
        if not alt and (s.runden or 1) <= 1:
            continue
        if s.rundenlinie and not alt:
            s.teile_json = "[]"
            continue
        s.teile_json = sg.nach_json(sg.aus_altem_modell(alt, s.runden or 1))


def messpunkte_eindeutig(session: Session) -> None:
    """Segmente 12: Messpunkte von Strecken einer Veranstaltung zusammengeführt – Namen eindeutig
    machen („Kreisel (20 km)“) und die Reihenfolge über die Veranstaltung durchnummerieren."""
    for v in session.scalars(select(Veranstaltung)):
        liste = strecken_von(session, v)
        reihe = {s.id: i for i, s in enumerate(liste)}
        mps = sorted(
            messpunkte_von(session, v),
            key=lambda m: (reihe.get(m.strecke_id, -1), m.position, m.id),
        )
        namen = [m.name.lower() for m in mps]
        streckenname = {s.id: s.name for s in liste}
        for pos, m in enumerate(mps):
            if namen.count(m.name.lower()) > 1 and m.strecke_id in streckenname:
                m.name = f"{m.name} ({streckenname[m.strecke_id]})"[:100]
            m.position = pos


# --- Speichern aus dem Formular ------------------------------------------------------------


def strecken_speichern(session: Session, v: Veranstaltung, zeilen: list[dict],
                       klassen_zuordnung: dict[str, str], klassen_mit_teilnehmern: set[str],
                       offiziell: bool = False, messpunkte: list[dict] | None = None,
                       teile: list[dict] | None = None, abschnitte: list[dict] | None = None,
                       nur: set[int] | None = None) -> list[str]:  # fmt: skip
    """Strecken und Zuordnung aus dem Formular übernehmen. Gibt Hinweise zurück.

    zeilen: [{"id": "3" oder "", "name": …, "laenge": "20,0", "entfernen": bool}] in Anzeigereihenfolge;
    klassen_zuordnung: Klasse → Zeilenindex (als Text). Leere Zeilen (ohne Name und Länge) zählen nicht.
    Segmente: messpunkte der Veranstaltung [{"id", "name", "entfernen"}] und teile [{"strecke",
    "block", "wdh", "name", "von", "nach", "km", "vergleich", "entfernen"}] in Folge (aufeinander-
    folgende Zeilen mit gleichem Blocknamen bilden einen Rundenblock; „von“/„nach“ = „Start“, „Ziel“
    oder Messpunkt-Name). abschnitte (Segmente 13): [{"strecke", "segment" (Schlüssel bei Segment-
    Abschnitten), "id", "name", "von", "nach", "art", "von_nr", "nach_nr", "vergleich", "aus",
    "entfernen"}]. None lässt Messpunkte, Teile bzw. Abschnitte unverändert. nur (Segmente 18): Teile und
    Abschnitte nur für diese Zeilen übernehmen (Seite einer Strecke), die anderen bleiben.
    """
    if v.zustand == "anonymisiert":
        raise StreckenFehler("Veranstaltung ist anonymisiert.")
    sicherstellen(session, v)
    alt = strecken_von(session, v)
    alt_nach_id = {str(s.id): s for s in alt}
    r = regeln(session, v)
    namen_klassen = zuordnungsklassen(r.klassen)
    eingefroren = bool(v.regeln_json)

    behalten: dict[int, dict] = {}  # Zeilenindex → Daten
    for i, z in enumerate(zeilen):
        name = str(z.get("name") or "").strip()
        laenge_text = str(z.get("laenge") or "").strip()
        sid = str(z.get("id") or "")
        if z.get("entfernen") or (not name and not laenge_text and sid not in alt_nach_id):
            continue
        if not name:
            raise StreckenFehler(f"Strecke {i + 1}: Name fehlt.")
        try:
            laenge = strecke_parsen(laenge_text)
        except SatzFehler as e:
            raise StreckenFehler(f"Strecke „{name}“: {e}") from e
        behalten[i] = {
            "id": sid if sid in alt_nach_id else None,
            "name": name[:100],
            "laenge": laenge,
        }
    if not behalten:
        raise StreckenFehler("Mindestens eine Strecke ist nötig.")
    gesehen: set[str] = set()
    for d in behalten.values():
        if d["name"].lower() in gesehen:
            raise StreckenFehler(f"Streckenname „{d['name']}“ ist doppelt.")
        gesehen.add(d["name"].lower())

    eingefroren_fehler = StreckenFehler(
        "Die Streckenzuordnung ist eingefroren (es gibt bereits Zeiten): Strecken und "
        "Zuordnung lassen sich nicht mehr ändern, nur Name und Länge."
    )
    if eingefroren and {d["id"] for d in behalten.values()} != {str(s.id) for s in alt}:
        raise eingefroren_fehler  # Strecke hinzugefügt oder entfernt
    mp_neu = _messpunkte_pruefen(messpunkte, behalten, alt, eingefroren,
                                 {str(m.id): m for m in messpunkte_von(session, v)})  # fmt: skip
    teile_neu = _im_bereich("verlauf", _teile_lesen, teile, behalten) if teile is not None else None

    # Zuordnung: Klasse → behaltener Zeilenindex
    bisher = zuordnung(alt)
    index_von_id = {d["id"]: i for i, d in behalten.items() if d["id"]}
    ziel: dict[str, int] = {}
    erste = min(behalten)
    for kl in namen_klassen:
        wahl = str(klassen_zuordnung.get(kl, ""))
        if wahl.isdigit() and int(wahl) in behalten:
            ziel[kl] = int(wahl)
            continue
        if not wahl and kl in bisher and str(alt[bisher[kl]].id) in index_von_id:
            ziel[kl] = index_von_id[str(alt[bisher[kl]].id)]  # nicht übermittelt: bleibt
            continue
        if kl in klassen_mit_teilnehmern:
            raise StreckenFehler(f"Klasse „{kl}“ hat Teilnehmer, aber keine Strecke.")
        ziel[kl] = erste

    if eingefroren and any(behalten[ziel[kl]]["id"] != str(alt[i].id)
                           for kl, i in bisher.items() if kl in ziel):  # fmt: skip
        raise eingefroren_fehler
    if offiziell:
        for d in behalten.values():
            if d["id"] and alt_nach_id[d["id"]].laenge_km != d["laenge"]:
                raise StreckenFehler(
                    "Das Ergebnis ist offiziell erklärt – die Streckenlänge lässt sich erst nach "
                    "„Zurücknehmen“ ändern."
                )

    klassen_je_zeile: dict[int, list[str]] = {i: [] for i in behalten}
    for kl, i in ziel.items():
        klassen_je_zeile[i].append(kl)
    if r.wertung is not None and not (v.einrichtung_schritt and plaetze_offen(session, v)):
        for (
            i,
            d,
        ) in behalten.items():  # im Assistenten erst, wenn je Platz entschieden ist (Segmente 49)
            if klassen_je_zeile[i] and d["laenge"] is None:
                raise StreckenFehler(
                    f"Strecke „{d['name']}“: Für die Leistungswertung ist die Streckenlänge Pflicht."
                )

    # übernehmen: vorhandene Zeilen aktualisieren (Farbe/Position bleiben stabil), Rest neu
    weg = [s for s in alt if str(s.id) not in index_von_id]
    for s in weg:
        session.delete(s)
    session.flush()
    for pos, (i, d) in enumerate(sorted(behalten.items())):
        s = alt_nach_id[d["id"]] if d["id"] else Strecke(veranstaltung_id=v.id)
        if not d["id"]:
            session.add(s)
            neue_strecke_orte(session, v, s)
        s.position, s.name, s.laenge_km = pos, d["name"], d["laenge"]
        s.klassen = [StreckeKlasse(klasse=kl) for kl in klassen_je_zeile[i]]
    hinweise_mp: list[str] = []
    if mp_neu is not None:  # Messpunkte der Veranstaltung (Segmente 12)
        vorhanden = {str(m.id): m for m in messpunkte_von(session, v)}
        bleibt_alt = [s for i, s in zip(sorted(behalten), strecken_von(session, v), strict=False)
                      if teile_neu is None or (nur is not None and i not in nur)]  # fmt: skip
        for m in vorhanden.values():
            if str(m.id) not in {x["id"] for x in mp_neu}:
                nutzer = [s.name for s in bleibt_alt if str(m.id) in sg.innere_punkte(teile_von(s))]
                if nutzer:
                    raise StreckenFehler(f"Zwischenzeit „{m.name}“ wird noch von {', '.join(nutzer)} genutzt – "
                                         "erst im Streckenverlauf entfernen.")  # fmt: skip
                session.delete(m)
        for j, x in enumerate(mp_neu):
            m = vorhanden.get(x["id"]) or Messpunkt(veranstaltung_id=v.id)
            m.position, m.name = j, x["name"]
            session.add(m)
        session.flush()  # neue Messpunkte brauchen ihre ID
    alle_mps = messpunkte_von(session, v)
    ids = {m.name.lower(): str(m.id) for m in alle_mps}
    for pos, (i, d) in enumerate(sorted(behalten.items())):
        s = next(x for x in strecken_von(session, v) if x.position == pos)
        if teile_neu is not None and (nur is None or i in nur):
            neu = _im_bereich("verlauf", _teile_bauen, teile_neu.get(i, []), ids, d["name"])
            fehler = sg.pruefen(neu, {str(m.id): m.name for m in alle_mps}, alle_genutzt=False)
            if fehler:
                raise StreckenFehler(f"Strecke „{d['name']}“: " + " ".join(fehler), "verlauf")
            if eingefroren and d["id"] and _aufbau(neu) != _aufbau(sg.aus_json(s.teile_json)):
                raise StreckenFehler(
                    "Segmente sind eingefroren (es gibt bereits Zeiten): nur Namen, Längen und "
                    "„Vergleich“ änderbar.",
                    "verlauf",
                )
            s.teile_json = sg.nach_json(neu) if neu else None
        if abschnitte is not None and (nur is None or i in nur):
            eigene = [x for x in abschnitte if str(x.get("strecke")) == str(i)]
            s.abschnitte_json = _im_bereich(
                "abschnitte", _abschnitte_bauen, eigene, s, ids, d["name"], alle_mps
            )
    if teile_neu is not None or mp_neu is not None:
        genutzt = {p for s in strecken_von(session, v) for p in sg.innere_punkte(teile_von(s))}
        hinweise_mp = [f"Zwischenzeit „{m.name}“ kommt noch in keiner Strecke vor." for m in alle_mps
                       if str(m.id) not in genutzt]  # fmt: skip
    unbenutzte_standards_ausblenden(session, v)
    session.commit()
    return [f"Strecke „{d['name']}“ hat keine Klasse." for i, d in sorted(behalten.items())
            if not klassen_je_zeile[i]] + hinweise_mp  # fmt: skip


def orte_speichern(session: Session, v: Veranstaltung, zeilen: list[dict]) -> list[str]:
    """Seite „Orte“ (Segmente 37): [{"id", "name", "art", "entfernen"}] in Anzeigereihenfolge.

    Die Art eines vorhandenen Orts bleibt; neue Zeilen ohne Art sind Messpunkte. Standardorte und
    Orte, die eine Strecke nutzt, sind nicht entfernbar; nach der ersten Zeit kommen keine Orte
    dazu oder weg (Namen bleiben änderbar). Entfernte Orte hinterlassen den Fingerabdruck ihrer
    Tokens (Geräte bekommen 410). Gibt Hinweise zurück.
    """
    from app.models import GeloeschterToken, token_fingerabdruck

    if v.zustand == "anonymisiert":
        raise StreckenFehler("Veranstaltung ist anonymisiert.")
    sicherstellen(session, v)
    vorhanden = {str(m.id): m for m in orte_von(session, v)}
    entfernt = {
        (m.art, m.name.lower()): m for m in orte_von(session, v, entfernte=True) if m.entfernt
    }
    liste = strecken_von(session, v)
    paare = orte_der_strecken(session, v)
    neu: list[tuple[Messpunkt | None, str, str]] = []  # (vorhandener Ort, Name, Art)
    weg: list[Messpunkt] = []
    for j, x in enumerate(zeilen):
        name = str(x.get("name") or "").strip()[:100]
        oid = str(x.get("id") or "")
        m = vorhanden.get(oid)
        if x.get("entfernen") and m is not None:
            weg.append(m)
            continue
        if x.get("entfernen") or (not name and m is None):
            continue
        art = m.art if m is not None else str(x.get("art") or "punkt")
        if art not in ORTSARTEN:
            raise StreckenFehler(f"Messpunkt {j + 1}: unbekannte Art.")
        if not name:
            raise StreckenFehler(f"{ORTSARTEN[art]} {j + 1}: Name fehlt.")
        for wort, a in (("start", "start"), ("ziel", "ziel")):
            if name.lower() == wort and art != a:
                raise StreckenFehler(
                    f"„{name}“ ist als Name nur für einen Messpunkt der Art „{ORTSARTEN[a]}“ möglich."
                )
        if m is None and (art, name.lower()) in entfernt and not v.regeln_json:
            m = entfernt.pop((art, name.lower()))  # Segmente 65: entfernter Standard kommt zurück
            m.entfernt = False
        neu.append((m, name, art))
    namen = [n.lower() for _, n, _ in neu]
    doppelt = next((n for _, n, _ in neu if namen.count(n.lower()) > 1), None)
    if doppelt:
        raise StreckenFehler(f"Messpunkt „{doppelt}“ ist doppelt.")
    for m in vorhanden.values():  # nicht übermittelte Orte bleiben
        if m not in weg and all(m is not x for x, _, _ in neu):
            neu.append((m, m.name, m.art))
    eingefroren = bool(v.regeln_json)
    if eingefroren and (weg or any(m is None for m, _, _ in neu)):
        raise StreckenFehler(
            "Messpunkte sind eingefroren (es gibt bereits Zeiten): nur der Name ist änderbar."
        )
    for m in weg:  # Segmente 65: auch der Standard, wenn ihn keine Strecke nutzt
        if m.art == "punkt":
            nutzer = [liste[i].name for i in nutzende_strecken(liste, m.id)]
        else:
            nutzer = [liste[i].name for i, p in enumerate(paare) if m.id in (p[0].id, p[1].id)]
        if nutzer:
            raise StreckenFehler(f"{ORTSARTEN[m.art]} „{m.name}“ wird noch von {', '.join(nutzer)} "
                                 "genutzt – erst im Streckenverlauf ändern.")  # fmt: skip
    for m in [m for m in weg if m.standard]:  # Tokens der Veranstaltung: Zeile bleibt, ausgeblendet
        m.entfernt = True
    weg = [m for m in weg if not m.standard]
    for m in weg:
        for token in (m.token, m.token_rennleitung):
            abdruck = token_fingerabdruck(token) if token else None
            if abdruck and not session.scalar(
                select(GeloeschterToken.id).where(GeloeschterToken.fingerabdruck == abdruck)
            ):
                session.add(GeloeschterToken(fingerabdruck=abdruck))
        session.delete(m)
    session.flush()
    for pos, (m, name, art) in enumerate(neu):
        if m is None:
            m = Messpunkt(veranstaltung_id=v.id, art=art)
            session.add(m)
        m.position, m.name = pos, name
    session.flush()
    genutzt = {p for st in strecken_von(session, v) for p in sg.innere_punkte(teile_von(st))}
    hinweise = [f"Zwischenzeit „{m.name}“ kommt noch in keiner Strecke vor."
                for m in messpunkte_von(session, v) if str(m.id) not in genutzt]  # fmt: skip
    for m in orte_von(session, v):
        if m.art != "punkt" and not m.standard and not strecken_am_ort(session, v, m):
            hinweise.append(f"{ORTSARTEN[m.art]} „{m.name}“ nutzt noch keine Strecke.")
    session.commit()
    return hinweise


def strecke_orte_setzen(session: Session, v: Veranstaltung, s: Strecke, start: str | None,
                        ziel: str | None) -> None:  # fmt: skip
    """Start- und Zielort einer Strecke (Segmente 37, IDs als Text; None = unverändert, ohne
    commit). Nach der ersten Zeit fest wie der Verlauf."""
    orte = orte_von(session, v)
    alt = (startort_von(s, orte).id, zielort_von(s, orte).id)
    neu = list(alt)
    for k, (wert, art) in enumerate(((start, "start"), (ziel, "ziel"))):
        if wert is None:
            continue
        m = next((m for m in orte if str(m.id) == str(wert) and m.art == art), None)
        if m is None:
            raise StreckenFehler(f"Unbekannter Messpunkt für „{ORTSARTEN[art]}“.", "verlauf")
        neu[k] = m.id
    if tuple(neu) == alt:
        return
    if v.regeln_json:
        raise StreckenFehler(
            "Start und Ziel der Strecke sind eingefroren (es gibt bereits Zeiten).", "verlauf"
        )
    standard = standard_ids(session, v)
    s.startort_id = None if neu[0] == standard.get("start") else neu[0]
    s.zielort_id = None if neu[1] == standard.get("ziel") else neu[1]


def _messpunkte_pruefen(messpunkte, behalten, alt, eingefroren, vorhanden=None):
    """Messpunkte der Veranstaltung aus dem Formular → [{"id", "name"}] in Folge oder None."""
    if messpunkte is None:
        return None
    vorhanden = vorhanden or {}
    liste: list[dict] = []
    for j, x in enumerate(messpunkte):
        name = str(x.get("name") or "").strip()
        mid = str(x.get("id") or "")
        if x.get("entfernen") or (not name and mid not in vorhanden):
            continue
        if not name:
            raise StreckenFehler(f"Zwischenzeit {j + 1}: Name fehlt.")
        if name.lower() in ("start", "ziel"):
            raise StreckenFehler(f"Zwischenzeit „{name}“: „Start“ und „Ziel“ sind eigene Geräte.")
        liste.append({"id": mid if mid in vorhanden else None, "name": name[:100]})
    namen = [x["name"].lower() for x in liste]
    doppelt = next((n for n in namen if namen.count(n) > 1), None)
    if doppelt:
        raise StreckenFehler(f"Zwischenzeit „{doppelt}“ ist doppelt.")
    if eingefroren and sorted(x["id"] or "" for x in liste) != sorted(vorhanden):
        raise StreckenFehler(
            "Zwischenzeiten sind eingefroren (es gibt bereits Zeiten): nur der Name ist änderbar."
        )
    return liste


def _km(text, wo: str) -> float | None:
    t = str(text or "").strip().replace(",", ".")
    if not t:
        return None
    try:
        km = round(float(t), 2)
    except ValueError:
        km = -1
    if km < 0:
        raise StreckenFehler(f"{wo}: Länge ungültig.")
    return km


def _teile_lesen(teile: list[dict], behalten) -> dict[int, list[dict]]:
    """Teile-Zeilen aus dem Formular → {Streckenzeile: [Zeilen in Folge]} (leere Zeilen fallen weg)."""
    je_zeile: dict[int, list[dict]] = {}
    for j, x in enumerate(teile):
        von, nach = str(x.get("von") or "").strip(), str(x.get("nach") or "").strip()
        if x.get("entfernen") or not (von or nach or str(x.get("name") or "").strip()):
            continue
        zeile = str(x.get("strecke") or "")
        if not zeile.isdigit() or int(zeile) not in behalten:
            raise StreckenFehler(f"Segment {j + 1}: Strecke fehlt oder wird entfernt.")
        if not von or not nach:
            raise StreckenFehler(f"Segment {j + 1}: „von“ und „nach“ sind Pflicht.")
        wdh_text = str(x.get("wdh") or "").strip()
        if wdh_text and (not wdh_text.isdigit() or not 1 <= int(wdh_text) <= sg.MAX_WDH):
            raise StreckenFehler(f"Segment {j + 1}: Wiederholungen müssen 1 bis {sg.MAX_WDH} sein.")
        je_zeile.setdefault(int(zeile), []).append({
            "block": str(x.get("block") or "").strip()[:100], "wdh": int(wdh_text) if wdh_text else None,
            "name": str(x.get("name") or "").strip()[:100], "von": von, "nach": nach,
            "km": _km(x.get("km"), f"Segment {j + 1}"), "vergleich": bool(x.get("vergleich")),
            "teil": x.get("teil"),  # Punktfolge (Segmente 28): Zeilen eines Teils, unabhängig vom Namen
        })  # fmt: skip
    return je_zeile


def _teile_bauen(zeilen: list[dict], ids: dict[str, str], strecke: str) -> tuple:
    """Formularzeilen einer Strecke → Teile; aufeinanderfolgende gleiche Blocknamen = ein Block."""

    def punkt(text: str) -> str:
        t = text.strip().lower()
        if t == "start":
            return sg.START
        if t == "ziel":
            return sg.ZIEL
        if t not in ids:
            raise StreckenFehler(
                f"Strecke „{strecke}“: „{text}“ ist keine Zwischenzeit der Veranstaltung (erst unter "
                "„Messpunkte“ anlegen)."
            )
        return ids[t]

    def gleich(a: dict, b: dict) -> bool:
        if a.get("teil") is not None:
            return a["teil"] == b.get("teil")
        return bool(a["block"]) and a["block"] == b["block"]

    teile, i = [], 0
    while i < len(zeilen):
        block = zeilen[i]["block"]
        gruppe = [zeilen[i]]
        while i + len(gruppe) < len(zeilen) and gleich(zeilen[i], zeilen[i + len(gruppe)]):
            gruppe.append(zeilen[i + len(gruppe)])
        i += len(gruppe)
        wdh_werte = {z["wdh"] for z in gruppe if z["wdh"] is not None}
        if len(wdh_werte) > 1:
            raise StreckenFehler(f"Rundenblock „{block}“: unterschiedliche Wiederholungen.")
        wdh = wdh_werte.pop() if wdh_werte else 1
        if not block and wdh > 1:
            raise StreckenFehler(
                f"Strecke „{strecke}“: Wiederholungen nur in einem Rundenblock (Blockname eintragen)."
            )
        segs = []
        for z in gruppe:
            von, nach = punkt(z["von"]), punkt(z["nach"])
            name = z["name"] or f"{z['von'].strip()}→{z['nach'].strip()}"
            segs.append(
                sg.SegmentDef(name, von, nach, z["km"])
            )  # „Vergleich“: Abschnitt (Segmente 13)
        teile.append(sg.TeilDef(tuple(segs), wdh, block))
    return tuple(teile)


def _abschnitte_bauen(
    zeilen: list[dict], s: Strecke, ids: dict[str, str], strecke: str, mps
) -> str | None:
    """Abschnitts-Zeilen einer Strecke → JSON für strecke.abschnitte_json (Segmente 13)."""
    import json

    teile = teile_von(s)
    namen = {str(m.id): m.name for m in mps}
    schluessel = {d.schluessel for d in sg.abfolge(teile)}
    bloecke_ok = {str(i) for i, t in enumerate(teile) if t.block and len(t.segmente) > 1}
    segmente, bloecke, eigene, vergeben = {}, {}, [], set()
    bisher = {a.id: a for a in abschnitte_von(s)}
    alt = {aid for aid, a in bisher.items() if not a.automatisch}

    def behalten(x: dict, aid: str, feld: str) -> bool:
        """Segmente 52: Anzeigen/Vergleich stellt der Editor nicht mehr ein – fehlt das Feld, bleibt
        der gespeicherte Wert (neue Abschnitte: angezeigt, ohne Vergleich)."""
        if feld in x:
            return bool(x[feld])
        a = bisher.get(aid)
        return bool(getattr(a, feld)) if a is not None else False

    def punkt(text: str) -> str:
        t = text.strip().lower()
        if t in ("start", "ziel"):
            return sg.START if t == "start" else sg.ZIEL
        if t not in ids:
            raise StreckenFehler(
                f"Strecke „{strecke}“: „{text}“ ist keine Zwischenzeit der Veranstaltung."
            )
        return ids[t]

    def nr(text, wo: str) -> int:
        t = str(text or "").strip() or "1"
        if not t.isdigit() or int(t) < 1:
            raise StreckenFehler(
                f"Abschnitt „{wo}“: Nummer der Durchfahrt muss 1 oder größer sein."
            )
        return int(t)

    def punkt_nr(text: str, nummer, wo: str) -> tuple[str, int]:
        """„2:Kreisel“ aus der Auswahl (Segmente 33) oder Punkt und Nummer getrennt."""
        nummer_text, _, rest = text.partition(":")
        if nummer_text.strip().isdigit() and rest.strip().lower() in ids | {"start": 0, "ziel": 0}:
            return punkt(rest), nr(nummer_text, wo)
        return punkt(text), nr(nummer, wo)

    for x in zeilen:
        if x.get("segment"):
            if x["segment"] in schluessel:
                segmente[x["segment"]] = {
                    "aus": behalten(x, f"s{x['segment']}", "aus"),
                    "vergleich": behalten(x, f"s{x['segment']}", "vergleich"),
                    "ausdruecklich": "vergleich" in x,
                }
            continue
        if x.get("block"):  # Rundenblock (Segmente 32): nur ausblenden und Vergleich
            if x["block"] in bloecke_ok:
                bloecke[x["block"]] = {
                    "aus": behalten(x, f"b{x['block']}", "aus"),
                    "vergleich": behalten(x, f"b{x['block']}", "vergleich"),
                }
            continue
        name = str(x.get("name") or "").strip()[:100]
        if x.get("entfernen") or not (
            name or str(x.get("von") or "").strip() or str(x.get("nach") or "").strip()
        ):
            continue
        aid = str(x.get("id") or "")
        if aid not in alt or aid in vergeben:
            aid = next(f"a{n}" for n in range(1, 10_000) if f"a{n}" not in alt | vergeben)
        vergeben.add(aid)
        von, von_nr = punkt_nr(str(x.get("von") or ""), x.get("von_nr"), name)
        nach, nach_nr = punkt_nr(str(x.get("nach") or ""), x.get("nach_nr"), name)
        art = str(x.get("art") or sg.WIEDERKEHREND)
        vergleich = behalten(x, aid, "vergleich") and ("vergleich" in x or art == sg.WIEDERKEHREND)
        a = sg.AbschnittDef(aid, name, von, nach, art, von_nr, nach_nr, vergleich, behalten(x, aid, "aus"))  # fmt: skip
        fehler = sg.abschnitt_pruefen(a, teile, namen)
        if fehler:
            raise StreckenFehler(f"Strecke „{strecke}“: " + " ".join(fehler))
        eigene.append({"id": a.id, "name": a.name, "von": a.von, "nach": a.nach, "art": a.art,
                       "von_nr": a.von_nr, "nach_nr": a.nach_nr, "vergleich": a.vergleich, "aus": a.aus})  # fmt: skip
    for key, e in segmente.items():
        seg = next(x for x in sg.abschnitte(teile) if x.segment == key)
        ausdruecklich = e.pop("ausdruecklich")
        if e["vergleich"] and seg.art != sg.WIEDERKEHREND:
            if ausdruecklich:
                raise StreckenFehler(
                    f"Strecke „{strecke}“: Segment „{seg.name}“: „Vergleich“ nur bei wiederholten Segmenten."
                )
            e["vergleich"] = False  # früherer Schalter, nach geändertem Verlauf nicht mehr möglich
    if not segmente and not bloecke and not eigene:
        return None
    return json.dumps(
        {"segmente": segmente, "bloecke": bloecke, "eigene": eigene}, ensure_ascii=False
    )


def _aufbau(teile) -> list:
    """Was mit dem Einfrieren fest ist: Punktfolge, Blöcke und Wiederholungen."""
    return [(t.wdh, bool(t.block), tuple((s.von, s.nach) for s in t.segmente)) for t in teile]


def laenge_setzen(session: Session, v: Veranstaltung, km: float | None) -> bool:
    """Gibt es genau eine Strecke, bekommt sie diese Länge (bisheriges Feld „Streckenlänge“)."""
    sicherstellen(session, v)
    liste = strecken_von(session, v)
    if len(liste) != 1:
        return False
    liste[0].laenge_km = km
    return True


def wertung_laengen_pruefen(session: Session, v: Veranstaltung, weiter: bool = False) -> None:
    """Bei Leistungswertung braucht jede Strecke mit Klassen eine gemessene Länge. Im Assistenten
    erst bei „Weiter“ (weiter=True) und wenn für jeden Längen-Platz entschieden ist (Segmente 49/59)."""
    if v.einrichtung_schritt and (not weiter or plaetze_offen(session, v)):
        return
    if regeln(session, v).wertung is None:
        return
    for s in strecken_von(session, v):
        if s.klassen and s.laenge_km is None:
            raise StreckenFehler(
                "Für die Leistungswertung ist die Streckenlänge Pflicht"
                + (f" (Strecke „{s.name}“)." if len(strecken_von(session, v)) > 1 else ".")
            )
