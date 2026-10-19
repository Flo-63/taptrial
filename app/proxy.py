"""Client-Adresse und Schema hinter Reverse-Proxys (z. B. Caddy oder eine Kette aus mehreren Proxys).

`X-Forwarded-For`/`X-Forwarded-Proto` werden nur ausgewertet, wenn die Anfrage direkt von einem
vertrauenswürdigen Proxy kommt (Standard 127.0.0.1/::1, erweiterbar per RCB_VERTRAUTE_PROXYS).
In der Kette zählt die rechteste nicht vertrauenswürdige Adresse – was ein Client selbst vorne
einträgt, kann die Erkennung nicht beeinflussen.
"""

import ipaddress
from dataclasses import dataclass

STANDARD = "127.0.0.1,::1"


@dataclass(frozen=True)
class Netze:
    netze: tuple

    @classmethod
    def aus_text(cls, text: str) -> "Netze":
        netze = []
        for teil in (t.strip() for t in (text or "").split(",")):
            if not teil:
                continue
            try:
                netze.append(ipaddress.ip_network(teil, strict=False))
            except ValueError as e:
                raise ValueError(
                    f"RCB_VERTRAUTE_PROXYS: „{teil}“ ist keine IP-Adresse/kein Netz"
                ) from e
        return cls(tuple(netze))

    def enthaelt(self, adresse: str | None) -> bool:
        try:
            ip = ipaddress.ip_address((adresse or "").strip())
        except ValueError:
            return False
        return any(ip.version == n.version and ip in n for n in self.netze)


def _peer(request) -> str | None:
    return request.client.host if request.client else None


def client_ip(request, netze: Netze) -> str:
    peer = _peer(request)
    if peer is None:
        return "?"
    kette = request.headers.get("x-forwarded-for")
    if not kette or not netze.enthaelt(peer):
        return peer
    for eintrag in reversed([e.strip() for e in kette.split(",")]):
        try:
            ipaddress.ip_address(eintrag)
        except ValueError:
            continue
        if not netze.enthaelt(eintrag):
            return eintrag
    return peer


def ist_https(request, netze: Netze) -> bool:
    if request.url.scheme == "https":
        return True
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return proto == "https" and netze.enthaelt(_peer(request))


def basis_url(request, netze: Netze) -> str:
    """Öffentliche Basis-URL (für Gerätelinks/QR-Codes), https auch hinter dem Proxy."""
    schema = "https" if ist_https(request, netze) else request.url.scheme
    return f"{schema}://{request.url.netloc}"
