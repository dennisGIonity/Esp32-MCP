@echo off
rem AEDI - IONITY GLOBAL | Lab template - put the lab WiFi into every board's firmware
rem You type the password here; it goes only into the git-ignored secrets.h files.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\lab\set_lab_wifi.ps1"
echo.
pause
