# ===========================================================================
# AEDI - IONITY GLOBAL | Build the ESP32-MCP testers package (zip)
# Policy 986 AED | (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd
# ---------------------------------------------------------------------------
#   powershell -ExecutionPolicy Bypass -File scripts\build_testers_package.ps1
# Output: dist\Ionity-ESP32-MCP-Testers-v<ver>.zip  (+ the unzipped folder)
# Ships ONLY public material: no .env, no secrets.h, no firmware/dist-lab,
# no data/, no logs. Verifies that before zipping.
# ===========================================================================
param([string]$Version = '')
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
if (-not $Version) {
  $Version = (& "$Root\.venv\Scripts\python.exe" -c "import sys;sys.path.insert(0,r'$Root\server');from app.mcp.protocol import SERVER_VERSION as v;print(v)").Trim()
  $fw = (Get-Content "$Root\firmware\dist\manifest.json" -Raw | ConvertFrom-Json).version
  $Version = "$Version-fw$fw"
}
$Name = "Ionity-ESP32-MCP-Testers-v$Version"
$Dist = Join-Path $Root 'dist'
$Out  = Join-Path $Dist $Name
if (Test-Path $Out) { Remove-Item $Out -Recurse -Force }   # build output only, regenerated every run
New-Item -ItemType Directory -Force $Out | Out-Null

function Copy-Tree($src, $dst, [string[]]$excludeDirs = @(), [string[]]$excludeFiles = @()) {
  $s = Join-Path $Root $src; $d = Join-Path $Out $dst
  $xd = @('__pycache__', '.pytest_cache', '.ruff_cache', 'node_modules') + $excludeDirs
  $rc = @($s, $d, '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/NP', '/XD') + $xd
  if ($excludeFiles.Count) { $rc += @('/XF') + $excludeFiles }
  robocopy @rc | Out-Null
  if ($LASTEXITCODE -ge 8) { throw "copy failed: $src" }
}

# package front matter (.cmd, manual, readme, tools)
Copy-Tree 'packaging\testers' '.'
# application
Copy-Tree 'server' 'server' -excludeFiles @('*.db', '*.log')
Copy-Tree 'dashboard' 'dashboard'
Copy-Tree 'flasher\dist' 'flasher\dist' -excludeFiles @('*.map')
Copy-Tree 'firmware\dist' 'firmware\dist'
Copy-Tree 'devices\pi-agent' 'devices\pi-agent'
New-Item -ItemType Directory -Force "$Out\scripts", "$Out\broker", "$Out\docs" | Out-Null
Copy-Item "$Root\scripts\device_emulator.py", "$Root\scripts\purge_devices.py" "$Out\scripts\"
Copy-Item "$Root\packaging\broker\run_broker.py", "$Root\packaging\broker\requirements.txt" "$Out\broker\"
foreach ($doc in 'DEPLOYABLES.md') { if (Test-Path "$Root\docs\$doc") { Copy-Item "$Root\docs\$doc" "$Out\docs\" } }
Copy-Item "$Root\CHANGELOG.md" "$Out\" -ErrorAction SilentlyContinue

# safety gate: nothing private may ship
$bad = Get-ChildItem $Out -Recurse -Force | Where-Object {
  $_.Name -in @('.env', 'secrets.h', 'fleet.db') -or $_.FullName -match '\\dist-lab\\|\\\.venv' }
if ($bad) { throw "refusing to package private files: $($bad.FullName -join ', ')" }
$hits = Select-String -Path (Get-ChildItem $Out -Recurse -File -Include *.py, *.js, *.json, *.md, *.html, *.ps1, *.env, *.txt).FullName `
  -Pattern '(?i)(wifi_pass|password)\s*[:=]\s*"[^"$<{]{6,}"' -ErrorAction SilentlyContinue
if ($hits) { throw "possible secret in package: $($hits | Select-Object -First 3 | Out-String)" }

$zip = Join-Path $Dist "$Name.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path $Out -DestinationPath $zip -CompressionLevel Optimal
$mb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host "[ionity] built $zip ($mb MB, $((Get-ChildItem $Out -Recurse -File).Count) files)" -ForegroundColor Green
