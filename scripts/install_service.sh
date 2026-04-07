#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.mac-ipv4-monitor.overlay"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/${LABEL}.plist"
LOG_DIR="$HOME/Library/Logs"
STDOUT_LOG="$LOG_DIR/mac-ipv4-monitor.out.log"
STDERR_LOG="$LOG_DIR/mac-ipv4-monitor.err.log"
PY_BIN="$ROOT_DIR/.venv/bin/python"
APP_FILE="$ROOT_DIR/python_ipv4_monitor.py"
USER_DOMAIN="gui/$(id -u)"

if [[ ! -x "$PY_BIN" ]]; then
  echo "[error] missing venv python: $PY_BIN"
  echo "[hint] run: ./scripts/setup_venv.sh"
  exit 1
fi

mkdir -p "$PLIST_DIR" "$LOG_DIR"

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>

  <key>ProgramArguments</key>
  <array>
    <string>$PY_BIN</string>
    <string>$APP_FILE</string>
  </array>

  <key>WorkingDirectory</key>
  <string>$ROOT_DIR</string>

  <key>RunAtLoad</key>
  <true/>

  <key>KeepAlive</key>
  <true/>

  <key>LimitLoadToSessionType</key>
  <array>
    <string>Aqua</string>
  </array>

  <key>StandardOutPath</key>
  <string>$STDOUT_LOG</string>

  <key>StandardErrorPath</key>
  <string>$STDERR_LOG</string>
</dict>
</plist>
PLIST

launchctl bootout "$USER_DOMAIN/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "$USER_DOMAIN" "$PLIST_PATH"
launchctl enable "$USER_DOMAIN/$LABEL" >/dev/null 2>&1 || true
launchctl kickstart -k "$USER_DOMAIN/$LABEL"

echo "[ok] service installed and started"
echo "[ok] plist: $PLIST_PATH"
echo "[ok] status: ./scripts/service_status.sh"
