"""Ruhiger Hinweis auf die Erlaubnis nach § 29 StVO im Assistenten (Schritt Grunddaten),
gleicher Satz im README und im Betriebshandbuch."""

from pathlib import Path

import pytest

from app.marke import HINWEIS_GENEHMIGUNG
from tests.api.test_einrichtung import admin  # noqa: F401 (Fixture)

PROJEKT = Path(__file__).resolve().parents[2]
SATZ = ("Hinweis: Auf öffentlichen Straßen ist für Veranstaltungen mit Zeitnahme in der Regel eine "
        "Erlaubnis nach § 29 StVO nötig, auf anderen Flächen die Zustimmung des Eigentümers.")  # fmt: skip


def test_hinweis_im_assistenten_ruhig(admin):  # noqa: F811
    assert HINWEIS_GENEHMIGUNG == SATZ
    html = admin.get("/admin/einrichtung/neu").text
    assert f'<p class="hint" id="hinweis-genehmigung">{SATZ}</p>' in html  # kein alert/Warnung


@pytest.mark.parametrize("datei", ["README.md", "docs/BETRIEB.md"])
def test_gleicher_satz_in_der_doku(datei):
    pfad = PROJEKT / datei
    if not pfad.exists():
        pytest.skip(f"{datei} fehlt (Export ohne diese Datei)")
    assert SATZ in pfad.read_text(encoding="utf-8")
