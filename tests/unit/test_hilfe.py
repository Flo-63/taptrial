"""Markdown-Umsetzer und Handbuch-Dateien."""

import pytest

from app.hilfe import BILDER_DIR, SEITEN, bilder_in, markdown_zu_html


def test_markdown_grundelemente():
    html = markdown_zu_html(
        "# Titel\n\nText mit **fett** und `code`\nzweite Zeile.\n\n- eins\n- zwei\n  weiter\n\n"
        "1. erst\n2. dann\n\n> Achtung <b>\n\n![Bild](bilder/x.png)\n\n```\na < b\n```\n"
        "[Start](START.md) [extern](https://example.org)"
    )
    assert "<h1>Titel</h1>" in html
    assert "<p>Text mit <strong>fett</strong> und <code>code</code> zweite Zeile.</p>" in html
    assert "<ul>\n<li>eins</li>\n<li>zwei weiter</li>\n</ul>" in html
    assert "<ol>\n<li>erst</li>\n<li>dann</li>\n</ol>" in html
    assert '<p class="hinweis">Achtung &lt;b&gt;</p>' in html
    assert '<img src="bilder/x.png" alt="Bild" loading="lazy">' in html
    assert "<pre><code>a &lt; b</code></pre>" in html
    assert '<a href="/hilfe/start">Start</a>' in html and 'href="https://example.org"' in html


def test_kein_html_einschleusbar():
    html = markdown_zu_html('<script>alert(1)</script> [x](javascript:"a")')
    assert "<script>" not in html and "&lt;script&gt;" in html


@pytest.mark.parametrize("name", list(SEITEN))
def test_handbuecher_vorhanden_und_bilder_existieren(name):
    bilder = bilder_in(name)
    assert bilder, f"{name}: mindestens ein Screenshot"
    for b in bilder:
        assert (BILDER_DIR / b).is_file(), b


def test_kurzanleitungen_behandeln_pflichtthemen():
    from app.hilfe import hilfe_markdown

    for name in ("start", "ziel", "rennleitung"):
        text = hilfe_markdown(name)
        for thema in ("Startbildschirm", "Netz", "Nach dem Rennen"):
            assert thema in text, (name, thema)
    for thema in ("Uhrzeitvergleich", "Doppeltipp", "zwei Fahrer gleichzeitig", "neu starten"):
        assert thema in hilfe_markdown("ziel"), thema
    admin = hilfe_markdown("admin")
    for thema in ("Anmelden", "CSV", "Leistungswertung", "QR", "Korrekturen", "vorläufig",
                  "offiziell", "Export", "Abschließen", "Anonymisieren", "löschen", "Typische Fehler",
                  "Assistent", "Vorschau", "Status & Datenschutz", "Drucken & Export", "Seitenleiste",
                  "Alle Klassen haben einen Eintrag"):  # fmt: skip
        assert thema in admin, thema
