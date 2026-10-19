"""Aufräumen nach der Neuordnung (ADMIN-REVIEW Schritt 9): ein Baukasten, keine Reste."""

import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"
VORLAGEN = APP / "templates"
GERAETE = {"geraet_basis.html", "start.html", "ziel.html"}  # Geräteseiten folgen später (R6)
ADMIN = sorted(
    p for p in VORLAGEN.glob("*.html") if p.name.startswith(("admin_", "_admin", "_strecken"))
)
FARBE = re.compile(r"#[0-9a-fA-F]{3,8}\b(?![\w-])|\brgba?\(|\bhsla?\(")
ALT = re.compile(r'class="[^"]*(?<![\w-])(knopf|knopf-zweit|knopf-rot|knopf-klein|knopf-klein-hell|karte|tabelle|'
                 r'tabelle-box|unterreiter|schalter|kennzahl|kacheln-admin|knoepfe|warnung-text|klein)(?![\w-])')  # fmt: skip
ERLAUBT = ("btn", "nav-item", "top-btn", "tbl-link", "marke", "kpi", "schritt-link", "reiter-link")


def ohne_kommentare(text: str) -> str:
    return re.sub(r"\{#.*?#\}", "", text, flags=re.DOTALL)


@pytest.mark.parametrize(
    "vorlage", sorted(p.name for p in VORLAGEN.glob("*.html") if p.name not in GERAETE)
)
def test_keine_farbwerte_in_vorlagen(vorlage):
    text = ohne_kommentare((VORLAGEN / vorlage).read_text(encoding="utf-8"))
    text = re.sub(r"<svg.*?</svg>", "", text, flags=re.DOTALL)
    assert not FARBE.findall(text), FARBE.findall(text)


def test_keine_farbwerte_im_admin_skript():
    for datei in ("admin.js", "admin_import.js"):
        assert not FARBE.findall((APP / "static/js" / datei).read_text(encoding="utf-8")), datei


@pytest.mark.parametrize("vorlage", [p.name for p in ADMIN])
def test_keine_alten_klassen(vorlage):
    text = (VORLAGEN / vorlage).read_text(encoding="utf-8")
    assert not ALT.findall(text), ALT.findall(text)


@pytest.mark.parametrize("vorlage", [p.name for p in ADMIN])
def test_keine_textlinks_fuer_aktionen(vorlage):
    """Aktionen und Seitenaufrufe sind Buttons bzw. Navigationspunkte, keine Textlinks."""
    text = (VORLAGEN / vorlage).read_text(encoding="utf-8")
    for tag in re.findall(r"<a\s[^>]*>", text):
        klasse = re.search(r'class="([^"]*)"', tag)
        assert klasse and any(k in klasse.group(1).split() for k in ERLAUBT), tag


def test_admin_css_nur_layout():
    text = (APP / "static/css/admin.css").read_text(encoding="utf-8")
    for alt in (".knopf", ".karte", ".tabelle", ".meldung {", ".unterreiter", ".kennzahl", ":root"):
        assert alt not in text, alt


# Seiten ohne Bearbeiten-Formulare im Sinne von „Abbrechen“: Anmeldung, direkte Aktionen mit Notiz
# bzw. Bestätigungsdialog (Fälle in „Aufgaben“, offiziell/zurücknehmen, Token, Gefahrenzone).
OHNE_ABBRECHEN = {"admin_anmelden.html", "admin_aufgaben.html", "admin_ergebnis.html", "admin_geraete.html",
                  "admin_status.html", "admin_basis.html", "admin_saetze.html", "admin_geraetedatei.html"}  # fmt: skip


@pytest.mark.parametrize("vorlage", sorted(p.name for p in VORLAGEN.glob("*.html")
                                           if p.name.startswith(("admin", "_strecken", "_zielmonitor"))))  # fmt: skip
def test_formulare_haben_abbrechen(vorlage):
    """Segmente 21: Jedes Formular zum Anlegen oder Bearbeiten hat „Abbrechen“ (ohne Speichern zurück)."""
    if vorlage in OHNE_ABBRECHEN:
        return
    text = (VORLAGEN / vorlage).read_text(encoding="utf-8")
    for form in re.findall(r"<form\b[^>]*>.*?</form>", text, re.DOTALL):
        if 'type="submit"' not in form or 'method="get"' in form:
            continue
        if re.search(
            r'class="btn btn-primary', form
        ):  # Speichern/Weiter; reine Aktionsknöpfe nicht
            assert "abbrechen(" in form or "zuklappen()" in form or ">Abbrechen<" in form, (
                vorlage,
                form[:160],
            )
    for aufklapp in re.findall(r'<details class="aktion[^"]*"[^>]*>.*?</details>', text, re.DOTALL):
        if "<form" in aufklapp:  # Aufklapp-Formulare: „Abbrechen“ klappt zu
            assert "zuklappen()" in aufklapp, (vorlage, aufklapp[:160])


@pytest.mark.parametrize("vorlage, zurueck", [("admin_strecke.html", "zurueck-strecken"),
                                              ("admin_fahrer.html", "zurueck-teilnehmer")])  # fmt: skip
def test_detailseiten_haben_oben_zurueck(vorlage, zurueck):
    assert f'id="{zurueck}"' in (VORLAGEN / vorlage).read_text(encoding="utf-8")
