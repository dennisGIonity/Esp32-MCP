@echo off
rem AEDI - IONITY GLOBAL | ESP32-MCP testers | Policy 986 AED | (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd
title Ionity ESP32-MCP - OPEN-DASHBOARD
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\tester.ps1" open
echo.
pause
