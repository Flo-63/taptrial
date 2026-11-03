"""Client-Adresse und Schema hinter Reverse-Proxys (z. B. Caddy oder eine Kette aus mehreren Proxys).

`X-Forwarded-For`/`X-Forwarded-Proto` werden nur ausgewertet, wenn die Anfrage direkt von einem
vertrauenswürdigen Proxy kommt (Standard 127.0.0.1/::1, erweiterbar per TAPTRIAL_VERTRAUTE_PROXYS).
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
                    f"TAPTRIAL_VERTRAUTE_PROXYS: „{teil}“ ist keine IP-Adresse/kein Netz"
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


def absender(ip: str) -> str:
    """Schlüssel für die Sperre nach Fehlversuchen: IPv6 je /64 (ein Anschluss hat alle Adressen
    davon), IPv4 je Adresse."""
    try:
        adresse = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if adresse.version == 4:
        return str(adresse)
    if adresse.ipv4_mapped is not None:
        return str(adresse.ipv4_mapped)
    return f"{ipaddress.ip_network(f'{adresse}/64', strict=False).network_address}/64"


def ist_https(request, netze: Netze) -> bool:
    if request.url.scheme == "https":
        return True
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return proto == "https" and netze.enthaelt(_peer(request))


def basis_url(request, netze: Netze) -> str:
    """Öffentliche Basis-URL (für Gerätelinks/QR-Codes), https auch hinter dem Proxy."""
    schema = "https" if ist_https(request, netze) else request.url.scheme
    return f"{schema}://{request.url.netloc}"
