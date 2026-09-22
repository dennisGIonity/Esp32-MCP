#!/usr/bin/env bash
# ===========================================================================
# AEDI - IONITY GLOBAL | Resume GateFlame on the Pi 5
# Policy 986 AED | (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd
# Restores exactly what gateflame-pause.sh recorded - no more, no less.
#   sudo bash gateflame-resume.sh
# ===========================================================================
set -u
STATE_DIR=/var/lib/ionity-lab
STATE=$STATE_DIR/gateflame-paused.state
if [ "$(id -u)" -ne 0 ]; then echo "Run with sudo."; exit 1; fi
if [ ! -f "$STATE" ]; then echo "Nothing to resume - no pause state at $STATE."; exit 1; fi

echo "=== GateFlame resume on $(hostname) - $(date -Is) ==="
while read -r kind a b c; do
  case "$kind" in
    avahi)
      echo "  restoring $a"
      mv "$STATE_DIR/gateflame.service.avahi" "$a" && systemctl reload avahi-daemon 2>/dev/null || true ;;
    cronfile)
      echo "  restoring $a"
      mv "$STATE_DIR/$(basename "$a").cron" "$a" ;;
    crontab)
      echo "  restoring root crontab"
      crontab "$STATE_DIR/root.crontab.bak" ;;
  esac
done < "$STATE"

# containers before units: the node agent expects its DNS stack to be up
while read -r kind name pol running; do
  [ "$kind" = "container" ] || continue
  echo "  container $name  restart=$pol  was running=$running"
  docker update --restart="$pol" "$name" >/dev/null
  [ "$running" = "true" ] && docker start "$name" >/dev/null
done < "$STATE"

while read -r kind name enabled active; do
  [ "$kind" = "unit" ] || continue
  echo "  unit $name  was enabled=$enabled active=$active"
  case "$enabled" in enabled|enabled-runtime) systemctl enable "$name" >/dev/null 2>&1 ;; esac
  [ "$active" = "active" ] && systemctl start "$name"
done < "$STATE"

mv "$STATE" "$STATE.resumed-$(date +%Y%m%d-%H%M%S)"
echo "=== GateFlame restored. ==="
systemctl --no-pager --no-legend list-units 'gateflame*' | sed 's/^/  /'
docker ps --format '  {{.Names}}  {{.Status}}' | grep -i gateflame || true
