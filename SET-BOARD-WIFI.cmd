@echo off
rem AEDI - IONITY GLOBAL | Give a USB-connected ESP32 its WiFi password and test it
rem Policy 986 AED | (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
rem The password is typed here (hidden) and goes only to the board over USB - never to a file.
cd /d "%~dp0"
set PY=.venv\Scripts\python.exe
if not exist "%PY%" set PY=python
set PORT=%1
if "%PORT%"=="" (
  for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "(Get-CimInstance Win32_PnPEntity | Where-Object { $_.Name -match 'CH34|CP210|USB Serial|USB JTAG' -and $_.Name -match '\(COM\d+\)' } | Select-Object -First 1).Name -replace '.*\((COM\d+)\).*','$1'"`) do set PORT=%%P
)
if "%PORT%"=="" ( echo No ESP32 USB port found. Plug the board in and run again, or: SET-BOARD-WIFI.cmd COM3 & pause & exit /b 1 )
echo Board on %PORT%. Close the Flasher / any serial monitor first.
set /p SSID=WiFi name [Afrihost Fibre DTM]: 
if "%SSID%"=="" set SSID=Afrihost Fibre DTM
"%PY%" scripts\provision.py %PORT% --ssid "%SSID%" --password - --role node --test
echo.
echo Done. The board appears ONLINE on http://localhost:8099/ within about a minute.
pause
