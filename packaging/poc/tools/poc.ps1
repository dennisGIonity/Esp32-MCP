# ===========================================================================
# AEDI - IONITY GLOBAL | ESP32-MCP POC edition
# Author: Johan Wilhelm van Antwerp | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd | AEDI
# Governance: Policy 986 AED | License: AED 900 | CC BY-NC-SA 4.0 where stated
# (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd - All Rights Reserved - TM2
# Owner: github.com/Ionity-Global-Pty-Ltd | www.ionity.today | ai@ionity.today
# ---------------------------------------------------------------------------
# Adds the proof-of-concept features on top of the testers kit (tools\tester.ps1):
#   setup   kit setup + LAN DNS on port 53 + fresh admin token / AI-gateway key + mcpo
#   start   kit start + AI gateway (mcpo, :8000, key from .env, never on a command line)
#   watch   YouTube alarm: watched site opened -> ESP32 red light (pwm0) + live report
#   stop    stops everything this folder started
#   status  kit status + AI gateway + alarm watcher
#   ai-key  copies the AI-gateway key to the clipboard (it is never printed)
# ===========================================================================
param([Parameter(Position = 0)][ValidateSet('setup', 'start', 'stop', 'status', 'watch', 'ai-key')][string]$Action = 'status')
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Tester = Join-Path $PSScriptRoot 'tester.ps1'
$Logs = Join-Path $Root 'logs'; New-Item -ItemType Directory -Force $Logs | Out-Null
$EnvFile = Join-Path $Root '.env'
function Say($m, $c = 'Gray') { Write-Host "[ionity] $m" -ForegroundColor $c }
function Secret { $b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); -join ($b | ForEach-Object { $_.ToString('x2') }) }
function EnvVal($k) { if (-not (Test-Path $EnvFile)) { return '' }; $m = [regex]::Match([IO.File]::ReadAllText($EnvFile), "(?m)^[ \t]*$k[ \t]*=(.*)$"); if ($m.Success) { $m.Groups[1].Value.Trim() } else { '' } }
function SetEnv($k, $v, [switch]$OnlyIfEmpty) {
  $t = if (Test-Path $EnvFile) { [IO.File]::ReadAllText($EnvFile) } else { '' }
  $m = [regex]::Match($t, "(?m)^[ \t]*$k[ \t]*=(.*)$")
  if ($m.Success) { if ($OnlyIfEmpty -and $m.Groups[1].Value.Trim()) { return }; $t = $t.Substring(0, $m.Index) + "$k=$v" + $t.Substring($m.Index + $m.Length) }
  else { $t = $t.TrimEnd() + "`r`n$k=$v`r`n" }
  [IO.File]::WriteAllText($EnvFile, $t, (New-Object Text.UTF8Encoding $false)) }
function Listening($p) { $c = New-Object Net.Sockets.TcpClient; try { $ar = $c.BeginConnect('127.0.0.1', $p, $null, $null); ($ar.AsyncWaitHandle.WaitOne(400) -and $c.Connected) } catch { $false } finally { $c.Dispose() } }
function Procs($pat) { @(Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like $pat }) }
function Detach($exe, $argLine, $out, $err) {
  $cl = "cmd.exe /d /c `"`"$exe`" $argLine > `"$out`" 2> `"$err`"`""
  $si = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
  $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cl; CurrentDirectory = $Root; ProcessStartupInformation = $si }
  if ($r.ReturnValue -ne 0) { throw "could not start $exe" } }
$Port = 8099; if (EnvVal 'IONITY_PORT') { $Port = [int](EnvVal 'IONITY_PORT') }

switch ($Action) {
  'setup' {
    & $Tester setup
    if (-not (Test-Path (Join-Path $Root '.venv\Scripts\python.exe'))) { exit 1 }
    SetEnv 'IONITY_DNS_PORT' '53'
    SetEnv 'IONITY_ADMIN_TOKEN' (Secret) -OnlyIfEmpty
    SetEnv 'IONITY_MCPO_API_KEY' (Secret) -OnlyIfEmpty
    if (-not (EnvVal 'IONITY_ALARM_DEVICE')) { SetEnv 'IONITY_ALARM_DEVICE' '' }
    Say 'POC settings: LAN DNS on port 53, fresh admin token + AI-gateway key in .env' Green
    $mv = Join-Path $Root '.venv-mcpo'
    if (-not (Test-Path "$mv\Scripts\python.exe")) { Say 'creating AI gateway environment (.venv-mcpo)'; & (Join-Path $Root '.venv\Scripts\python.exe') -m venv $mv }
    & "$mv\Scripts\python.exe" -m pip install -q --disable-pip-version-check mcpo 'mcp>=1.24,<2'
    if ($LASTEXITCODE) { Say 'AI gateway install failed (optional) - check the internet connection and run SETUP again' Yellow } else { Say 'AI gateway (mcpo) ready' Green }
    Say 'Next: START.cmd, then FLASH-BOARD.cmd for each ESP32. See the User Guide for the DNS step.' Cyan
  }
  'start' {
    & $Tester start
    $mp = Join-Path $Root '.venv-mcpo\Scripts\python.exe'
    if (Listening 8000) { Say 'AI gateway      already up on :8000' }
    elseif (Test-Path $mp) {
      Detach $mp "`"$Root\scripts\run_mcpo.py`"" "$Logs\mcpo_out.txt" "$Logs\mcpo_err.txt"
      for ($i = 0; $i -lt 30 -and -not (Listening 8000); $i++) { Start-Sleep 1 }
      if (Listening 8000) { Say 'AI gateway      up on :8000  (docs: /docs, key: COPY-AI-KEY.cmd)' Green } else { Say "AI gateway      did not start - see $Logs\mcpo_err.txt" Yellow } }
    Say 'YouTube alarm:  double-click WATCH-YOUTUBE-ALARM.cmd' Cyan
  }
  'watch' {
    $dev = EnvVal 'IONITY_ALARM_DEVICE'
    if (-not $dev) {
      try { $b = '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_devices","arguments":{"limit":100}}}'
            $r = (Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/v1/mcp/rpc" -Method Post -Body $b -ContentType 'application/json').result.content[0].text | ConvertFrom-Json
            $d = @($r.devices) | Where-Object { $_.health -eq 'online' -and $_.device_id -like 'esp32-*' } | Select-Object -First 1
            if ($d) { $dev = $d.device_id; Say "alarm board: $($d.label) ($dev) - set IONITY_ALARM_DEVICE in .env to choose another" Cyan } } catch { } }
    if (-not $dev) { Say 'No ESP32 is online yet. Start the system and flash a board first.' Yellow; exit 1 }
    $rep = Join-Path $Root 'reports\network-report.html'; New-Item -ItemType Directory -Force (Split-Path $rep) | Out-Null
    Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoProfile', '-Command', "Start-Sleep 5; Start-Process '$rep'"
    Say 'Watching. Open YouTube on any device that uses this PC as DNS. Ctrl+C to stop.' Green
    & (Join-Path $Root '.venv\Scripts\python.exe') (Join-Path $Root 'scripts\net_watch.py') --base "http://127.0.0.1:$Port" --device $dev --report $rep
  }
  'stop' {
    foreach ($p in (Procs '*net_watch.py*') + (Procs '*run_mcpo.py*')) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    Get-Process mcpo -ErrorAction SilentlyContinue | Stop-Process -Force
    Say 'AI gateway and alarm watcher stopped'
    & $Tester stop
    $mine = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | ForEach-Object { $_.IPAddress })
    $dns = @(Get-DnsClientServerAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.ServerAddresses | Where-Object { $mine -contains $_ } })
    if ($dns.Count) { Say ('WARNING: ' + (($dns | ForEach-Object { $_.InterfaceAlias }) -join ', ') + ' still uses this PC as DNS - reset it (User Guide, section 8) or this PC loses internet.') Yellow }
  }
  'status' {
    & $Tester status
    Say ('AI gateway      ' + $(if (Listening 8000) { 'UP   :8000' } else { 'down' }))
    Say ('alarm watcher   ' + $(if ((Procs '*net_watch.py*').Count) { 'running' } else { 'not running (WATCH-YOUTUBE-ALARM.cmd)' }))
  }
  'ai-key' {
    $k = EnvVal 'IONITY_MCPO_API_KEY'
    if ($k) { Set-Clipboard -Value $k; Say 'AI-gateway key copied to the clipboard. Paste it where a client asks for a Bearer key / API key.' Green }
    else { Say 'No AI-gateway key yet - run SETUP.cmd' Yellow }
  }
}
