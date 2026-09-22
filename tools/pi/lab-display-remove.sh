#!/usr/bin/env bash
# AEDI - IONITY GLOBAL | Remove the Ionity lab display kiosk (packages are left installed)
set -u
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo."; exit 1; }
U=${SUDO_USER:-wabapi}; UH=$(getent passwd "$U" | cut -d: -f6)
systemctl disable --now ionity-lab-display.service 2>/dev/null
rm -f /etc/systemd/system/ionity-lab-display.service
systemctl daemon-reload
systemctl enable --now getty@tty1.service 2>/dev/null || true
rm -f "$UH/.config/autostart/ionity-lab-display.desktop" /usr/local/bin/ionity-lab-display /etc/ionity-lab-display.conf
pkill -u "$U" -f ionity-lab-kiosk 2>/dev/null || true
echo "Ionity lab display removed."
