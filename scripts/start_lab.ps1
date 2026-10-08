# ===========================================================================
# AEDI - IONITY GLOBAL | Bring ESP32-MCP up inside the Ionity Lab (idempotent)
# Policy 986 AED | (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd
# ---------------------------------------------------------------------------
# The lab itself (network, shared MQTT broker, Pi tools) is its own project:
# Ionity-Lab, E:\.claude\Ionity\.IONITY-LAB (override with IONITY_LAB_HOME). This script:
#   1. MQTT broker :1883 - the Ionity Lab's if installed, else the bundled one (broker\, .venv-broker)
#   2. starts the fleet server             :8099   server\run.py            (.venv)
#   3. starts the serial bridge                    scripts\serial_bridge.py (.venv)
#   4. starts mcpo (optional)              :8000   OpenAPI/REST for the MCP tools (.venv-mcpo)
#      one-time setup:  python -m venv .venv-mcpo
#                       .venv-mcpo\Scripts\python -m pip install mcpo "mcp>=1.24,<2"
#      (mcp 2.x renamed streamablehttp_client and breaks mcpo 0.0.20)
#      API key: IONITY_MCPO_API_KEY in .env (clients send Authorization: Bearer <key>),
#      loaded in-process by scripts\run_mcpo.py so it never shows on a command line.
#      Device writes on :8099 need IONITY_ADMIN_TOKEN (.env); the stdio proxy adds it.
#
# Order matters: broker first, so the fleet server's MQTT client connects on
# its first attempt instead of after a back-off.
#
# Run at logon by the Startup shortcut, by the MCP bridge if it finds the
# server down, and by the lab's own "lab.ps1 start". Safe to run repeatedly.
# -Restart restarts this project's services only; the broker belongs to the lab.
# ===========================================================================
param([switch]$Restart, [switch]$Quiet)

$root = if ($env:IONITY_ROOT) { $env:IONITY_ROOT } else { Split-Path -Parent $PSScriptRoot }   # works wherever the folder is unpacked
$labHome = if ($env:IONITY_LAB_HOME) { $env:IONITY_LAB_HOME } else { 'E:\.claude\Ionity\.IONITY-LAB' }
Set-Location $root
$logs = Join-Path $root 'logs'   # runtime logs live here (git-ignored), not in the repo root
New-Item -ItemType Directory -Force $logs | Out-Null
function Say($m) { if (-not $Quiet) { Write-Host "[lab] $m" } }
function Start-Detached($exe, $argLine, $outFile, $errFile, $cwd) {
  # No inherited handles: Start-Process lets children inherit this shell's stdout
  # pipe, so any wrapper capturing our output blocks until the service exits.
  $cl = "cmd.exe /d /c `"`"$exe`" $argLine > `"$outFile`" 2> `"$errFile`"`""
  $si = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
  $r  = Invoke-CimMethod -ClassName Win32_Process -MethodName Create `
          -Arguments @{ CommandLine = $cl; CurrentDirectory = $cwd; ProcessStartupInformation = $si }
  if ($r.ReturnValue -ne 0) { throw "could not start $exe (Win32_Process.Create rc=$($r.ReturnValue))" }
  return $r.ProcessId
}
function Listening($p) {
  # Real connect test: Get-NetTCPConnection -State Listen misses some Python sockets
  # on Windows (empty State), which started duplicate brokers/servers.
  $c = New-Object Net.Sockets.TcpClient
  try { $ar = $c.BeginConnect('127.0.0.1', [int]$p, $null, $null)
        if ($ar.AsyncWaitHandle.WaitOne(400) -and $c.Connected) { return $true } ; return $false }
  catch { return $false } finally { $c.Dispose() }
}
function ProcLike($pattern) {
  @(Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like $pattern })
}
function Stop-Like($pattern) { ProcLike $pattern | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } }

if ($Restart) {
  Say 'restarting ESP32-MCP services (fleet server + serial bridge + mcpo)'
  Stop-Like '*serial_bridge.py*'; Stop-Like '*server\run.py*'
  Stop-Like '*run_mcpo.py*'; Get-Process mcpo -ErrorAction SilentlyContinue | Stop-Process -Force
  Start-Sleep 3
}

# 1. broker - owned by the Ionity Lab
if (Listening 1883) { Say 'broker        already up on :1883' }
elseif (Test-Path "$labHome\lab.ps1") {
  & "$labHome\lab.ps1" broker -Quiet:$Quiet
}
elseif (Test-Path "$root\.venv-broker\Scripts\python.exe") {
  Say 'broker        starting (bundled) on :1883'
  Start-Detached "$root\.venv-broker\Scripts\python.exe" "`"$root\broker\run_broker.py`" 0.0.0.0:1883" "$logs\broker_out.txt" "$logs\broker_err.txt" $root | Out-Null
  for ($i = 0; $i -lt 20 -and -not (Listening 1883); $i++) { Start-Sleep 1 }
  if (Listening 1883) { Say 'broker        up on :1883' } else { Say "broker        FAILED to start - see $logs\broker_err.txt" }
}
else {
  Say "broker        DOWN: no Ionity Lab at $labHome and no bundled broker - run INSTALL (scripts\install.ps1)"
  Say '              (the fleet server still runs; devices fall back to HTTP until the broker is up)'
}

# 2. fleet server
if (Listening 8099) { Say 'fleet server  already up on :8099' }
else {
  Say 'fleet server  starting'
  # python.exe with BOTH streams redirected, not pythonw: under pythonw sys.stdout is
  # None and uvicorn's log formatter dies at startup ("Unable to configure formatter
  # 'default'") - the server then never listens and the MCP tools go dark.
  Start-Detached "$root\.venv\Scripts\python.exe" "`"$root\server\run.py`"" "$logs\fleet_out.txt" "$logs\fleet_err.txt" $root | Out-Null
  for ($i = 0; $i -lt 40 -and -not (Listening 8099); $i++) { Start-Sleep 1 }
}

# 3. serial bridge
if ((ProcLike '*serial_bridge.py*').Count -gt 0) { Say 'serial bridge already running' }
else {
  Say 'serial bridge starting'
  Start-Detached "$root\.venv\Scripts\python.exe" "`"$root\scripts\serial_bridge.py`"" "$logs\bridge_out.txt" "$logs\bridge_err.txt" $root | Out-Null
}

# 4. mcpo - OpenAPI/REST front-end for the MCP tools (after the fleet server: its
#    stdio proxy forwards every call to :8099). Optional: skipped if not installed.
$mcpoPy = "$root\.venv-mcpo\Scripts\python.exe"
if (Listening 8000) { Say 'mcpo          already up on :8000' }
elseif (Test-Path $mcpoPy) {
  Say 'mcpo          starting on :8000'
  # scripts\run_mcpo.py reads IONITY_MCPO_API_KEY (environment or .env) and hands it to mcpo
  # in-process, so the key never shows on a process command line. Clients send
  # 'Authorization: Bearer <key>'. Writes then reach :8099 with IONITY_ADMIN_TOKEN, added by the stdio proxy.
  if (-not ($env:IONITY_MCPO_API_KEY -or (Select-String -Path "$root\.env" -Pattern '^\s*IONITY_MCPO_API_KEY\s*=\s*\S' -Quiet -ErrorAction SilentlyContinue))) {
    Say 'mcpo          WARNING: no IONITY_MCPO_API_KEY set - open to the whole LAN' }
  Start-Detached $mcpoPy "`"$root\scripts\run_mcpo.py`"" "$logs\mcpo_out.txt" "$logs\mcpo_err.txt" $root | Out-Null
  for ($i = 0; $i -lt 30 -and -not (Listening 8000); $i++) { Start-Sleep 1 }
}
else { Say 'mcpo          not installed (.venv-mcpo) - optional, see header' }

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
    if (Listening 8000) { Say ("mcpo       http://{0}:8000/docs" -f $h.discovery.advertised_ip) }
  } catch { Say "fleet server did not answer - see $logs\fleet_err.txt" }
}
