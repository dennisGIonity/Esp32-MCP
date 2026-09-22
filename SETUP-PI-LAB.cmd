@echo off
rem AEDI - IONITY GLOBAL | Pi 5 lab setup. Double-click.
rem   1. pauses GateFlame (reversible - RESUME-GATEFLAME.cmd undoes it)
rem   2. puts the live fleet dashboard on the ASUS screen
rem You type YOUR SSH key passphrase (if not already loaded) and YOUR Pi sudo password.
"C:\Program Files\Git\bin\bash.exe" -l "/e/.ESP32-MCP/tools/pi/pi-gateflame.sh" lab-setup
echo.
pause
