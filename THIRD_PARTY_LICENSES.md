# Fremde Bestandteile und ihre Lizenzen

TapTrial selbst steht unter der PolyForm Noncommercial License 1.0.0 (siehe [LICENSE](LICENSE)). Diese fremden Bestandteile gehören zur App bzw. zum Docker-Image und behalten ihre eigenen Lizenzen.

## Schrift

| Bestandteil | Lizenz | Quelle |
|---|---|---|
| IBM Plex Sans und IBM Plex Mono (Dateien unter `app/static/fonts/`) | SIL Open Font License 1.1 (Text: `app/static/fonts/OFL.txt`) | https://github.com/IBM/plex |

## Python-Abhängigkeiten (Laufzeit)

Stand `uv.lock` (Version 0.1.0); alle über PyPI, im Docker-Image enthalten.

| Paket | Version | Lizenz | Quelle |
|---|---|---|---|
| annotated-doc | 0.0.5 | MIT | https://github.com/fastapi/annotated-doc |
| annotated-types | 0.8.0 | MIT | https://github.com/annotated-types/annotated-types |
| anyio | 4.15.1 | MIT | https://github.com/agronholm/anyio |
| click | 8.5.0 | BSD-3-Clause | https://github.com/pallets/click/ |
| fastapi | 0.142.2 | MIT | https://github.com/fastapi/fastapi |
| h11 | 0.16.0 | MIT | https://github.com/python-hyper/h11 |
| httptools | 0.8.0 | MIT | https://github.com/MagicStack/httptools |
| idna | 3.20 | BSD-3-Clause | https://github.com/kjd/idna |
| jinja2 | 3.1.6 | BSD-3-Clause | https://github.com/pallets/jinja/ |
| markupsafe | 3.0.3 | BSD-3-Clause | https://github.com/pallets/markupsafe/ |
| opentelemetry-api | 1.45.0 | Apache-2.0 | https://github.com/open-telemetry/opentelemetry-python |
| pydantic | 2.13.5 | MIT | https://github.com/pydantic/pydantic |
| pydantic-core | 2.46.5 | MIT | https://github.com/pydantic/pydantic/tree/main/pydantic-core |
| python-dotenv | 1.2.4 | BSD-3-Clause | https://github.com/theskumar/python-dotenv |
| python-multipart | 0.0.32 | Apache-2.0 | https://github.com/Kludex/python-multipart |
| pyyaml | 6.0.3 | MIT | https://github.com/yaml/pyyaml |
| segno | 1.6.6 | BSD-3-Clause | https://github.com/heuer/segno |
| sqlalchemy | 2.1.2 | MIT | https://github.com/sqlalchemy/sqlalchemy |
| starlette | 1.7.0 | BSD-3-Clause | https://github.com/Kludex/starlette |
| typing-extensions | 4.16.0 | PSF-2.0 | https://github.com/python/typing_extensions |
| typing-inspection | 0.4.4 | MIT | https://github.com/pydantic/typing-inspection |
| uvicorn | 0.54.0 | BSD-3-Clause | https://github.com/Kludex/uvicorn |
| uvloop | 0.23.0 | MIT OR Apache-2.0 | https://github.com/MagicStack/uvloop |
| watchfiles | 1.3.0 | MIT | https://github.com/samuelcolvin/watchfiles |
| websockets | 17.1 | BSD-3-Clause | https://github.com/python-websockets/websockets |

## Entwicklung und Tests (nicht im Image)

pytest (MIT), ruff (MIT), Playwright für Python (Apache-2.0), pytest-playwright (Apache-2.0), httpx (BSD-3-Clause) – nur für Entwicklung und Tests, werden nicht mit ausgeliefert.

## Basis-Images und Werkzeuge

Das Image baut auf `python:3.12-slim` (Python: PSF-2.0; Debian-Pakete mit ihren jeweiligen Lizenzen) und nutzt beim Bauen `uv` (MIT OR Apache-2.0). `docker-compose.caddy.yml` startet das offizielle Image `caddy:2` (Apache-2.0).

## Daten

Die mitgelieferten Altersklassen und Leistungsstufen (`config/`) geben die Werte des Deutschen Radsportabzeichens wieder (Tabellenwerte, keine übernommenen Texte). Die offiziellen Unterlagen sind nicht Teil des Repositorys.
