# ===========================================================================
# AEDI - IONITY GLOBAL | Lab template: one-look health check
# Doc ID: DOC-2026-09-LAB-TEMPLATE | Policy 986 AED
# Checks the whole template: household internet, lab link, lab services,
# boards on the lab subnet, and that nothing lab-related is on the household.
# ===========================================================================
$ErrorActionPreference = 'SilentlyContinue'
$root = Split-Path (Split-Path $PSScriptRoot)
$cfg  = Get-Content (Join-Path $root 'config\lab.json') -Raw | ConvertFrom-Json
$ok = 0; $bad = 0
function Check($name, [bool]$pass, $detail) {
  $script:ok += [int]$pass; $script:bad += [int](-not $pass)
  $mark = if ($pass) { 'OK  ' } else { 'FAIL' }
  $col  = if ($pass) { 'Green' } else { 'Red' }
  Write-Host ("[{0}] {1,-34} {2}" -f $mark, $name, $detail) -ForegroundColor $col
}

Write-Host "`n== Household ($($cfg.household.router)) =="
$route = Find-NetRoute -RemoteIPAddress 1.1.1.1 | Select-Object -Last 1
Check 'internet goes via household NIC' ($route.InterfaceAlias -eq $cfg.household.laptop_nic) "$($route.InterfaceAlias) -> $($route.NextHop)"
$web = try { (Invoke-WebRequest http://www.google.com/generate_204 -UseBasicParsing -TimeoutSec 6).StatusCode -eq 204 } catch { $false }
Check 'internet reachable' $web 'google generate_204'

Write-Host "`n== Lab ($($cfg.lab.router)) =="
$labIp = (Get-NetIPAddress -InterfaceAlias $cfg.lab.laptop_nic -AddressFamily IPv4 | ? PrefixOrigin -ne 'WellKnown').IPAddress
Check 'laptop has pinned lab address' ($labIp -eq $cfg.lab.server_ip) "$($cfg.lab.laptop_nic) = $labIp (want $($cfg.lab.server_ip))"
Check 'lab router reachable' (Test-Connection $cfg.lab.router_ip -Count 1 -Quiet) $cfg.lab.router_ip
$prof = (Get-NetConnectionProfile -InterfaceAlias $cfg.lab.laptop_nic).NetworkCategory
Check 'lab network is Private' ("$prof" -eq 'Private') "$prof"
Check 'lab firewall rules present' ((Get-NetFirewallRule -Group 'Ionity Lab' | Measure-Object).Count -ge 4) 'group Ionity Lab'

Write-Host "`n== Lab services (this laptop) =="
$h = try { Invoke-RestMethod "http://127.0.0.1:$($cfg.ports.http)/api/v1/health" -TimeoutSec 5 } catch { $null }
Check 'fleet server up' ([bool]$h) "http :$($cfg.ports.http)"
Check 'MQTT broker connected' ([bool]$h.mqtt.connected) "$($h.mqtt.host)"
Check 'mDNS advertises lab address' ($h.discovery.advertised_ip -eq $cfg.lab.server_ip) "$($h.discovery.hostname) -> $($h.discovery.advertised_ip)"
Check 'DNS logger on lab only' ($h.dns.bind -like "$($cfg.lab.server_ip)*") "bind $($h.dns.bind)"

Write-Host "`n== Boards =="
$devs = try { (Invoke-RestMethod "http://127.0.0.1:$($cfg.ports.http)/api/v1/devices?limit=500" -TimeoutSec 5).devices } catch { @() }
$prefix = ($cfg.lab.subnet -split '\.')[0..2] -join '.'
foreach ($d in $devs) {
  $where = if ($d.transport -eq 'serial') { 'USB serial' } else { $d.ip }
  $onLab = ($d.transport -eq 'serial') -or ("$($d.ip)" -like "$prefix.*")
  Check "$($d.device_id)" (($d.health -eq 'online') -and $onLab) "$($d.health), $where, fw $($d.fw)$(if (-not $onLab) { '  <- still on household network' })"
}
if (-not $devs) { Check 'boards reporting' $false 'none - is the server up?' }

Write-Host "`n$ok passed, $bad failed.`n"
