# ===========================================================================
# AEDI - IONITY GLOBAL | Flash + provision an ESP32 that is plugged into the lab Pi
# Doc ID: DOC-2026-09-ESP32MCP-PIDEPLOY | Policy 986 AED
# (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd
# ---------------------------------------------------------------------------
# The Pi 5 sits on the isolated lab network with no internet, so everything it
# needs is copied over SSH: the image, scripts/provision.py and (first time
# only) an offline esptool wheel set.
#
#   python firmware\build.py --lab esp32s3_uart          # image with the lab WiFi baked in
#   scripts\deploy_pi_board.ps1 -Image firmware\dist-lab\esp32s3_uart.bin -Label "lab-node-01 (S3 16MB, CH340)"
#   scripts\deploy_pi_board.ps1 ... -Wheels C:\path\esptool-aarch64.tar   # first run, Pi offline
#
# Full erase by default so the new image seeds NVS from secrets.h (lab WiFi)
# and nothing stale from an older firmware survives.
# ===========================================================================
param(
  [Parameter(Mandatory)] [string]$Image,
  [string]$Label = "",
  [string]$PiHost = "192.168.124.3",
  [string]$User = "wabapi",
  [string]$Key = "$env:USERPROFILE\.ssh\id_ed25519",
  [string]$Port = "",                 # default: first /dev/serial/by-id entry
  [string]$Server = "",               # MCP host for the board; "" = mDNS ionity-fleet.local
  [string]$Site = "lab",
  [string]$Group = "bench",
  [string]$Wheels = "",               # tar of esptool + deps wheels for aarch64 (offline Pi)
  [switch]$KeepNvs
)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$ssh = @("-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-i", $Key)
$dst = "$User@$PiHost"
function Say($m) { Write-Host "[pi-deploy] $m" }
function Remote($cmd) {
  $out = & ssh @ssh $dst $cmd 2>&1 | Where-Object { $_ -notmatch "Pseudo-terminal" }
  if ($LASTEXITCODE -ne 0) { $out | ForEach-Object { Write-Host "  $_" }; throw "remote command failed: $cmd" }
  $out
}

Say "copying image + provision.py to ${dst}:~/ionity-flash"
Remote "mkdir -p ~/ionity-flash" | Out-Null
& scp @ssh $Image "$dst`:ionity-flash/image.bin" | Out-Null
& scp @ssh "$root\scripts\provision.py" "$dst`:ionity-flash/provision.py" | Out-Null

$has = Remote "test -x ~/ionity-flash/venv/bin/esptool.py && echo yes || echo no"
if ("$has".Trim() -ne "yes") {
  Say "installing esptool into ~/ionity-flash/venv"
  if ($Wheels) {
    & scp @ssh $Wheels "$dst`:ionity-flash/wheels.tar" | Out-Null
    Remote "cd ~/ionity-flash && mkdir -p wheels && tar -xf wheels.tar -C wheels && python3 -m venv --system-site-packages venv && venv/bin/pip install -q --no-index --find-links wheels esptool" | Out-Null
  } else {
    Remote "cd ~/ionity-flash && python3 -m venv --system-site-packages venv && venv/bin/pip install -q esptool" | Out-Null
  }
}

if (-not $Port) { $Port = ("" + (Remote "ls /dev/serial/by-id/* 2>/dev/null | head -1")).Trim() }
if (-not $Port) { throw "no USB serial device on the Pi (ls /dev/serial/by-id)" }
Say "board on $Port"
Remote "sudo -n fuser -k $Port 2>/dev/null; true" | Out-Null      # free the port (serial monitors)

$py = "~/ionity-flash/venv/bin/python"
$chip = Remote "$py -m esptool --port $Port chip_id 2>&1 | grep -E 'Chip is|MAC:' | head -2"
$chip | ForEach-Object { Say "  $_" }
if (-not $KeepNvs) {
  Say "erasing flash"
  Remote "$py -m esptool --port $Port erase_flash >/dev/null" | Out-Null
}
Say "writing $([IO.Path]::GetFileName($Image))"
Remote "$py -m esptool --port $Port --baud 460800 write_flash -z --flash_size keep 0x0 ~/ionity-flash/image.bin | grep -E 'Wrote|Hash'" | ForEach-Object { Say "  $_" }
Start-Sleep 3

$args2 = "--site '$Site' --group '$Group'"
if ($Label)  { $args2 += " --label '$Label'" }
$args2 += " --server '$Server'"
Say "provisioning (host='$Server' site=$Site group=$Group) and testing WiFi"
Remote "$py ~/ionity-flash/provision.py $Port $args2 --test 2>&1 | grep -E '^(==|!!|>>)'" | ForEach-Object { Say "  $_" }
Say "done"
