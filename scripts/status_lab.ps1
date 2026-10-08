# AEDI - IONITY GLOBAL | ESP32-MCP status at a glance
# Policy 986 AED | (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd
function Up($p) { $c = New-Object Net.Sockets.TcpClient; try { $ar = $c.BeginConnect('127.0.0.1', $p, $null, $null); ($ar.AsyncWaitHandle.WaitOne(400) -and $c.Connected) } catch { $false } finally { $c.Dispose() } }
function Line($n, $ok, $extra) { Write-Host ('  {0,-16}' -f $n) -NoNewline; Write-Host $(if ($ok) { 'UP  ' } else { 'DOWN' }) -ForegroundColor $(if ($ok) { 'Green' } else { 'Red' }) -NoNewline; Write-Host "  $extra" }
Write-Host 'Ionity ESP32-MCP status'
Line 'MQTT broker' (Up 1883) ':1883'
Line 'fleet server' (Up 8099) ':8099  dashboard + MCP'
Line 'AI gateway' (Up 8000) ':8000  mcpo (OpenAPI)'
$w = @(Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*net_watch.py*' }).Count
Line 'alarm watcher' ($w -gt 0) 'YouTube -> ESP32 red light'
try { $h = Invoke-RestMethod http://127.0.0.1:8099/api/v1/health -TimeoutSec 4
  $f = Invoke-RestMethod http://127.0.0.1:8099/api/v1/fleet/summary -TimeoutSec 4
  Write-Host ("  boards          {0} registered, {1} online, {2} open alerts" -f $f.total_devices, $f.online, $f.open_alerts)
  Write-Host ("  LAN DNS         {0}, {1} lookups since start" -f $(if ($h.dns.running) { 'running' } else { 'off' }), $h.dns.queries) } catch { }
