"""Einstellungen: Allgemein, Klassen & Wertung, Strecken, Ziel/Rennleitung."""

from tests.api import test_admin
from tests.api.test_admin import T0, geraet, post

admin, v, keine_sperre = test_admin.admin, test_admin.v, test_admin.keine_sperre  # Fixtures


def test_reiter_mit_eigener_adresse_formular_und_hilfe(admin, v):
    """Einstellungen mit Reitern – je Reiter eigene Adresse, Speichern und Hilfe-Anker."""
    import re

    reiter = {"": ("allgemein", ["allgemein"], ["stammdaten"], "einstellungen"),
              "/klassen": ("klassen", ["regeln"], ["regeln"], "klassen-wertung"),
              "/zielmonitor": ("zielmonitor", ["oeffentlichkeit", "zielmonitor"], ["oeffentlichkeit", "zielmonitor"],
                                   "zielmonitor-ergebnisse")}  # fmt: skip
    for pfad, (key, bereiche, formulare, anker) in reiter.items():
        html = admin.get(f"/admin/v/{v.id}/einstellungen{pfad}").text
        assert 'id="einstellungen-reiter"' in html and html.count('class="reiter-link"') == 3
        assert 'data-reiter="strecken"' not in html  # Eigene Seite
        assert re.search(rf'data-reiter="{key}" aria-current=page', html), key
        for bereich in bereiche:
            assert f'id="{bereich}"' in html, (key, bereich)
        for formular in formulare:
            assert f'action="/admin/v/{v.id}/{formular}"' in html, (key, formular)
        assert f'#{anker}"' in html  # Hilfe-Button springt zum Abschnitt
        assert html.count('class="btn btn-primary"') >= 1
    assert admin.get(f"/admin/v/{v.id}/einstellungen/unbekannt").status_code == 404
    assert "rennleitung-modus" not in admin.get(f"/admin/v/{v.id}/einstellungen").text


def test_strecken_und_messpunkte_eigene_seiten(admin, v):
    """Seitenleiste „Strecken“ und „Messpunkte“ zwischen Einstellungen und
    Teilnehmer; die alten Adressen (auch „/orte“) leiten weiter."""
    import re

    html = admin.get(f"/admin/v/{v.id}").text
    nav = re.findall(r'data-seite="([a-z]+)"', html)
    assert nav[nav.index("einstellungen") : nav.index("teilnehmer") + 1] == [
        "einstellungen",
        "strecken",
        "messpunkte",
        "teilnehmer",
    ]
    st = admin.get(f"/admin/v/{v.id}/strecken").text
    assert 'id="strecken-liste"' in st and f'action="/admin/v/{v.id}/strecken/neu"' in st
    assert (
        re.search(r'data-seite="strecken"\s+aria-current="page"', st)
        and "einstellungen-reiter" not in st
    )
    r = admin.get(f"/admin/v/{v.id}/orte", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/admin/v/{v.id}/messpunkte"
    mp = admin.get(f"/admin/v/{v.id}/messpunkte").text
    assert 'id="messpunkte"' in mp and f'action="/admin/v/{v.id}/messpunkte"' in mp
    assert re.search(r'data-seite="messpunkte"\s+aria-current="page"', mp)
    assert "<h1>Messpunkte</h1>" in mp
    for wert, art in (
        ("start", "Start"),
        ("punkt", "Zwischenzeit"),
        ("ziel", "Ziel"),
    ):
        assert re.search(rf'<option value="{wert}"[^>]*>{art}</option>', mp), art
    assert "Startort" not in mp and "Zielort" not in mp
    r = admin.get(f"/admin/v/{v.id}/einstellungen/strecken", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == f"/admin/v/{v.id}/strecken"
    assert 'class="stapel-eng rennleitung-modus"' in admin.get(f"/admin/v/{v.id}/geraete").text
    oe = admin.get(f"/admin/v/{v.id}/einstellungen/zielmonitor").text
    assert 'id="oeffentlich-hinweis"' in oe and "öffentlich" in oe and 'id="monitor-vorschau"' in oe


def test_option_ziel_rennleitung_mit_warnung_nach_erster_zeit(admin, v):
    """Die Option steht auf der Karte des Ziels unter „Geräte“."""
    import re

    html = admin.get(f"/admin/v/{v.id}/geraete").text
    karte = re.search(
        r'<section class="card geraet" data-titel="Ziel">.*?</section>', html, re.DOTALL
    ).group(0)
    assert (
        "Ziel und Rennleitung auf einem Gerät umschaltbar" in karte and "schon Zeiten" not in karte
    )
    geraet(admin, v, "START", startnummer=1, zeit_ms=T0)
    html = admin.get(f"/admin/v/{v.id}/geraete").text
    assert "schon Zeiten" in html and 'name="bestaetigung"' in html
    r = post(admin, f"/admin/v/{v.id}/rennleitung", zusammen="")
    assert "bestätigen" in r.text.lower() or "Bestätigung" in r.text
    r = post(admin, f"/admin/v/{v.id}/rennleitung", zusammen="", bestaetigung="1")
    assert r.url.path == f"/admin/v/{v.id}/geraete" and "getrennt" in r.text


def test_gesperrt_nennt_grund(admin, v):
    geraet(admin, v, "START", startnummer=1, zeit_ms=T0)
    html = admin.get(f"/admin/v/{v.id}/einstellungen/klassen").text
    assert 'id="wahl-klassensatz" disabled' in html and "Gesperrt: es gibt bereits Zeiten." in html
    post(admin, f"/admin/v/{v.id}/abschliessen", bestaetigung="1", name="Kottenforst")
    html = admin.get(f"/admin/v/{v.id}/geraete").text
    assert (
        "Gesperrt: Die Gerätelinks sind ungültig, weil die Veranstaltung abgeschlossen ist." in html
    )
