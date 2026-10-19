"""Hilfe-Seiten /hilfe/<rolle> aus den Handbüchern in docs/handbuch/ (eine Quelle für Repo und App).

Ein kleiner Markdown-Umsetzer für genau den Umfang, den die Handbücher nutzen (Überschriften,
Absätze, Listen, Fettdruck, Code, Links, Bilder, Hinweise mit „>“, Codeblöcke) – ohne zusätzliche
Abhängigkeit. Die Kurzanleitungen sind für eine DIN-A4-Seite gesetzt (Druck-CSS in hilfe.html).
"""

import html
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from app.einstellungen import PROJEKT_DIR
from app.seiten import templates

HANDBUCH_DIR = PROJEKT_DIR / "docs" / "handbuch"
BILDER_DIR = HANDBUCH_DIR / "bilder"
SEITEN = {
    "start": ("START.md", "Start", True),
    "ziel": ("ZIEL.md", "Ziel – Erfassen", True),
    "rennleitung": ("RENNLEITUNG.md", "Rennleitung", True),
    "posten": ("POSTEN.md", "Posten (Zwischenzeit)", True),
    "monitor": ("MONITOR.md", "Zielmonitor", True),
    "admin": ("ADMIN.md", "Admin-Handbuch", False),
}  # Name → (Datei, Titel, Kurzanleitung für eine A4-Seite)
OFFLINE = ("start", "ziel", "rennleitung", "posten")
# Frühere Sprungziele, die weiter funktionieren (alt → neu), z. B. Segmente 60
ANKER_ALIAS = {"oeffentlichkeit": "zielmonitor-ergebnisse"}  # vom Service Worker vorab gespeichert

router = APIRouter(prefix="/hilfe", include_in_schema=False)


def _inline(text: str) -> str:
    text = html.escape(text, quote=False)
    text = re.sub(
        r"!\[([^\]]*)\]\(([^)\s]+)\)",
        lambda m: f'<img src="{html.escape(m[2])}" alt="{html.escape(m[1])}" loading="lazy">',
        text,
    )
    text = re.sub(
        r"\[([^\]]+)\]\(([^)\s]+)\)",
        lambda m: f'<a href="{html.escape(_link(m[2]))}">{m[1]}</a>',
        text,
    )
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)


def _link(ziel: str) -> str:
    """Verweise zwischen den Handbüchern (START.md …) auf die Hilfe-Seiten umbiegen."""
    for name, (datei, _, _) in SEITEN.items():
        if ziel == datei:
            return f"/hilfe/{name}"
    return ziel


def markdown_zu_html(quelltext: str) -> str:
    aus: list[str] = []
    liste: str | None = None
    absatz: list[str] = []
    code: list[str] | None = None

    def absatz_beenden():
        if absatz:
            aus.append(f"<p>{_inline(' '.join(absatz))}</p>")
            absatz.clear()

    def liste_beenden():
        nonlocal liste
        if liste:
            aus.append(f"</{liste}>")
            liste = None

    for zeile in quelltext.splitlines():
        if code is not None:
            if zeile.startswith("```"):
                aus.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
                code = None
            else:
                code.append(zeile)
            continue
        if zeile.startswith("```"):
            absatz_beenden()
            liste_beenden()
            code = []
            continue
        if not zeile.strip():
            absatz_beenden()
            liste_beenden()
            continue
        if m := re.match(r"^(#{1,4})\s+(.*)$", zeile):  # bis h4 (Unterabschnitte im Admin-Handbuch)
            absatz_beenden()
            liste_beenden()
            n = len(m[1])
            text, anker = m[2], ""
            if a := re.search(r"\s*\{#([a-z0-9-]+)\}$", text):  # Sprungziel „## Titel {#anker}“
                text, anker = text[: a.start()], f' id="{a[1]}"'
            alias = "".join(f'<span id="{alt}"></span>' for alt, neu in ANKER_ALIAS.items()
                            if anker == f' id="{neu}"')  # fmt: skip
            aus.append(f"{alias}<h{n}{anker}>{_inline(text)}</h{n}>")
            continue
        if m := re.match(r"^\s*(?:([-*])|(\d+)\.)\s+(.*)$", zeile):
            absatz_beenden()
            art = "ul" if m[1] else "ol"
            if liste != art:
                liste_beenden()
                aus.append(f"<{art}>")
                liste = art
            aus.append(f"<li>{_inline(m[3])}</li>")
            continue
        if zeile.startswith(">"):
            absatz_beenden()
            liste_beenden()
            aus.append(f'<p class="hinweis">{_inline(zeile.lstrip("> ").strip())}</p>')
            continue
        if liste and zeile.startswith("  "):  # Fortsetzung eines Listenpunkts
            aus[-1] = aus[-1][: -len("</li>")] + " " + _inline(zeile.strip()) + "</li>"
            continue
        liste_beenden()
        absatz.append(zeile.strip())
    absatz_beenden()
    liste_beenden()
    return "\n".join(aus)


def bilder_in(name: str) -> list[str]:
    """Bilddateien, auf die ein Handbuch verweist (für Precache und Tests)."""
    datei = HANDBUCH_DIR / SEITEN[name][0]
    return re.findall(r"!\[[^\]]*\]\(bilder/([^)\s]+)\)", datei.read_text(encoding="utf-8"))


@router.get("/bilder/{datei}")
def bild(datei: str):
    pfad = (BILDER_DIR / datei).resolve()
    if pfad.parent != BILDER_DIR.resolve() or pfad.suffix != ".png" or not pfad.is_file():
        raise HTTPException(404)
    return FileResponse(pfad, media_type="image/png")


@router.get("/{name}", response_class=HTMLResponse)
def hilfe(request: Request, name: str, zurueck: str = ""):
    if name not in SEITEN:
        raise HTTPException(404, "Hilfe nicht gefunden")
    datei, titel, kurz = SEITEN[name]
    inhalt = markdown_zu_html((HANDBUCH_DIR / datei).read_text(encoding="utf-8"))
    # Vom Admin aufgerufen: fixierte Kopfzeile mit „Zurück zum Admin“ (nur eigene Adressen)
    admin = zurueck if zurueck.startswith("/admin") and "//" not in zurueck else ""
    return templates.TemplateResponse(
        request,
        "hilfe.html",
        {"titel": titel, "inhalt": inhalt, "kurz": kurz, "seiten": SEITEN, "name": name,
         "zurueck": admin},
    )  # fmt: skip


def hilfe_markdown(name: str) -> str:
    return Path(HANDBUCH_DIR / SEITEN[name][0]).read_text(encoding="utf-8")
