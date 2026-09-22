@echo off
rem AEDI - IONITY GLOBAL | Pause GateFlame on the Pi 5 (reversible). Double-click.
rem You will be asked for YOUR SSH key passphrase (if not loaded) and YOUR Pi sudo password.
"C:\Program Files\Git\bin\bash.exe" -l "/e/.ESP32-MCP/tools/pi/pi-gateflame.sh" pause
echo.
pause
