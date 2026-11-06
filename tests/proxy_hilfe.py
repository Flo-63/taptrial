"""Anmeldedienst-Attrappe für den Modus `proxy`: eigenes Schlüsselpaar, JWKS als Datei, Tokens und
Kopfzeilen wie von Authentik."""

import json
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

AUSSTELLER = "https://anmeldung.beispiel.test/application/o/taptrial/"
KID = "test-schluessel"
ZIELGRUPPE = "taptrial-client"


def schluesselpaar():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@dataclass
class Anmeldedienst:
    schluessel: object
    jwks_url: str
    aussteller: str = AUSSTELLER

    server: ThreadingHTTPServer | None = None

    @classmethod
    def starten(cls) -> "Anmeldedienst":
        """Schlüsselpaar und ein kleiner HTTP-Server, der die öffentlichen Schlüssel (JWKS) liefert."""
        privat = schluesselpaar()
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(privat.public_key()))
        inhalt = json.dumps({"keys": [{**jwk, "kid": KID, "use": "sig", "alg": "RS256"}]}).encode()

        class Jwks(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(inhalt)

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Jwks)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return cls(privat, f"http://127.0.0.1:{server.server_port}/jwks/", server=server)

    def beenden(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()

    def token(self, uid: str, benutzername: str, *, schluessel=None, ablauf_s: int = 300,
              aussteller: str | None = None, **felder) -> str:  # fmt: skip
        jetzt = int(time.time())
        daten = {"iss": aussteller or self.aussteller, "sub": uid, "iat": jetzt, "aud": ZIELGRUPPE,
                 "exp": jetzt + ablauf_s, "preferred_username": benutzername, **felder}  # fmt: skip
        return jwt.encode(daten, schluessel or self.schluessel, algorithm="RS256",
                          headers={"kid": KID})  # fmt: skip

    def kopf(self, uid: str, benutzername: str, *, name: str = "", email: str = "",
             token: str | None = None, **felder) -> dict[str, str]:  # fmt: skip
        """Kopfzeilen, wie sie der Proxy nach erfolgreicher Anmeldung weiterreicht."""
        return {
            "X-Authentik-Uid": uid,
            "X-Authentik-Username": benutzername,
            "X-Authentik-Name": name,
            "X-Authentik-Email": email,
            "X-Authentik-Jwt": token
            or self.token(uid, benutzername, name=name, email=email, **felder),
        }


def einstellungen_proxy(dienst: Anmeldedienst, **mehr) -> dict[str, str]:
    return {"anmeldung": "proxy", "proxy_jwks_url": dienst.jwks_url,
            "proxy_jwt_issuer": dienst.aussteller, "proxy_jwt_audience": ZIELGRUPPE, **mehr}  # fmt: skip
