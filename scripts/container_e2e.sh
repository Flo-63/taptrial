#!/usr/bin/env bash
# Baut das Docker-Image und führt die komplette E2E-Suite gegen den Container aus.
#   scripts/container_e2e.sh
set -euo pipefail
cd "$(dirname "$0")/.."
docker build -t rcb-zeitfahren:e2e .
RCB_E2E_IMAGE=rcb-zeitfahren:e2e uv run pytest -q tests/e2e "$@"
