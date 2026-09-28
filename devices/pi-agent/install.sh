#!/usr/bin/env bash
# ===========================================================================
# AEDI - IONITY GLOBAL | Install the Ionity fleet agent on a Raspberry Pi
# Policy 986 AED | (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd
# ---------------------------------------------------------------------------
#   sudo bash install.sh                    # install / upgrade, keep existing config
#   sudo bash install.sh --site lab --group bench --label "pi-zero-01"
#   sudo bash install.sh --server 192.168.124.4
#   sudo bash install.sh --uninstall
# Idempotent. Needs: Raspberry Pi OS (Bullseye/Bookworm) or any systemd Linux, python3.
# ===========================================================================
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST=/opt/ionity-agent
CONF=/etc/ionity-agent.conf
UNIT=/etc/systemd/system/ionity-agent.service
say() { echo "[ionity-agent] $*"; }
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo."; exit 1; }

if [ "${1:-}" = "--uninstall" ]; then
  systemctl disable --now ionity-agent.service 2>/dev/null || true
  rm -f "$UNIT"; systemctl daemon-reload
  rm -rf "$DEST"
  say "removed (config $CONF and state /var/lib/ionity-agent kept)"
  exit 0
fi

SITE="" GROUP="" LABEL="" SERVER=""
while [ $# -gt 0 ]; do
  case "$1" in
    --site) SITE="$2"; shift 2 ;;
    --group) GROUP="$2"; shift 2 ;;
    --label) LABEL="$2"; shift 2 ;;
    --server) SERVER="$2"; shift 2 ;;
    *) echo "unknown option $1"; exit 2 ;;
  esac
done

command -v python3 >/dev/null || { say "python3 missing: apt install python3"; exit 1; }
if ! python3 -c 'import paho.mqtt.client' 2>/dev/null; then
  say "installing paho-mqtt (MQTT + commands)"
  if command -v apt-get >/dev/null; then
    apt-get install -y python3-paho-mqtt >/dev/null || say "apt failed - agent will run HTTP-only"
  fi
fi
if ! getent hosts ionity-fleet.local >/dev/null 2>&1; then
  command -v apt-get >/dev/null && apt-get install -y libnss-mdns avahi-daemon >/dev/null 2>&1 || true
fi

install -d -m 755 "$DEST"
install -m 755 "$HERE/ionity_agent.py" "$DEST/ionity_agent.py"
if [ ! -f "$CONF" ]; then
  install -m 640 "$HERE/ionity-agent.conf.example" "$CONF"
  say "created $CONF"
fi
setkey() { [ -n "$2" ] && sed -i "s|^$1 *=.*|$1 = $2|" "$CONF"; return 0; }
setkey site "$SITE"; setkey group "$GROUP"; setkey label "$LABEL"; setkey server "$SERVER"

install -m 644 "$HERE/ionity-agent.service" "$UNIT"
systemctl daemon-reload
systemctl enable ionity-agent.service >/dev/null
systemctl restart ionity-agent.service

say "installed. One reading from this Pi:"
python3 "$DEST/ionity_agent.py" --config "$CONF" --once | head -40
sleep 3
systemctl --no-pager --lines=8 status ionity-agent.service || true
say "logs: journalctl -u ionity-agent -f"
