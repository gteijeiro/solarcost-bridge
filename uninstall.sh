#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv}"

if [ ! -x "$VENV_DIR/bin/sa-totals-bridge" ]; then
  echo "No se encontro $VENV_DIR/bin/sa-totals-bridge"
  exit 1
fi

exec "$VENV_DIR/bin/sa-totals-bridge" uninstall "$@"
