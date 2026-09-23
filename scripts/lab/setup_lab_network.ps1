# ===========================================================================
# AEDI - IONITY GLOBAL | Lab template: laptop dual-network setup
# Doc ID: DOC-2026-09-LAB-TEMPLATE | Policy 986 AED
# (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd
# ---------------------------------------------------------------------------
# Household WiFi  -> internet (always preferred, metric 10)
# Lab Ethernet    -> H3C lab only (metric 200: never used for internet/DNS)
# Lab Ethernet profile -> Private, and firewall opens the lab ports ONLY to
# the lab subnet. Run it by double-clicking SETUP-LAB-NETWORK.cmd (asks UAC).
# Re-running is safe. Undo: scripts\lab\setup_lab_network.ps1 -Undo
# ===========================================================================
param([switch]$Undo)
$ErrorActionPreference = 'Stop'
$root = Split-Path (Split-Path $PSScriptRoot)
$cfg  = Get-Content (Join-Path $root 'config\lab.json') -Raw | ConvertFrom-Json

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
  $a = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
  if ($Undo) { $a += ' -Undo' }
  Start-Process powershell -Verb RunAs -ArgumentList $a
  exit
}

$log = Join-Path $root 'data\lab-network.log'
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
Start-Transcript -Path $log -Force | Out-Null
$homeNic = $cfg.household.laptop_nic; $lab = $cfg.lab.laptop_nic; $group = 'Ionity Lab'

try {
  if ($Undo) {
    Write-Host "[lab-net] UNDO: automatic metrics back on, lab firewall rules removed"
    foreach ($n in $homeNic, $lab) {
      foreach ($af in 'IPv4','IPv6') { Set-NetIPInterface -InterfaceAlias $n -AddressFamily $af -AutomaticMetric Enabled -ErrorAction SilentlyContinue }
    }
    Get-NetFirewallRule -Group $group -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    Set-DnsClient -InterfaceAlias $lab -RegisterThisConnectionsAddress $true -ErrorAction SilentlyContinue
    Write-Host "[lab-net] undone."
    return
  }

  foreach ($n in $homeNic, $lab) {
    if (-not (Get-NetAdapter -Name $n -ErrorAction SilentlyContinue)) { throw "network adapter '$n' not found (edit config\lab.json)" }
  }

  # 1. Route + DNS preference: household WiFi always wins, the lab never carries internet
  foreach ($af in 'IPv4','IPv6') {
    Set-NetIPInterface -InterfaceAlias $homeNic -AddressFamily $af -AutomaticMetric Disabled -InterfaceMetric $cfg.metrics.household_nic -ErrorAction SilentlyContinue
    Set-NetIPInterface -InterfaceAlias $lab     -AddressFamily $af -AutomaticMetric Disabled -InterfaceMetric $cfg.metrics.lab_nic       -ErrorAction SilentlyContinue
  }
  Set-DnsClient -InterfaceAlias $lab -RegisterThisConnectionsAddress $false
  Write-Host "[lab-net] metrics: $homeNic=$($cfg.metrics.household_nic) (internet)  $lab=$($cfg.metrics.lab_nic) (lab only)"

  # 2. Lab network = Private (a Public profile silently blocks every board)
  $p = Get-NetConnectionProfile -InterfaceAlias $lab -ErrorAction SilentlyContinue
  if ($p) { Set-NetConnectionProfile -InterfaceAlias $lab -NetworkCategory Private; Write-Host "[lab-net] '$($p.Name)' on $lab -> Private" }
  else    { Write-Host "[lab-net] WARNING: $lab has no network yet - plug it into an H3C LAN port and re-run" }

  # 3. Firewall: lab ports open to the lab subnet only (household cannot reach them)
  Get-NetFirewallRule -Group $group -ErrorAction SilentlyContinue | Remove-NetFirewallRule
  $sub = $cfg.lab.subnet
  $rules = @(
    @{n='MQTT broker';       p='TCP'; port=$cfg.ports.mqtt},
    @{n='Fleet server HTTP'; p='TCP'; port=$cfg.ports.http},
    @{n='Lab DNS logger';    p='UDP'; port=$cfg.ports.dns},
    @{n='mDNS discovery';    p='UDP'; port=$cfg.ports.mdns}
  )
  foreach ($r in $rules) {
    New-NetFirewallRule -DisplayName "Ionity Lab - $($r.n)" -Group $group -Direction Inbound -Action Allow `
      -Protocol $r.p -LocalPort $r.port -RemoteAddress $sub -Profile Private,Public | Out-Null
    Write-Host ("[lab-net] firewall: allow {0,-3} {1,-5} from {2}  ({3})" -f $r.p, $r.port, $sub, $r.n)
  }

  # 4. Report
  Write-Host ""
  $route = Find-NetRoute -RemoteIPAddress 1.1.1.1 | Select-Object -Last 1
  Write-Host "[lab-net] internet now goes via: $($route.InterfaceAlias) -> $($route.NextHop)"
  $ip = (Get-NetIPAddress -InterfaceAlias $lab -AddressFamily IPv4 -ErrorAction SilentlyContinue | ? PrefixOrigin -ne 'WellKnown').IPAddress
  Write-Host "[lab-net] laptop on the lab: $ip   (template expects $($cfg.lab.server_ip) - pin it on the H3C)"
  Write-Host "[lab-net] H3C reachable: $(Test-Connection $cfg.lab.router_ip -Count 1 -Quiet)"
  Write-Host "[lab-net] DONE"
}
catch { Write-Host "[lab-net] FAILED: $($_.Exception.Message)" -ForegroundColor Red }
finally {
  Stop-Transcript | Out-Null
  Write-Host "`nLog saved to $log. This window closes in 20 s."
  Start-Sleep 20
}
