# AEDI - IONITY GLOBAL | Stop every ESP32-MCP service started from this folder
# Policy 986 AED | (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd
# Stops the watcher, AI gateway, serial bridge, fleet server and this folder's own
# MQTT broker. A broker owned by a separate Ionity Lab install is left alone.
$root = Split-Path -Parent $PSScriptRoot
function Say($m, $c = 'Cyan') { Write-Host "[stop] $m" -ForegroundColor $c }
function KillLike($pattern, $name) {
  $p = @(Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like $pattern })
  $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  Say ('{0,-15} {1}' -f $name, $(if ($p.Count) { 'stopped' } else { 'was not running' })) }
KillLike '*net_watch.py*' 'alarm watcher'
KillLike '*run_mcpo.py*' 'AI gateway'; Get-Process mcpo -ErrorAction SilentlyContinue | Stop-Process -Force
KillLike '*serial_bridge.py*' 'serial bridge'
KillLike '*server\run.py*' 'fleet server'
KillLike "*$root\packaging\broker\run_broker.py*" 'MQTT broker'
$mine = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | ForEach-Object { $_.IPAddress })
$dns = @(Get-DnsClientServerAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.ServerAddresses | Where-Object { $mine -contains $_ } })
if ($dns.Count) { Say ('WARNING: ' + (($dns | ForEach-Object { $_.InterfaceAlias }) -join ', ') + ' still uses this PC as DNS. Reset it or this PC loses internet:') 'Yellow'
  Write-Host ("   Set-DnsClientServerAddress -InterfaceAlias '" + (($dns | ForEach-Object { $_.InterfaceAlias }) -join "','") + "' -ResetServerAddresses   (Administrator PowerShell)") }
