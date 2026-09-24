# ===========================================================================
# AEDI - IONITY GLOBAL | Bring ESP32-MCP up inside the Ionity Lab (idempotent)
# Policy 986 AED | (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd
# ---------------------------------------------------------------------------
# The lab itself (network, shared MQTT broker, Pi tools) is its own project:
# Ionity-Lab, E:\.IONITY-LAB (override with IONITY_LAB_HOME). This script:
#   1. asks the lab for its MQTT broker   :1883   (lab.ps1 broker)
#   2. starts the fleet server             :8099   server\run.py            (.venv)
#   3. starts the serial bridge                    scripts\serial_bridge.py (.venv)
#
# Order matters: broker first, so the fleet server's MQTT client connects on
# its first attempt instead of after a back-off.
#
# Run at logon by the Startup shortcut, by the MCP bridge if it finds the
# server down, and by the lab's own "lab.ps1 start". Safe to run repeatedly.
# -Restart restarts this project's services only; the broker belongs to the lab.
# ===========================================================================
param([switch]$Restart, [switch]$Quiet)

$root = 'E:\.ESP32-MCP'
$labHome = if ($env:IONITY_LAB_HOME) { $env:IONITY_LAB_HOME } else { 'E:\.IONITY-LAB' }
Set-Location $root
function Say($m) { if (-not $Quiet) { Write-Host "[lab] $m" } }
function Listening($port) { [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) }
function ProcLike($pattern) {
  @(Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like $pattern })
}
function Stop-Like($pattern) { ProcLike $pattern | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } }

if ($Restart) {
  Say 'restarting ESP32-MCP services (fleet server + serial bridge)'
  Stop-Like '*serial_bridge.py*'; Stop-Like '*server\run.py*'
  Start-Sleep 3
}

# 1. broker - owned by the Ionity Lab
if (Listening 1883) { Say 'broker        already up on :1883 (Ionity Lab)' }
elseif (Test-Path "$labHome\lab.ps1") {
  & "$labHome\lab.ps1" broker -Quiet:$Quiet
}
else {
  Say "broker        DOWN and Ionity Lab not found at $labHome - clone github.com/dennisGIonity/Ionity-2nd-Router-Test-Lab there"
  Say '              (the fleet server still runs; devices fall back to HTTP until the broker is up)'
}

# 2. fleet server
if (Listening 8099) { Say 'fleet server  already up on :8099' }
else {
  Say 'fleet server  starting'
  # python.exe with BOTH streams redirected, not pythonw: under pythonw sys.stdout is
  # None and uvicorn's log formatter dies at startup ("Unable to configure formatter
  # 'default'") - the server then never listens and the MCP tools go dark.
  Start-Process -FilePath "$root\.venv\Scripts\python.exe" -ArgumentList "$root\server\run.py" `
    -WorkingDirectory $root -RedirectStandardOutput "$root\logs_out.txt" `
    -RedirectStandardError "$root\logs_err.txt" -WindowStyle Hidden
  for ($i = 0; $i -lt 40 -and -not (Listening 8099); $i++) { Start-Sleep 1 }
}

# 3. serial bridge
if ((ProcLike '*serial_bridge.py*').Count -gt 0) { Say 'serial bridge already running' }
else {
  Say 'serial bridge starting'
  Start-Process -FilePath "$root\.venv\Scripts\pythonw.exe" -ArgumentList "$root\scripts\serial_bridge.py" `
    -WorkingDirectory $root -RedirectStandardOutput "$root\bridge_out.txt" -WindowStyle Hidden
}

if (-not $Quiet) {
  Start-Sleep 3
  try {
    $h = Invoke-RestMethod 'http://127.0.0.1:8099/api/v1/health' -TimeoutSec 8
    $s = Invoke-RestMethod 'http://127.0.0.1:8099/api/v1/fleet/summary' -TimeoutSec 8
    Say ("UP  broker={0}  mqtt={1}  mdns={2}  dns={3}" -f (Listening 1883), $h.mqtt.connected,
         $h.discovery.advertised_ip, $h.dns.running)
    Say ("fleet: total={0} online={1} stale={2} offline={3} alerting={4}" -f `
         $s.total_devices, $s.online, $s.stale, $s.offline, $s.alerting)
    Say ("dashboard  http://{0}:8099/" -f $h.discovery.advertised_ip)
  } catch { Say "fleet server did not answer - see $root\logs_err.txt" }
}
