"""Druckansichten im Browser: Start- und Zielprotokoll."""

import re

import pytest

from app.simulation import rennplan
from tests.e2e.conftest import TOKENS


@pytest.fixture
def teilnehmer_liste():
    return rennplan().fahrer  # 50 Fahrer


@pytest.fixture
def admin(browser, server):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, service_workers="block")
    page = ctx.new_page()
    page.goto(f"{server.url}/admin")
    page.fill("input[name=token]", TOKENS["admin"])
    page.click("#anmelden button")
    yield page
    ctx.close()


def seiten(pdf: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page[^s]", pdf))


def test_papierprotokoll_druckt_a4(admin, server):
    admin.goto(f"{server.url}/admin/v/1/papierprotokoll")
    assert admin.locator("tr.fahrer").count() == 50 and admin.locator("tr.leer").count() == 10
    pdf = admin.pdf(prefer_css_page_size=True, print_background=True)
    assert 2 <= seiten(pdf) <= 3  # 60 Zeilen auf A4 hochkant
    breite = admin.locator("th:has-text('Uhrzeit Start')").bounding_box()["width"]
    assert breite >= 100  # Platz für hh:mm:ss,z von Hand


def test_urkunden_eine_je_seite_a4_quer(admin, server):
    import uuid

    plan = rennplan()
    for nr in (1, 2, 3):
        t = plan.start_ms[nr]
        server.senden([{"uuid": str(uuid.uuid4()), "art": "START", "startnummer": nr, "zeit_ms": t,
                        "erstellt_ms": t, "geraet": "s"}], rolle="start")  # fmt: skip
        z = plan.ziel_ms[nr]
        server.senden([{"uuid": str(uuid.uuid4()), "art": "ZIEL", "startnummer": nr, "zeit_ms": z,
                        "erstellt_ms": z, "geraet": "z"}], rolle="ziel")  # fmt: skip
    admin.goto(f"{server.url}/admin/v/1/urkunden")
    assert admin.locator("section.urkunde").count() == 3
    pdf = admin.pdf(prefer_css_page_size=True, print_background=True)
    assert seiten(pdf) == 3
    breite, hoehe = (
        float(x) for x in re.search(rb"/MediaBox\s*\[\s*0 0 ([\d.]+) ([\d.]+)", pdf).groups()
    )
    assert breite > hoehe  # quer


def test_druckleiste_zurueck_im_selben_tab(admin, server):
    admin.click("#veranstaltungen a")
    admin.click(".sl-nav a[data-seite=drucken]")
    admin.click("#papierprotokoll-link")  # öffnet im selben Tab
    assert admin.url.endswith("/papierprotokoll?zurueck=/admin/v/1/drucken")
    leiste = admin.locator(".druckleiste")
    assert leiste.is_visible() and admin.locator("#drucken").is_visible()
    admin.emulate_media(media="print")
    assert leiste.is_hidden()  # beim Drucken ausgeblendet
    admin.emulate_media(media="screen")
    admin.click("#zurueck-admin")
    assert admin.url.endswith("/admin/v/1/drucken")
    admin.click("#geraetelinks-link")
    assert admin.locator(".feld svg").count() == 4
