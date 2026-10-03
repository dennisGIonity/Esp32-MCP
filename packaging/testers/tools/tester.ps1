# ===========================================================================
# AEDI - IONITY GLOBAL | ESP32-MCP Testers Package - control script
# Policy 986 AED | License AED 900 | (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd
# Web: https://www.ionity.today | Ref: https://www.ionity.co.za
# ---------------------------------------------------------------------------
# One script behind every .cmd in this package:
#   setup | start | stop | status | open | flasher | tests | demo | purge-demo
# Runs from wherever the package was unzipped. No admin rights needed.
# Nothing here deletes your files: 'stop' only stops processes this package started.
# ===========================================================================
param(
  [Parameter(Position = 0)][ValidateSet('setup','start','stop','status','open','flasher','tests','demo','purge-demo')]
  [string]$Action = 'status'
)
$ErrorActionPreference = 'Stop'
$Root   = Split-Path -Parent $PSScriptRoot
$Venv   = Join-Path $Root '.venv'
$Py     = Join-Path $Venv 'Scripts\python.exe'
$BrVenv = Join-Path $Root '.venv-broker'
$BrPy   = Join-Path $BrVenv 'Scripts\python.exe'
$Logs   = Join-Path $Root 'logs'
$Port   = 8099
Set-Location $Root
New-Item -ItemType Directory -Force -Path $Logs, (Join-Path $Root 'data') | Out-Null

function Say($m, $c = 'Gray') { Write-Host "[ionity] $m" -ForegroundColor $c }
function Listening($p) { [bool](Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue) }
function Ours($pattern) {
  @(Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*$Root*" -and $_.CommandLine -like $pattern })
}
function Find-Python {
  foreach ($c in @('py -3.13', 'py -3.12', 'py -3.14', 'py -3.11', 'python')) {
    $exe, $arg = $c.Split(' ', 2)
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
    try {
      $v = & $exe @($arg | Where-Object { $_ }) -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
      if ($v -and [version]$v -ge [version]'3.11') { return @{ Exe = $exe; Arg = $arg; Ver = $v } }
    } catch {}
  }
  return $null
}
function Need-Setup { if (-not (Test-Path $Py)) { Say 'Not set up yet - run SETUP.cmd first.' Yellow; exit 1 } }
function Lan-IP {
  $ip = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' -and $_.PrefixOrigin -ne 'WellKnown' } |
    Sort-Object InterfaceMetric | Select-Object -First 1
  if ($ip) { $ip.IPAddress } else { '127.0.0.1' }
}

function Do-Setup {
  Say 'Ionity ESP32-MCP testers setup' Cyan
  $p = Find-Python
  if (-not $p) {
    Say 'Python 3.11+ was not found. Install it from https://www.python.org/downloads/ (tick "Add to PATH"), then run SETUP.cmd again.' Red
    exit 1
  }
  Say "Python $($p.Ver) found"
  if (-not (Test-Path $Py)) {
    Say 'creating virtual environment (.venv)'
    & $p.Exe @($p.Arg | Where-Object { $_ }) -m venv $Venv
  }
  if (-not (Test-Path $BrPy)) {
    Say 'creating broker environment (.venv-broker) - kept separate so its pins never clash with the server'
    & $p.Exe @($p.Arg | Where-Object { $_ }) -m venv $BrVenv
  }
  Say 'installing server packages (first run takes 1-3 minutes)'
  $env:SKIP_CYTHON = '1'   # zeroconf: pure-Python wheel; avoids Smart App Control blocking compiled extensions
  & $Py -m pip install --disable-pip-version-check -q --upgrade pip
  & $Py -m pip install --disable-pip-version-check -q -r (Join-Path $Root 'server\requirements.txt')
  if ($LASTEXITCODE -ne 0) { Say 'server package install failed - see the messages above' Red; exit 1 }
  Say 'installing MQTT broker packages'
  & $BrPy -m pip install --disable-pip-version-check -q -r (Join-Path $Root 'broker\requirements.txt')
  if ($LASTEXITCODE -ne 0) { Say 'broker package install failed - see the messages above' Red; exit 1 }
  $envFile = Join-Path $Root '.env'
  if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $Root 'tools\tester.env') $envFile
    Say 'created .env from tools\tester.env (edit it to change ports or add an admin token)'
  } else { Say '.env already exists - left unchanged' }
  Say 'setup complete. Next: START.cmd' Green
}

function Env-Value($key, $default) {
  $f = Join-Path $Root '.env'
  if (Test-Path $f) {
    $line = Get-Content $f | Where-Object { $_ -match "^\s*$key\s*=" } | Select-Object -Last 1
    if ($line) { $v = ($line -split '=', 2)[1].Trim().Trim('"'); if ($v) { return $v } }
  }
  return $default
}
$Port     = [int](Env-Value 'IONITY_PORT' 8099)
$MqttPort = [int](Env-Value 'IONITY_MQTT_PORT' 1883)
$Base     = "http://127.0.0.1:$Port"

function Do-Start {
  Need-Setup
  if (Listening $MqttPort) { Say "MQTT broker   already listening on :$MqttPort" }
  else {
    Say "MQTT broker   starting on :$MqttPort"
    Start-Process -FilePath $BrPy -ArgumentList "`"$Root\broker\run_broker.py`" 0.0.0.0:$MqttPort" -WorkingDirectory $Root `
      -RedirectStandardOutput "$Logs\broker_out.txt" -RedirectStandardError "$Logs\broker_err.txt" -WindowStyle Hidden
    for ($i = 0; $i -lt 20 -and -not (Listening $MqttPort); $i++) { Start-Sleep -Milliseconds 500 }
  }
  if (Listening $Port) { Say "fleet server  already listening on :$Port" }
  else {
    Say "fleet server  starting on :$Port"
    Start-Process -FilePath $Py -ArgumentList "`"$Root\server\run.py`"" -WorkingDirectory $Root `
      -RedirectStandardOutput "$Logs\server_out.txt" -RedirectStandardError "$Logs\server_err.txt" -WindowStyle Hidden
    for ($i = 0; $i -lt 40 -and -not (Listening $Port); $i++) { Start-Sleep 1 }
  }
  Do-Status
  if ((Listening $Port) -and -not $env:IONITY_NO_BROWSER) { Start-Process "$Base/" }
}

function Do-Stop {
  $n = 0
  foreach ($pat in '*device_emulator.py*', '*server\run.py*', '*broker\run_broker.py*') {
    Ours $pat | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $n++ }
  }
  Say "stopped $n process(es) started from this package" Green
}

function Do-Status {
  Say ("broker  :{0}  {1}" -f $MqttPort, $(if (Listening $MqttPort) { 'UP' } else { 'down' }))
  try {
    $h = Invoke-RestMethod "$Base/api/v1/health" -TimeoutSec 6
    $s = Invoke-RestMethod "$Base/api/v1/fleet/summary" -TimeoutSec 6
    Say ("server  :{0}  UP   mqtt={1}  mcp={2} ({3} tools)" -f $Port, $h.mqtt.connected, $h.mcp.server, $h.mcp.tools) Green
    Say ("fleet   total={0} online={1} stale={2} offline={3} alerting={4}" -f $s.total_devices, $s.online, $s.stale, $s.offline, $s.alerting)
    Say ("dashboard  $Base/      (other PCs on the LAN: http://{0}:{1}/)" -f (Lan-IP), $Port) Cyan
    Say ("flasher    $Base/flasher/")
    Say ("MCP        $Base/api/v1/mcp/rpc")
  } catch { Say ("server  :{0}  down  (logs: {1})" -f $Port, "$Logs\server_err.txt") Yellow }
}

function Do-Tests {
  Need-Setup
  Say 'running the server test suite'
  & $Py -m pytest -q (Join-Path $Root 'server\tests')
  if ($LASTEXITCODE -eq 0) { Say 'all tests passed' Green } else { Say "tests failed (exit $LASTEXITCODE)" Red }
}

function Do-Demo {
  Need-Setup
  if (-not (Listening $MqttPort)) { Say 'start the stack first (START.cmd)' Yellow; exit 1 }
  if ((Ours '*device_emulator.py*').Count) { Say 'demo device already running' ; return }
  Say 'starting ONE simulated device (esp32-emu000000001, group "emulated") - for testers without hardware'
  Start-Process -FilePath $Py -ArgumentList "`"$Root\scripts\device_emulator.py`" --mqtt-port $MqttPort --label `"DEMO - simulated`"" `
    -WorkingDirectory $Root -RedirectStandardOutput "$Logs\demo_out.txt" -RedirectStandardError "$Logs\demo_err.txt" -WindowStyle Hidden
  Say 'it appears on the dashboard within ~10 s. Remove it later with PURGE-DEMO.cmd' Green
}

function Do-PurgeDemo {
  Need-Setup
  Ours '*device_emulator.py*' | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  if (Listening $MqttPort) {
    # The demo's Last Will is RETAINED on the broker; clear it or the server re-creates the device on restart.
    $clear = "import paho.mqtt.client as m;c=m.Client(m.CallbackAPIVersion.VERSION2);c.connect('127.0.0.1',$MqttPort);c.loop_start();" +
             "[c.publish(f'ionity/lab/esp32-emu000000001/{t}',b'',qos=1,retain=True).wait_for_publish(3) for t in ('status','telemetry')];c.loop_stop();c.disconnect()"
    & $Py -c $clear
  }
  & $Py (Join-Path $Root 'scripts\purge_devices.py') --pattern 'esp32-emu%' --db (Join-Path $Root 'data\fleet.db') --commit
  if (Listening $Port) {   # the server caches the registry in memory - restart it so the purge shows
    Ours '*server\run.py*' | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep 2; Do-Start
  }
  Say 'demo devices removed (refresh the dashboard)' Green
}

switch ($Action) {
  'setup'      { Do-Setup }
  'start'      { Do-Start }
  'stop'       { Do-Stop }
  'status'     { Do-Status }
  'open'       { Start-Process "$Base/" }
  'flasher'    { Start-Process "$Base/flasher/"; Say 'Use Chrome or Edge - the flasher needs Web Serial.' }
  'tests'      { Do-Tests }
  'demo'       { Do-Demo }
  'purge-demo' { Do-PurgeDemo }
}
