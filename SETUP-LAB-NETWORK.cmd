@echo off
rem AEDI - IONITY GLOBAL | Lab template - one-click laptop network setup
rem Household WiFi = internet, H3C Ethernet = lab only. Asks for admin (UAC).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\lab\setup_lab_network.ps1"
