# ===========================================================================
# AEDI - IONITY GLOBAL | Bring the whole lab up (idempotent)
# Policy 986 AED | (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd
# ---------------------------------------------------------------------------
# Starts, only if not already running:
#   1. MQTT broker      :1883   infra\broker\run_broker.py   (.venv-broker)
#   2. Fleet server     :8099   server\run.py                (.venv)
#   3. Serial bridge            scripts\serial_bridge.py     (.venv)
#
# Order matters: broker first, so the fleet server's MQTT client connects on
# its first attempt instead of after a back-off.
#
# Run at logon by the Startup shortcut, and by the MCP bridge if it finds the
# server down. Safe to run any number of times.
# ===========================================================================
param([switch]$Restart, [switch]$Quiet)

$root = 'E:\.ESP32-MCP'
Set-Location $root
function Say($m) { if (-not $Quiet) { Write-Host "[lab] $m" } }
function Listening($port) { [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) }
function ProcLike($pattern) {
  @(Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like $pattern })
}
function Stop-Like($pattern) { ProcLike $pattern | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } }

if ($Restart) {
  Say 'restarting everything'
  Stop-Like '*serial_bridge.py*'; Stop-Like '*server\run.py*'; Stop-Like '*run_broker.py*'
  Start-Sleep 3
}

# 1. broker
if (Listening 1883) { Say 'broker        already up on :1883' }
else {
  Say 'broker        starting'
  Start-Process -FilePath "$root\.venv-broker\Scripts\pythonw.exe" -ArgumentList "$root\infra\broker\run_broker.py" `
    -WorkingDirectory $root -RedirectStandardError "$root\broker_err.txt" -WindowStyle Hidden
  for ($i = 0; $i -lt 15 -and -not (Listening 1883); $i++) { Start-Sleep 1 }
}

# 2. fleet server
if (Listening 8099) { Say 'fleet server  already up on :8099' }
else {
  Say 'fleet server  starting'
  Start-Process -FilePath "$root\.venv\Scripts\pythonw.exe" -ArgumentList "$root\server\run.py" `
    -WorkingDirectory $root -RedirectStandardError "$root\logs_err.txt" -WindowStyle Hidden
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
