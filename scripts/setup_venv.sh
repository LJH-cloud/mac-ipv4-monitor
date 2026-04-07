#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$ROOT_DIR/.venv"

python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip
python -m pip install -r "$ROOT_DIR/requirements.txt"

python - <<'PY'
import sys

print(f"[ok] venv python: {sys.executable}")

try:
    import objc  # noqa: F401
    import AppKit  # noqa: F401
except Exception as exc:  # noqa: BLE001
    print(f"[error] PyObjC import failed: {exc}")
    raise SystemExit(1)

print("[ok] PyObjC Cocoa runtime is available")
PY

echo "[done] venv is ready at: $VENV_DIR"
echo "[hint] run: ./scripts/run.sh"
