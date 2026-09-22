#!/usr/bin/env bash
# ===========================================================================
# AEDI - IONITY GLOBAL | Pause GateFlame on the Pi 5 - REVERSIBLE
# Policy 986 AED | (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd
# ---------------------------------------------------------------------------
# Stops and disables every GateFlame piece so the Pi is a quiet, ordinary
# host on the isolated lab router. Deletes NOTHING. Every prior state is
# recorded in $STATE and gateflame-resume.sh puts it back exactly.
#
#   sudo bash gateflame-pause.sh            # pause
#   sudo bash gateflame-pause.sh --dry-run  # show what would happen
#
# Unit/container names are the real ones (gateflame-kiosk, -node-agent,
# -mdns-alias, gateflame-pihole, gateflame-unbound) but the script discovers
# them rather than trusting a list, because guessed names fail silently.
# ===========================================================================
set -u
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
STATE_DIR=/var/lib/ionity-lab
STATE=$STATE_DIR/gateflame-paused.state
run() { if [ $DRY -eq 1 ]; then echo "  [dry-run] $*"; else eval "$@"; fi; }

if [ "$(id -u)" -ne 0 ]; then echo "Run with sudo."; exit 1; fi
if [ -f "$STATE" ] && [ $DRY -eq 0 ]; then
  echo "GateFlame already paused (state: $STATE). Run gateflame-resume.sh first."; exit 1
fi
mkdir -p "$STATE_DIR"
[ $DRY -eq 0 ] && : > "$STATE"
rec() { [ $DRY -eq 0 ] && echo "$*" >> "$STATE"; }

echo "=== GateFlame pause on $(hostname) - $(date -Is) ==="

echo "--- systemd units ---"
for u in $(systemctl list-unit-files 'gateflame*' --no-legend 2>/dev/null | awk '{print $1}'); do
  en=$(systemctl is-enabled "$u" 2>/dev/null || true)
  ac=$(systemctl is-active  "$u" 2>/dev/null || true)
  echo "  $u  enabled=$en active=$ac  -> disable --now"
  rec "unit $u $en $ac"
  run "systemctl disable --now '$u' >/dev/null 2>&1"
done

echo "--- docker containers ---"
if command -v docker >/dev/null; then
  for c in $(docker ps -a --format '{{.Names}}' | grep -i gateflame); do
    pol=$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' "$c")
    st=$(docker inspect -f '{{.State.Running}}' "$c")
    echo "  $c  restart=$pol running=$st  -> restart=no, stop"
    rec "container $c $pol $st"
    run "docker update --restart=no '$c' >/dev/null"
    run "docker stop '$c' >/dev/null"
  done
fi

echo "--- mDNS alias (gateflame.local) ---"
if [ -f /etc/avahi/services/gateflame.service ]; then
  echo "  moving /etc/avahi/services/gateflame.service aside"
  rec "avahi /etc/avahi/services/gateflame.service"
  run "mv /etc/avahi/services/gateflame.service '$STATE_DIR/gateflame.service.avahi'"
  run "systemctl reload avahi-daemon 2>/dev/null || true"
fi

echo "--- cron ---"
for f in /etc/cron.d/*gateflame*; do
  [ -e "$f" ] || continue
  echo "  moving $f aside"; rec "cronfile $f"
  run "mv '$f' '$STATE_DIR/$(basename "$f").cron'"
done
if crontab -l 2>/dev/null | grep -qi gateflame; then
  echo "  root crontab has gateflame lines -> saved and removed"
  run "crontab -l > '$STATE_DIR/root.crontab.bak'"
  rec "crontab root"
  run "crontab -l | grep -vi gateflame | crontab -"
fi

echo "--- left in place, for your information (NOT changed) ---"
echo "  ip_forward = $(cat /proc/sys/net/ipv4/ip_forward)"
grep -E '^nameserver' /etc/resolv.conf | sed 's/^/  resolv.conf: /'
if grep -qE '^nameserver 127\.' /etc/resolv.conf; then
  echo "  NOTE: the Pi resolves through itself (Pi-hole). With Pi-hole stopped the Pi"
  echo "        cannot resolve names until resume. SSH by IP is unaffected."
fi
(nft list ruleset 2>/dev/null | grep -ci gateflame | sed 's/^/  nft rules mentioning gateflame: /') || true
(iptables-save 2>/dev/null | grep -ci gateflame | sed 's/^/  iptables rules mentioning gateflame: /') || true

[ $DRY -eq 0 ] && echo "paused $(date -Is)" >> "$STATE"
echo "=== done. State recorded in $STATE - undo with: sudo bash gateflame-resume.sh ==="
