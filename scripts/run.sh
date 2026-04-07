#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_PY="$ROOT_DIR/.venv/bin/python"

if [[ ! -x "$VENV_PY" ]]; then
  echo "[error] .venv is missing. Run: ./scripts/setup_venv.sh"
  exit 1
fi

"$VENV_PY" "$ROOT_DIR/python_ipv4_monitor.py" &
APP_PID=$!

cleanup() {
  if kill -0 "$APP_PID" >/dev/null 2>&1; then
    kill "$APP_PID" >/dev/null 2>&1 || true
    wait "$APP_PID" >/dev/null 2>&1 || true
  fi
}

trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM

wait "$APP_PID"
