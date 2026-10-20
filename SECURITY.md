# Sicherheit

## Lücke melden

Bitte Sicherheitslücken **nicht** als öffentliches Issue melden, sondern vertraulich über GitHub:
Reiter **„Security“** → **„Report a vulnerability“** (Private Vulnerability Reporting) in diesem Repository.

Hilfreich sind: betroffene Version (`/version`), Schritte zum Nachvollziehen, mögliche Auswirkungen. Wir bestätigen den Eingang so bald wie möglich; eine feste Antwortzeit können wir als Freizeitprojekt nicht zusagen.

## Unterstützte Versionen

Sicherheitskorrekturen gibt es nur für die jeweils neueste Version.

## Betrieb

- Admin-Passphrase (`TAPTRIAL_TOKEN_ADMIN`) lang und zufällig wählen, nie weitergeben; `.env` nicht veröffentlichen.
- Nur über HTTPS betreiben (z. B. `docker-compose.caddy.yml`).
- Gerätelinks enthalten Zugangstoken: nicht aushängen; bei Verlust im Admin unter „Geräte“ „Zugang sperren“.
