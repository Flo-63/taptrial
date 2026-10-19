"""Leistungswertung Deutsches Radsportabzeichen (KONZEPT 7a).

Dateien in config/wertungen/*.toml werden beim Start geladen und geprüft; ungültig → kein Start.
Streckenlängen werden intern in Zehntel-Kilometern (int) geführt, damit alle Vergleiche exakt sind.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from app.klassen import Klasse

ARTEN = ("gesamtzeit", "pro_km")
STUFEN = ("GOLD", "SILBER", "BRONZE")
STUFE_TEXT = {"GOLD": "Gold", "SILBER": "Silber", "BRONZE": "Bronze"}


class WertungsConfigFehler(ValueError):
    pass


@dataclass(frozen=True)
class Stufe:
    klasse: str
    gold_ms: int
    silber_ms: int
    bronze_ms: int
    strecke_zehntel_km: int | None = None  # nur bei „gesamtzeit“


@dataclass(frozen=True)
class Wertung:
    datei: str
    name: str
    art: str
    stufen: dict[str, Stufe]
    strecke_min_zehntel_km: int | None = None  # nur bei „pro_km“
    strecke_max_zehntel_km: int | None = None


def zeit_zu_ms(text: str) -> int:
    """„H:MM:SS“ oder „M:SS“ → Millisekunden."""
    teile = str(text).strip().split(":")
    if not 2 <= len(teile) <= 3 or not all(t.isdigit() for t in teile):
        raise ValueError(f"Zeit „{text}“ ungültig")
    sekunden = 0
    for t in teile:
        sekunden = sekunden * 60 + int(t)
    return sekunden * 1000


def zehntel_km(wert) -> int:
    return round(float(wert) * 10)


def pruefe_wertung(datei: str, roh: dict, klassen: list[Klasse]) -> Wertung:
    fehler: list[str] = []
    name = str(roh.get("name") or "").strip()
    if not name:
        fehler.append("name fehlt")
    art = roh.get("art")
    if art not in ARTEN:
        fehler.append(f"art muss {' oder '.join(ARTEN)} sein")
    smin = smax = None
    if art == "pro_km":
        try:
            smin, smax = zehntel_km(roh["strecke_km_min"]), zehntel_km(roh["strecke_km_max"])
            if not 0 < smin <= smax:
                fehler.append("strecke_km_min/max ungültig")
        except (KeyError, TypeError, ValueError):
            fehler.append("strecke_km_min und strecke_km_max sind Pflicht bei art = pro_km")
    bekannte = {k.name for k in klassen}
    stufen: dict[str, Stufe] = {}
    for i, s in enumerate(roh.get("stufe") or [], start=1):
        klasse = s.get("klasse")
        wo = f"Stufe {i} ({klasse})"
        if klasse not in bekannte:
            fehler.append(f"{wo}: Klasse „{klasse}“ gibt es nicht in altersklassen.toml")
            continue
        if klasse in stufen:
            fehler.append(f"{wo}: Klasse doppelt")
            continue
        try:
            gold, silber, bronze = (zeit_zu_ms(s[x]) for x in ("gold", "silber", "bronze"))
        except (KeyError, ValueError) as e:
            fehler.append(f"{wo}: Zeit fehlt oder ungültig ({e})")
            continue
        if not gold < silber < bronze:
            fehler.append(f"{wo}: Reihenfolge muss gold < silber < bronze sein")
            continue
        strecke = None
        if art == "gesamtzeit":
            try:
                strecke = zehntel_km(s["strecke_km"])
                if strecke <= 0:
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                fehler.append(f"{wo}: strecke_km fehlt oder ungültig")
                continue
        stufen[klasse] = Stufe(klasse, gold, silber, bronze, strecke)
    if not stufen and not fehler:
        fehler.append("keine Stufen")
    if fehler:
        raise WertungsConfigFehler(f"{datei}: " + "; ".join(fehler))
    return Wertung(datei, name, art, stufen, smin, smax)


def lade_wertungen(verzeichnis: str, klassen: list[Klasse]) -> dict[str, Wertung]:
    ergebnis: dict[str, Wertung] = {}
    for pfad in sorted(Path(verzeichnis).glob("*.toml")):
        try:
            with open(pfad, "rb") as f:
                roh = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError) as e:
            raise WertungsConfigFehler(f"{pfad.name}: nicht lesbar ({e})") from e
        ergebnis[pfad.name] = pruefe_wertung(pfad.name, roh, klassen)
    return ergebnis


def stufe_berechnen(
    wertung: Wertung, klasse: str | None, fahrzeit_ms: int, strecke_zehntel_km: int
) -> str | None:
    """Beste erreichte Stufe oder None. Es zählt die auf Zehntel abgeschnittene Fahrzeit (A33)."""
    stufe = wertung.stufen.get(klasse) if klasse else None
    if stufe is None or fahrzeit_ms is None or not strecke_zehntel_km:
        return None
    zeit = fahrzeit_ms // 100 * 100
    if wertung.art == "gesamtzeit":
        if strecke_zehntel_km < stufe.strecke_zehntel_km:  # A34: mindestens die Stufenlänge
            return None
        erreicht = {x: zeit <= getattr(stufe, f"{x.lower()}_ms") for x in STUFEN}
    else:
        if (
            not wertung.strecke_min_zehntel_km
            <= strecke_zehntel_km
            <= wertung.strecke_max_zehntel_km
        ):
            return None
        # Zeit pro km <= Schwelle  ⇔  zeit * 10 <= Schwelle * Strecke[Zehntel-km] (ganzzahlig, exakt)
        erreicht = {
            x: zeit * 10 <= getattr(stufe, f"{x.lower()}_ms") * strecke_zehntel_km for x in STUFEN
        }
    return next((x for x in STUFEN if erreicht[x]), None)
