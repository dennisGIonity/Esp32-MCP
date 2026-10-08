# ===========================================================================
# AEDI - IONITY GLOBAL | Build the ESP32-MCP POC kit (testers kit + POC layer)
# Author: Johan Wilhelm van Antwerp | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd | AEDI
# Governance: Policy 986 AED | License: AED 900 | CC BY-NC-SA 4.0 where stated
# (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd - All Rights Reserved - TM2
# Owner: github.com/Ionity-Global | www.ionity.today | ai@ionity.today
# ---------------------------------------------------------------------------
#   powershell -ExecutionPolicy Bypass -File scripts\build_poc_package.ps1 -Out <folder>
# Runs scripts\build_testers_package.ps1 (broker, server, dashboard, flasher +
# firmware, manual), copies the result to <folder>\Ionity-ESP32-MCP-POC-Kit and
# overlays packaging\poc (setup/start/stop/status/watch/ai-key) plus the alarm
# watcher and AI-gateway launcher. Ships no secrets: verified before it returns.
# ===========================================================================
param([Parameter(Mandatory)][string]$Out)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
& "$Root\scripts\build_testers_package.ps1" | Out-Host
$kit = Get-ChildItem "$Root\dist" -Directory -Filter 'Ionity-ESP32-MCP-Testers-v*' | Where-Object { $_.Name -notlike '*dryrun*' } | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $kit) { throw 'testers kit not built' }
$dest = Join-Path $Out 'Ionity-ESP32-MCP-POC-Kit'
if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
New-Item -ItemType Directory -Force $Out | Out-Null
Copy-Item $kit.FullName $dest -Recurse
Copy-Item "$Root\packaging\poc\*.cmd" $dest -Force
Copy-Item "$Root\packaging\poc\tools\poc.ps1" "$dest\tools\" -Force
New-Item -ItemType Directory -Force "$dest\scripts" | Out-Null
Copy-Item "$Root\scripts\net_watch.py", "$Root\scripts\run_mcpo.py" "$dest\scripts\" -Force
if (-not (Test-Path "$dest\server\mcp_stdio_proxy.py")) { Copy-Item "$Root\server\mcp_stdio_proxy.py" "$dest\server\" }
$bad = @(Get-ChildItem $dest -Recurse -Force -File | Where-Object { $_.Name -in @('.env', 'secrets.h', 'fleet.db') -or $_.FullName -match '\\\.venv|\\dist-lab\\' })
if ($bad.Count) { throw ('private files in the kit: ' + (($bad | ForEach-Object { $_.FullName }) -join ', ')) }
"POC kit: $dest  (from $($kit.Name); $(@(Get-ChildItem $dest -Recurse -File).Count) files, no secrets)"
