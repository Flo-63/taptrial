# TapTrial – Zeitnahme per Handy. Ein Prozess (uvicorn), Port 8000, Healthcheck /up, Daten in /data.
FROM python:3.12-slim

# Version aus pyproject.toml; der Release-Workflow übergibt sie als --build-arg
ARG VERSION=0.1.1
LABEL org.opencontainers.image.title="TapTrial" \
      org.opencontainers.image.description="TapTrial – Zeitnahme per Handy für Einzelzeitfahren" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.licenses="PolyForm-Noncommercial-1.0.0" \
      org.opencontainers.image.source="https://github.com/Flo-63/taptrial" \
      org.opencontainers.image.url="https://github.com/Flo-63/taptrial"

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    TAPTRIAL_DB_PFAD=/data/zeitfahren.sqlite3

WORKDIR /app

# Abhängigkeiten zuerst (Layer-Cache), ohne Dev-Gruppe
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY config ./config
COPY docs/handbuch ./docs/handbuch
COPY LICENSE THIRD_PARTY_LICENSES.md ./

# Nicht als root; /data gehört dem App-Nutzer (benanntes Volume übernimmt die Rechte)
RUN useradd --system --uid 1000 --home /app zeitfahren \
    && mkdir -p /data && chown zeitfahren:zeitfahren /data
USER zeitfahren
VOLUME ["/data"]

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/up', timeout=4).status == 200 else 1)"]

# TLS terminiert ein Reverse-Proxy davor (z. B. Caddy, docker-compose.caddy.yml). X-Forwarded-*
# wertet die App selbst aus, und nur von vertrauten Proxys (TAPTRIAL_VERTRAUTE_PROXYS, app/proxy.py).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
