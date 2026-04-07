#!/usr/bin/env bash
set -euo pipefail

LABEL="com.mac-ipv4-monitor.overlay"
USER_DOMAIN="gui/$(id -u)"

if launchctl print "$USER_DOMAIN/$LABEL" >/tmp/mac_ipv4_monitor_launchctl.txt 2>/dev/null; then
  echo "[ok] service is loaded: $LABEL"
  rg "state =|pid =|path =" /tmp/mac_ipv4_monitor_launchctl.txt || true
  rm -f /tmp/mac_ipv4_monitor_launchctl.txt
else
  echo "[info] service is not loaded: $LABEL"
fi
