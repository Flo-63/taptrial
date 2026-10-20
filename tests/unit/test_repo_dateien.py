"""Dateien für Außenstehende – README, LICENSE (PolyForm Noncommercial 1.0.0 im
Originaltext), THIRD_PARTY_LICENSES (alle Laufzeit-Abhängigkeiten aus uv.lock), SECURITY,
CONTRIBUTING, Issue-Vorlagen, ROADMAP mit „Lauf, Wellen- und Massenstart“."""

import hashlib
import re
import tomllib
from pathlib import Path

import yaml

PROJEKT = Path(__file__).resolve().parents[2]
# sha256 des Originaltexts (polyform-licenses, Tag 1.0.0, PolyForm-Noncommercial-1.0.0.md)
POLYFORM_SHA256 = "c0ea4a896d2c8c394b29f9427589996db826cd501c512279ff0ed3ef48fabbe5"


def lesen(name: str) -> str:
    return (PROJEKT / name).read_text(encoding="utf-8")


def test_readme_inhalt():
    r = lesen("README.md")
    assert r.startswith("# TapTrial – Zeitnahme per Handy")
    bilder = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", r)
    assert len(bilder) >= 3 and all((PROJEKT / b).is_file() for b in bilder), bilder
    for abschnitt in ("Was TapTrial kann", "bewusst nicht", "Schnellstart", "docker-compose.caddy.yml",
                      "Selbst bauen", "Konfiguration", "Sicherung und Wiederherstellung", "Update",
                      "python -m app.verwaltung demo", "§ 29 StVO", "PolyForm Noncommercial",
                      "ohne Gewähr", "keine Supportzusage", "Pull Requests nehmen wir derzeit nicht an"):  # fmt: skip
        assert abschnitt in r, abschnitt
    assert "Ohne HTTPS gibt es keinen Offline-Betrieb" in r
    for name in ("TAPTRIAL_TOKEN_ADMIN", "TAPTRIAL_DOMAIN", "TAPTRIAL_VERTRAUTE_PROXYS"):
        assert f"`{name}`" in r


def test_lizenz_originaltext():
    text = lesen("LICENSE")
    notice, _, original = text.partition("\n\n")
    assert notice.startswith("Required Notice: Copyright 2026 ")
    assert hashlib.sha256(original.encode("utf-8")).hexdigest() == POLYFORM_SHA256


def laufzeit_pakete() -> set[str]:
    lock = tomllib.loads(lesen("uv.lock"))
    pakete = {p["name"]: p for p in lock["package"]}
    offen = [
        (d["name"], tuple(d.get("extra", []))) for d in pakete["rcb-zeitfahren"]["dependencies"]
    ]
    gesehen = set()
    while offen:
        name, extras = offen.pop()
        if (name, extras) in gesehen:
            continue
        gesehen.add((name, extras))
        p = pakete[name]
        deps = list(p.get("dependencies", []))
        for e in extras:
            deps += p.get("optional-dependencies", {}).get(e, [])
        offen += [(d["name"], tuple(d.get("extra", []))) for d in deps]
    return {n for n, _ in gesehen}


def test_drittlizenzen_vollstaendig():
    text = lesen("THIRD_PARTY_LICENSES.md")
    zeilen = {
        m.group(1): m.group(2)
        for m in re.finditer(r"^\| ([a-z0-9-]+) \| [^|]+ \| ([^|]*) \|", text, re.MULTILINE)
    }
    fehlend = laufzeit_pakete() - set(zeilen)
    assert not fehlend, fehlend
    assert all(lizenz.strip() for lizenz in zeilen.values())
    assert "IBM Plex" in text and "Open Font License" in text


def test_security_contributing_vorlagen():
    assert "Report a vulnerability" in lesen("SECURITY.md")
    assert "Pull Requests" in lesen("CONTRIBUTING.md") and "nicht" in lesen("CONTRIBUTING.md")
    vorlagen = PROJEKT / ".github" / "ISSUE_TEMPLATE"
    for name in ("fehler.yml", "wunsch.yml"):
        v = yaml.safe_load((vorlagen / name).read_text(encoding="utf-8"))
        assert v["name"] and v["body"], name
    assert yaml.safe_load((vorlagen / "config.yml").read_text())["blank_issues_enabled"] is False


def test_roadmap():
    r = lesen("ROADMAP.md")
    assert "## Lauf, Wellen- und Massenstart" in r
    for stichwort in ("Bruttozeit", "erst Zeiten, dann Nummern", "Nummernfeld", "Rundenzählung"):
        assert stichwort in r
    for intern in ("jetzt", "Branch", "flox", "Flo ", "Entscheidung", "A1 ", "B1 "):
        assert intern not in r, intern  # keine internen Notizen
