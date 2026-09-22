#!/usr/bin/env bash
# ===========================================================================
# AEDI - IONITY GLOBAL | Drive the GateFlame pause/resume from Windows
#   pi-gateflame.sh status | dry-run | pause | resume
# Run from Git-bash. It never handles a secret itself: if the SSH key is not
# loaded it runs GateFlame's own load-key.cmd so YOU type the passphrase, and
# the sudo password prompt appears in this window for YOU to answer.
# ===========================================================================
set -u
ACTION=${1:-status}
export SSH_AUTH_SOCK=/c/Users/DGMic/.ssh/agent.sock
HERE=$(cd "$(dirname "$0")" && pwd)
SSH_OPTS=(-o ConnectTimeout=6 -o HostKeyAlias=raspberrypi -o StrictHostKeyChecking=yes)

if ! ssh-add -l >/dev/null 2>&1; then
  echo "SSH key not loaded. Loading it now - enter YOUR passphrase when asked."
  rm -f ~/.ssh/agent.sock
  eval "$(ssh-agent -a ~/.ssh/agent.sock -s)" >/dev/null
  ssh-add ~/.ssh/id_ed25519 || { echo "Key not loaded - stopping."; exit 1; }
fi

HOST=""
for h in 192.168.124.3 192.168.0.11 192.168.0.10; do
  if ssh "${SSH_OPTS[@]}" -o BatchMode=yes "wabapi@$h" true 2>/dev/null; then HOST=$h; break; fi
done
[ -n "$HOST" ] || { echo "Could not reach the Pi as wabapi on any known address."; exit 1; }
echo "Pi reachable at $HOST"

status() {
  ssh "${SSH_OPTS[@]}" "wabapi@$HOST" '
    echo "--- gateflame units ---"
    systemctl list-unit-files "gateflame*" --no-legend | while read u s _; do
      printf "  %-34s enabled=%-9s active=%s\n" "$u" "$(systemctl is-enabled $u 2>/dev/null)" "$(systemctl is-active $u 2>/dev/null)"; done
    echo "--- gateflame containers ---"
    docker ps -a --format "  {{.Names}}  {{.Status}}" | grep -i gateflame || echo "  none"
    [ -f /var/lib/ionity-lab/gateflame-paused.state ] && echo "STATE: PAUSED" || echo "STATE: running (not paused)"'
}

case "$ACTION" in
  status)  status ;;
  dry-run|pause|resume)
    scp "${SSH_OPTS[@]}" -q "$HERE/gateflame-pause.sh" "$HERE/gateflame-resume.sh" "wabapi@$HOST:~/" || exit 1
    case "$ACTION" in
      dry-run) ssh -t "${SSH_OPTS[@]}" "wabapi@$HOST" "sudo bash ~/gateflame-pause.sh --dry-run" ;;
      pause)   ssh -t "${SSH_OPTS[@]}" "wabapi@$HOST" "sudo bash ~/gateflame-pause.sh" ;;
      resume)  ssh -t "${SSH_OPTS[@]}" "wabapi@$HOST" "sudo bash ~/gateflame-resume.sh" ;;
    esac
    echo; status ;;
  *) echo "usage: pi-gateflame.sh status|dry-run|pause|resume"; exit 2 ;;
esac
