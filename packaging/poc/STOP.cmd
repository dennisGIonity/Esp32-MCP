@echo off
rem AEDI - IONITY GLOBAL | ESP32-MCP POC
rem Author: Johan Wilhelm van Antwerp | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd | AEDI
rem Governance: Policy 986 AED | License: AED 900 | CC BY-NC-SA 4.0 where stated
rem (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd - All Rights Reserved - TM2
rem Owner: github.com/Ionity-Global | www.ionity.today | ai@ionity.today
title Ionity ESP32-MCP - STOP
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\poc.ps1" stop
echo.
pause
