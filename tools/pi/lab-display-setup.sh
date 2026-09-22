#!/usr/bin/env bash
# ===========================================================================
# AEDI - IONITY GLOBAL | Pi 5 lab display: fleet dashboard kiosk on the ASUS
# Policy 986 AED | (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd
# ---------------------------------------------------------------------------
#   sudo bash lab-display-setup.sh [URL]
# Default URL: http://ionity-fleet.local:8099/ (falls back to the IP if the
# Pi cannot resolve .local names).
#
# Works out what it is running on instead of assuming:
#   * which HDMI output the screen is on, and whose screen it is (EDID)
#   * DisplayLink USB monitor?  -> stops: that driver needs a vendor EULA
#   * Pi OS desktop (labwc/wayfire/lxde)?  -> XDG autostart entry
#   * Pi OS Lite?  -> cage + chromium as a systemd kiosk on tty1
# Undo: sudo bash lab-display-remove.sh
# ===========================================================================
set -u
URL=${1:-http://ionity-fleet.local:8099/}
FALLBACK_URL=http://192.168.0.3:8099/
U=${SUDO_USER:-wabapi}
UH=$(getent passwd "$U" | cut -d: -f6)
UID_=$(id -u "$U")
say() { echo "[lab-display] $*"; }
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo."; exit 1; }

say "=== $(hostname) | $(. /etc/os-release; echo "$PRETTY_NAME") | $(uname -m) | user=$U ==="

# --- 1. what screen is connected? -----------------------------------------
pnp() { # decode EDID manufacturer id (bytes 8-9) -> e.g. AUS
  local b; b=$(od -An -tu1 -j8 -N2 "$1" 2>/dev/null) || return
  set -- $b; [ $# -eq 2 ] || return
  local v=$(( ($1 << 8) | $2 ))
  printf "\\$(printf %03o $(( ((v>>10)&31)+64 )))\\$(printf %03o $(( ((v>>5)&31)+64 )))\\$(printf %03o $(( (v&31)+64 )))"
}
CONNECTED=""
for s in /sys/class/drm/card*-*/status; do
  [ "$(cat "$s")" = "connected" ] || continue
  d=$(dirname "$s"); n=$(basename "$d"); m=$(pnp "$d/edid")
  modes=$(head -3 "$d/modes" 2>/dev/null | tr '\n' ' ')
  say "display connected: $n  maker=${m:-?}  modes: $modes"
  [ "$m" = "AUS" ] && say "  -> that is the ASUS screen"
  CONNECTED="$CONNECTED $n"
done
if lsusb 2>/dev/null | grep -qi '17e9:'; then
  say "DisplayLink USB display adapter detected:"
  lsusb | grep -i '17e9:' | sed 's/^/  /'
  if ! lsmod | grep -q '^evdi'; then
    say "This screen needs the DisplayLink (evdi) driver, which is distributed by"
    say "Synaptics under an end-user licence. I will not accept that licence for you."
    say "Get it at https://www.synaptics.com/products/displaylink-graphics/downloads/ubuntu"
    say "(Raspberry Pi OS 64-bit build), install it, reboot, then run this again."
    [ -z "$CONNECTED" ] && exit 3
  fi
fi
[ -z "$CONNECTED" ] && say "WARNING: no connected HDMI display right now. On a Pi 5 use the micro-HDMI"\
                          "port nearest the USB-C power socket (HDMI0). Setting up anyway; it will show when plugged in."

# --- 2. GateFlame's kiosk must not own the screen ------------------------
if systemctl is-active --quiet gateflame-kiosk 2>/dev/null; then
  say "gateflame-kiosk is still ACTIVE and would fight for the screen."
  say "Pause GateFlame first (PAUSE-GATEFLAME.cmd), then re-run."; exit 4
fi

# --- 3. can the Pi reach the dashboard? ------------------------------------
host=$(echo "$URL" | sed -E 's#^[a-z]+://([^:/]+).*#\1#')
if ! getent hosts "$host" >/dev/null; then
  say "the Pi cannot resolve $host (no mDNS resolver?) -> using $FALLBACK_URL"
  URL=$FALLBACK_URL
fi
if command -v curl >/dev/null && curl -fsS -m 6 -o /dev/null "$URL"; then say "dashboard reachable: $URL"
else say "WARNING: dashboard not reachable from the Pi right now ($URL). The kiosk will keep retrying."; fi

# --- 4. desktop or lite? ----------------------------------------------------
DESKTOP=0
for b in labwc wayfire lxsession startlxde-pi; do command -v $b >/dev/null && DESKTOP=1; done
[ "$(systemctl get-default)" = "graphical.target" ] || DESKTOP=0
say "mode: $([ $DESKTOP -eq 1 ] && echo 'Pi OS desktop -> autostart entry' || echo 'Lite -> cage kiosk service')"

CHROME=$(command -v chromium || command -v chromium-browser || true)
PKGS=""
[ -z "$CHROME" ] && PKGS="$PKGS chromium"
[ $DESKTOP -eq 0 ] && ! command -v cage >/dev/null && PKGS="$PKGS cage"
command -v curl >/dev/null || PKGS="$PKGS curl"
if [ -n "$PKGS" ]; then
  say "installing:$PKGS"
  apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $PKGS \
    || { [ -z "$CHROME" ] && apt-get install -y -qq chromium-browser; }
  CHROME=$(command -v chromium || command -v chromium-browser || true)
fi
[ -n "$CHROME" ] || { say "ERROR: chromium could not be installed (no internet on the Pi?)"; exit 5; }

# --- 5. the launcher --------------------------------------------------------
echo "URL=$URL" > /etc/ionity-lab-display.conf
cat > /usr/local/bin/ionity-lab-display <<EOF
#!/usr/bin/env bash
# Ionity lab display launcher - edit /etc/ionity-lab-display.conf to change the URL
. /etc/ionity-lab-display.conf
for i in \$(seq 1 60); do curl -fsS -m 3 -o /dev/null "\$URL" && break; sleep 2; done
exec $CHROME --kiosk --noerrdialogs --disable-infobars --no-first-run \\
  --disable-session-crashed-bubble --disable-features=Translate --check-for-update-interval=31536000 \\
  --password-store=basic --ozone-platform-hint=auto \\
  --user-data-dir=$UH/.config/ionity-lab-kiosk "\$URL"
EOF
chmod 755 /usr/local/bin/ionity-lab-display

# --- 6. start it on boot ----------------------------------------------------
if [ $DESKTOP -eq 1 ]; then
  install -d -o "$U" -g "$U" "$UH/.config/autostart"
  cat > "$UH/.config/autostart/ionity-lab-display.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Ionity Lab Display
Exec=/usr/local/bin/ionity-lab-display
X-GNOME-Autostart-enabled=true
EOF
  chown "$U:$U" "$UH/.config/autostart/ionity-lab-display.desktop"
  command -v raspi-config >/dev/null && raspi-config nonint do_blanking 1 >/dev/null 2>&1 && say "screen blanking off"
  # launch into the running session now, if there is one
  RT=/run/user/$UID_
  if [ -S "$RT/wayland-0" ] || [ -S "$RT/wayland-1" ]; then
    WD=$(ls "$RT" | grep -m1 '^wayland-[0-9]$')
    sudo -u "$U" XDG_RUNTIME_DIR=$RT WAYLAND_DISPLAY=$WD nohup /usr/local/bin/ionity-lab-display >/dev/null 2>&1 &
    say "launched into the running desktop session ($WD)"
  elif [ -S /tmp/.X11-unix/X0 ]; then
    sudo -u "$U" DISPLAY=:0 nohup /usr/local/bin/ionity-lab-display >/dev/null 2>&1 &
    say "launched into the running X session"
  else
    say "no desktop session running yet - it will open at next login/boot"
  fi
else
  usermod -aG video,render,input "$U"
  cat > /etc/systemd/system/ionity-lab-display.service <<EOF
[Unit]
Description=Ionity lab display (fleet dashboard kiosk)
After=systemd-user-sessions.service network-online.target
Wants=network-online.target
Conflicts=getty@tty1.service

[Service]
User=$U
PAMName=login
TTYPath=/dev/tty1
StandardInput=tty
Environment=XDG_RUNTIME_DIR=/run/user/$UID_
ExecStart=/usr/bin/cage -s -- /usr/local/bin/ionity-lab-display
Restart=always
RestartSec=5

[Install]
WantedBy=graphical.target multi-user.target
EOF
  systemctl daemon-reload
  systemctl disable --now getty@tty1.service >/dev/null 2>&1 || true
  systemctl enable --now ionity-lab-display.service
  sleep 4
  say "kiosk service: $(systemctl is-active ionity-lab-display)"
fi
say "=== done. Showing $URL on the screen. Undo: sudo bash lab-display-remove.sh ==="
