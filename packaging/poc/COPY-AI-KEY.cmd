@echo off
rem AEDI - IONITY GLOBAL | ESP32-MCP POC | Policy 986 AED | (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd
title Ionity ESP32-MCP - AI KEY
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\poc.ps1" ai-key
echo.
pause
