# ===========================================================================
# AEDI - IONITY GLOBAL | Start the fleet server if it isn't already running
# Policy 986 AED | (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd
# ===========================================================================
param([switch]$Restart, [int]$Port = 8099)

$root = 'E:\.ESP32-MCP'
Set-Location $root

$listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listening -and -not $Restart) {
  Write-Host "[fleet] already running on :$Port (pid $($listening[0].OwningProcess))"
} else {
  if ($listening) {
    Write-Host "[fleet] restarting..."
    $listening | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
    Start-Sleep 3
  }
  Write-Host "[fleet] starting..."
  Start-Process -FilePath "$root\.venv\Scripts\python.exe" -ArgumentList "$root\server\run.py" `
    -RedirectStandardOutput "$root\logs_out.txt" -RedirectStandardError "$root\logs_err.txt" `
    -WindowStyle Hidden
  Start-Sleep 12
}

try {
  $h = Invoke-RestMethod "http://127.0.0.1:$Port/api/v1/health" -TimeoutSec 8
  Write-Host ("[fleet] UP  devices={0}  queue={1}  mqtt={2}  dns={3} ({4} queries)" -f `
    $h.devices_known, $h.queue_depth, $h.mqtt.connected, $h.dns.running, $h.dns.queries)
  $s = Invoke-RestMethod "http://127.0.0.1:$Port/api/v1/fleet/summary" -TimeoutSec 8
  Write-Host ("[fleet] online={0} stale={1} offline={2} alerting={3}" -f `
    $s.online, $s.stale, $s.offline, $s.alerting)
  Write-Host "[fleet] dashboard  http://192.168.2.11:$Port/"
  Write-Host "[fleet] mcp  (http) http://192.168.2.11:$Port/api/v1/mcp/rpc"
} catch {
  Write-Host "[fleet] FAILED to come up. Last errors:"
  Get-Content "$root\logs_err.txt" -Tail 15 -ErrorAction SilentlyContinue
  exit 1
}
