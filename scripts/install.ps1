# ====================================================================================
# AEDI - IONITY GLOBAL | ESP32-MCP one-step installer (Windows 10/11)
# Author: Johan Wilhelm van Antwerp | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd | AEDI
# Governance: Policy 986 AED | License: AED 900 | CC BY-NC-SA 4.0 where stated
# (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd - All Rights Reserved - TM2
# Owner: github.com/Ionity-Global | www.ionity.today | ai@ionity.today
# ---------------------------------------------------------------------------
# Creates the three Python environments, writes .env with fresh random secrets
# and (unless -NoStart) starts everything. Safe to run again: existing
# environments are updated and an existing .env is never overwritten.
#
#   .\scripts\install.ps1               install + start
#   .\scripts\install.ps1 -NoStart      install only
#   .\scripts\install.ps1 -NoMcpo       skip the AI gateway (mcpo)
#   .\scripts\install.ps1 -Autostart    also start the system at every Windows sign-in
#   .\scripts\install.ps1 -Firewall     open the needed ports on Private networks (run as Administrator)
# ===========================================================================
param([switch]$NoStart, [switch]$NoMcpo, [switch]$Autostart, [switch]$Firewall)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
function Say($m, $c = 'Cyan') { Write-Host "[install] $m" -ForegroundColor $c }
function Fail($m) { Write-Host "[install] $m" -ForegroundColor Red; exit 1 }

# 1. Python 3.11 or newer
$py = $null; $ver = $null
foreach ($cand in @(@('py', '-3'), @('python'), @('python3'))) {
  try {
    $a = @(); if ($cand.Count -gt 1) { $a += $cand[1] }
    $v = & $cand[0] @a -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    if ($LASTEXITCODE -eq 0 -and $v -and [version]$v -ge [version]'3.11') { $py = $cand; $ver = $v; break }
  } catch { }
}
if (-not $py) { Fail 'Python 3.11 or newer is required. Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run INSTALL again.' }
Say "Python $ver found"
function PyRun([string[]]$a) { $x = @(); if ($py.Count -gt 1) { $x += $py[1] }; & $py[0] @x @a }

# 2. environments
function Venv($dir, $label, $req, [string[]]$pkgs) {
  if (-not (Test-Path "$dir\Scripts\python.exe")) { Say "creating $label environment"; PyRun @('-m', 'venv', $dir); if ($LASTEXITCODE) { Fail "could not create $dir" } }
  $vp = "$dir\Scripts\python.exe"
  & $vp -m pip install -q --disable-pip-version-check --upgrade pip 2>$null | Out-Null
  if ($req) { & $vp -m pip install -q --disable-pip-version-check -r $req } else { & $vp -m pip install -q --disable-pip-version-check @pkgs }
  if ($LASTEXITCODE) { Fail "installing $label failed - check the internet connection and run INSTALL again" }
  Say "$label ready" 'Green'
}
Venv "$root\.venv" 'fleet server' "$root\server\requirements.txt"
Venv "$root\.venv-broker" 'MQTT broker' "$root\packaging\broker\requirements.txt"
if (-not $NoMcpo) { Venv "$root\.venv-mcpo" 'AI gateway (mcpo)' $null @('mcpo', 'mcp>=1.24,<2') }

# 3. .env with fresh secrets (never overwritten)
function NewSecret { $b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); -join ($b | ForEach-Object { $_.ToString('x2') }) }
if (Test-Path "$root\.env") { Say '.env already exists - kept as it is' }
else {
  $t = Get-Content "$root\.env.example" -Raw
  $t = $t -replace '(?m)^IONITY_ADMIN_TOKEN=.*$', ('IONITY_ADMIN_TOKEN=' + (NewSecret))
  $t = $t -replace '(?m)^IONITY_MCPO_API_KEY=.*$', ('IONITY_MCPO_API_KEY=' + (NewSecret))
  [IO.File]::WriteAllText("$root\.env", $t, (New-Object Text.UTF8Encoding $false))
  Say '.env created with a fresh admin token and AI-gateway key (stored only in .env)' 'Green'
}
New-Item -ItemType Directory -Force "$root\logs" | Out-Null

# 4. optional: firewall (Private networks only) and autostart
if ($Firewall) {
  $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
  if (-not $admin) { Say 'firewall: skipped - run INSTALL as Administrator to add the rules' 'Yellow' }
  else {
    foreach ($r in @(@('TCP', '8099', 'dashboard + MCP'), @('TCP', '8000', 'AI gateway (mcpo)'), @('TCP', '1883', 'MQTT broker'), @('UDP', '53', 'LAN DNS'), @('UDP', '5353', 'mDNS'))) {
      $n = "Ionity ESP32-MCP $($r[2]) $($r[0]) $($r[1])"
      if (-not (Get-NetFirewallRule -DisplayName $n -ErrorAction SilentlyContinue)) { New-NetFirewallRule -DisplayName $n -Direction Inbound -Protocol $r[0] -LocalPort $r[1] -Action Allow -Profile Private | Out-Null } }
    Say 'firewall: inbound rules added for Private networks' 'Green' }
}
if ($Autostart) {
  $lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'Ionity ESP32-MCP.lnk'
  $s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
  $s.TargetPath = 'powershell.exe'; $s.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$root\scripts\start_lab.ps1`" -Quiet"
  $s.WorkingDirectory = $root; $s.WindowStyle = 7; $s.Save()
  Say "autostart: $lnk" 'Green'
}

# 5. start
if (-not $NoStart) { & "$root\scripts\start_lab.ps1" }
$ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notmatch '^(127|169\.254)\.' -and $_.PrefixOrigin -ne 'WellKnown' } | Sort-Object InterfaceMetric | Select-Object -First 1).IPAddress
if (-not $ip) { $ip = 'localhost' }
Write-Host ''
Say 'done.' 'Green'
Write-Host "  Dashboard     http://${ip}:8099/"
if (-not $NoMcpo) { Write-Host "  AI gateway    http://${ip}:8000/docs      (key: IONITY_MCPO_API_KEY in .env)" }
Write-Host "  Admin token   IONITY_ADMIN_TOKEN in .env  (the dashboard asks for it once, to control devices)"
Write-Host '  Next          flash an ESP32 with the dashboard''s Flasher, then follow the User Guide.'
