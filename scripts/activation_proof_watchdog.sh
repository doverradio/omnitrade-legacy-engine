#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${OMNITRADE_WATCHDOG_PYTHON:-python3}"
exec "${PYTHON}" "${ROOT_DIR}/scripts/activation_proof_watchdog.py" "$@"
