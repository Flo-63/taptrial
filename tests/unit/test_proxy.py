"""Client-IP und Schema hinter dem Proxy."""

from types import SimpleNamespace

import pytest

from app.proxy import Netze, client_ip, ist_https


def anfrage(peer, xff=None, proto=None, schema="http"):
    kopf = {}
    if xff is not None:
        kopf["x-forwarded-for"] = xff
    if proto is not None:
        kopf["x-forwarded-proto"] = proto
    return SimpleNamespace(
        client=SimpleNamespace(host=peer) if peer else None,
        headers=kopf,
        url=SimpleNamespace(scheme=schema),
    )


STANDARD = Netze.aus_text("127.0.0.1,::1")
DOCKER = Netze.aus_text("127.0.0.1, ::1, 172.16.0.0/12")


@pytest.mark.parametrize(
    ("peer", "xff", "netze", "erwartet"),
    [
        ("127.0.0.1", "203.0.113.5", STANDARD, "203.0.113.5"),  # vom lokalen Proxy
        ("198.51.100.7", "203.0.113.5", STANDARD, "198.51.100.7"),  # fremd: Header ignoriert
        ("127.0.0.1", None, STANDARD, "127.0.0.1"),
        # Kette: rechteste nicht vertrauenswürdige Adresse gewinnt, vorne Gefälschtes zählt nicht
        ("127.0.0.1", "1.2.3.4, 203.0.113.5", STANDARD, "203.0.113.5"),
        ("172.18.0.3", "1.2.3.4, 203.0.113.5, 172.18.0.1", DOCKER, "203.0.113.5"),
        ("172.18.0.3", "203.0.113.5", STANDARD, "172.18.0.3"),  # Docker-Netz nicht konfiguriert
        ("127.0.0.1", "kaputt, 203.0.113.5", STANDARD, "203.0.113.5"),
        ("127.0.0.1", "kaputt", STANDARD, "127.0.0.1"),
        ("::1", "2001:db8::1", STANDARD, "2001:db8::1"),
        ("testclient", "203.0.113.5", STANDARD, "testclient"),
        (None, "203.0.113.5", STANDARD, "?"),
    ],
)
def test_client_ip(peer, xff, netze, erwartet):
    assert client_ip(anfrage(peer, xff), netze) == erwartet


def test_https_nur_vom_vertrauten_proxy():
    assert ist_https(anfrage("127.0.0.1", proto="https"), STANDARD)
    assert not ist_https(anfrage("198.51.100.7", proto="https"), STANDARD)
    assert ist_https(anfrage("198.51.100.7", schema="https"), STANDARD)
    assert not ist_https(anfrage("127.0.0.1"), STANDARD)


def test_netze_aus_text_ungueltig():
    with pytest.raises(ValueError, match="TAPTRIAL_VERTRAUTE_PROXYS"):
        Netze.aus_text("127.0.0.1, kein-netz")
