# ===========================================================================
# AEDI - IONITY GLOBAL | Lab template: point every board at the lab network
# Doc ID: DOC-2026-09-LAB-TEMPLATE | Policy 986 AED
# (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd
# ---------------------------------------------------------------------------
# Asks for the lab WiFi name + password (password typed hidden, never logged,
# never committed) and writes them into the git-ignored secrets.h of every
# sketch. Also points the server fallback, mDNS advertisement and DNS logger
# at the laptop's lab address from config\lab.json.
# Afterwards reflash each board:  scripts\add_device.ps1 -Port COMx
# ===========================================================================
param([string]$Ssid)
$ErrorActionPreference = 'Stop'
$root = Split-Path (Split-Path $PSScriptRoot)
$cfgPath = Join-Path $root 'config\lab.json'
$cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json

if (-not $Ssid) {
  $def = if ($cfg.lab.wifi_ssid) { $cfg.lab.wifi_ssid } else { 'IONITY-LAB' }
  $Ssid = Read-Host "Lab WiFi name (2.4 GHz) [$def]"
  if (-not $Ssid) { $Ssid = $def }
}
$sec = Read-Host "Password for '$Ssid' (hidden)" -AsSecureString
$pw  = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
if ($pw.Length -lt 8) { throw "WPA2 passwords are at least 8 characters - nothing changed." }

function CStr([string]$s) { '"' + ($s -replace '\\','\\' -replace '"','\"') + '"' }
function Set-Define([string]$file, [string]$name, [string]$value) {
  $txt = Get-Content $file -Raw
  $pat = "(?m)^(\s*#define\s+$name\s+).*$"
  if ($txt -match $pat) { $txt = [regex]::Replace($txt, $pat, { param($m) $m.Groups[1].Value + $value }) }
  else { $txt = $txt.TrimEnd() + "`r`n#define $name $value`r`n" }
  [IO.File]::WriteAllText($file, $txt)
}

# 1. secrets.h in every sketch (git-ignored)
$sketches = Get-ChildItem (Join-Path $root 'firmware-arduino') -Directory | ? { Test-Path (Join-Path $_.FullName 'secrets.h') }
foreach ($s in $sketches) {
  $f = Join-Path $s.FullName 'secrets.h'
  Set-Define $f 'WIFI_SSID'     (CStr $Ssid)
  Set-Define $f 'WIFI_PASSWORD' (CStr $pw)
  Write-Host "[lab-wifi] $($s.Name)\secrets.h -> SSID '$Ssid'"
}
$pw = $null

# 2. server fallback address baked into the firmware (not secret)
$labIp = $cfg.lab.server_ip
foreach ($s in Get-ChildItem (Join-Path $root 'firmware-arduino') -Directory) {
  $c = Join-Path $s.FullName 'config.h'
  if ((Test-Path $c) -and ((Get-Content $c -Raw) -match 'SERVER_HOST_FALLBACK')) {
    Set-Define $c 'SERVER_HOST_FALLBACK' (CStr $labIp)
    Write-Host "[lab-wifi] $($s.Name)\config.h fallback -> $labIp"
  }
}

# 3. server side: advertise + DNS logger on the lab address only (.env, git-ignored)
$envFile = Join-Path $root '.env'
$lines = if (Test-Path $envFile) { @(Get-Content $envFile) } else { @('# Local overrides - git-ignored.') }
$want = @{ 'IONITY_MDNS_ADVERTISE_IP' = $labIp; 'IONITY_DNS_BIND' = $labIp }
foreach ($k in $want.Keys) {
  $lines = @($lines | ? { $_ -notmatch "^$k=" }) + "$k=$($want[$k])"
}
Set-Content $envFile $lines -Encoding UTF8
Write-Host "[lab-wifi] .env -> mDNS + DNS logger bound to $labIp (household can no longer see them)"

# 4. remember the SSID in the template config (no password)
$cfg.lab.wifi_ssid = $Ssid
$cfg | ConvertTo-Json -Depth 5 | Set-Content $cfgPath -Encoding UTF8
Write-Host "[lab-wifi] config\lab.json -> wifi_ssid '$Ssid'"
Write-Host ""
Write-Host "Next: restart the lab (scripts\start_lab.ps1 -Restart), then reflash each board:"
Write-Host "      scripts\add_device.ps1 -Port COMx"
