# ===========================================================================
# AEDI - IONITY GLOBAL | Add a device to the fleet
# Doc ID: DOC-2026-09-ESP32MCP-ADD | Policy 986 AED
# (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd
# ---------------------------------------------------------------------------
# Plug a board in, run this, wait. It finds the port, identifies the chip,
# picks the matching board target, flashes, and then watches the fleet server
# until the new device registers itself.
#
#   .\scripts\add_device.ps1                       # auto-detect everything
#   .\scripts\add_device.ps1 -Port COM12
#   .\scripts\add_device.ps1 -Site kelvin-drive -Group power -Label "GF riser"
#
# The same firmware goes on every unit -- identity comes from the eFuse MAC.
# -Site/-Group/-Label are applied afterwards over the API, not compiled in.
# ===========================================================================
param(
  [string]$Port  = "",
  [string]$Site  = "",
  [string]$Group = "",
  [string]$Label = "",
  [string]$Server = "http://127.0.0.1:8099"
)

$ErrorActionPreference = "Stop"
$acli = 'E:\Program Files (x86)\ArduinoIDE\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe'
$sk   = 'E:\.ESP32-MCP\firmware-arduino\Esp32_MCP_Node'
$root = 'E:\.ESP32-MCP\firmware-arduino'

function Say($m) { Write-Host "[add-device] $m" }

# --- 1. find the board ------------------------------------------------------
if (-not $Port) {
  $cands = Get-CimInstance Win32_PnPEntity |
    Where-Object { $_.Name -match '\(COM\d+\)' -and $_.DeviceID -match '^USB\\VID' }
  if (-not $cands) {
    Say "No USB serial device found. Only Bluetooth COM ports are present."
    Say "Check: data-capable cable, the board's other USB socket, and power."
    exit 1
  }
  if ($cands.Count -gt 1) {
    Say "More than one USB serial device present - pass -Port explicitly:"
    $cands | ForEach-Object { Say ("   " + $_.Name) }
    exit 1
  }
  $Port = ([regex]::Match($cands.Name, '\((COM\d+)\)')).Groups[1].Value
  Say "found $($cands.Name)"
}
Say "using port $Port"

# --- 2. identify the chip ---------------------------------------------------
$esptool = Get-ChildItem "$env:LOCALAPPDATA\Arduino15\packages\esp32\tools\esptool_py" `
             -Recurse -Filter 'esptool.exe' -ErrorAction SilentlyContinue |
           Select-Object -First 1 -ExpandProperty FullName
$chipOut = & $esptool --port $Port --baud 115200 flash_id 2>&1 | Out-String
$chip = [regex]::Match($chipOut, 'Chip is (ESP32[^\s(]*)').Groups[1].Value
$mac  = [regex]::Match($chipOut, 'MAC:\s+([0-9a-f:]{17})').Groups[1].Value
if (-not $chip) { Say "could not identify the chip on $Port"; Write-Output $chipOut; exit 1 }

# Flash size wording varies between esptool builds ("Detected flash size: 16MB"
# vs "Flash size: 16MB"). If we cannot read it, OMIT the FlashSize option
# entirely rather than emitting FlashSize=M, which is an invalid FQBN.
$fsz = ''
foreach ($rx in 'Detected flash size:\s*(\d+)\s*MB', 'Flash size:\s*(\d+)\s*MB', '(\d+)MB') {
  $m = [regex]::Match($chipOut, $rx)
  if ($m.Success) { $fsz = $m.Groups[1].Value; break }
}
if ($fsz) { Say "chip=$chip flash=${fsz}MB mac=$mac" }
else      { Say "chip=$chip flash=unknown (using board default) mac=$mac" }

switch -Regex ($chip) {
  'ESP32-S3' {
    $opts = @('PartitionScheme=min_spiffs')
    if ($fsz) { $opts = @("FlashSize=${fsz}M") + $opts }
    $fqbn = "esp32:esp32:esp32s3:" + ($opts -join ',')
  }
  'ESP32-C3' { $fqbn = "esp32:esp32:esp32c3" }
  'ESP32-S2' { $fqbn = "esp32:esp32:esp32s2" }
  default    { $fqbn = "esp32:esp32:esp32" }
}
Say "target $fqbn"

# Expected device id, so we know what to watch for.
$expected = "esp32-" + ($mac -replace ':', '')
Say "this board will register itself as $expected"

# --- 3. build + flash -------------------------------------------------------
$bp = Join-Path $root (".build-" + ($chip -replace '[^A-Za-z0-9]', '') + "-$fsz")
Say "compiling (cached after the first board of each type)..."
& $acli compile --fqbn $fqbn --build-path $bp --log-level warn $sk 2>&1 |
  Select-String 'Sketch uses|Global variables|error:'
if ($LASTEXITCODE -ne 0) { Say "COMPILE FAILED"; exit 1 }

Say "uploading..."
& $acli upload --fqbn $fqbn --port $Port --build-path $bp $sk 2>&1 |
  Select-String 'Connected to|Wrote|Hash of data|Hard resetting|error|failed'
$uploaded = ($LASTEXITCODE -eq 0)

if (-not $uploaded) {
  # arduino-cli's upload recipe resets the chip with DTR/RTS (default-reset).
  # That is right for CH340/CP210x bridge boards and WRONG for the ESP32-S3/C3
  # built-in USB-Serial-JTAG (VID 303A): the device drops off USB mid-connect
  # and pySerial reports PermissionError 13 / Windows error 31. esptool's
  # usb-reset sequence is what that peripheral expects. Found on the lab's
  # second S3: arduino-cli failed every time, this succeeded first try.
  Say "arduino-cli upload failed - retrying with esptool usb-reset (native USB boards)"
  $e5 = Get-ChildItem "$env:LOCALAPPDATA\Arduino15\packages\esp32\tools\esptool_py" -Recurse -Filter 'esptool.exe' |
        Where-Object { $_.FullName -match '\\5\.' } | Select-Object -First 1 -ExpandProperty FullName
  $b0 = Get-ChildItem "$env:LOCALAPPDATA\Arduino15\packages\esp32\hardware\esp32" -Recurse -Filter 'boot_app0.bin' |
        Select-Object -First 1 -ExpandProperty FullName
  $chipArg = ($chip.ToLower() -replace '[^a-z0-9]', '')
  for ($t = 1; $t -le 3 -and -not $uploaded; $t++) {
    $o = & $e5 --chip $chipArg --port $Port --baud 115200 --before usb-reset --after hard-reset `
           write-flash -z --flash-mode keep --flash-freq keep --flash-size keep `
           0x0     (Join-Path $bp 'Esp32_MCP_Node.ino.bootloader.bin') `
           0x8000  (Join-Path $bp 'Esp32_MCP_Node.ino.partitions.bin') `
           0xe000  $b0 `
           0x10000 (Join-Path $bp 'Esp32_MCP_Node.ino.bin') 2>&1 | Out-String
    $uploaded = ($LASTEXITCODE -eq 0 -and $o -match 'Hash of data verified')
    if (-not $uploaded) { Say "  attempt $t failed"; Start-Sleep 4 }
  }
}
if (-not $uploaded) {
  Say "UPLOAD FAILED. Put the board in download mode by hand:"
  Say "  hold BOOT, tap RESET, release BOOT - then run this again."
  exit 1
}
Say "flashed."

# --- 4. wait for it to register itself --------------------------------------
Say "waiting for $expected to report in (boot + wifi + first telemetry)..."
# A device row may already exist from a previous flash. Existence proves
# nothing -- require a reading that arrived AFTER we reset the board, or we
# report success while the board is actually dead.
$flashedAt = Get-Date
$found = $null
for ($i = 0; $i -lt 25; $i++) {
  Start-Sleep 3
  try {
    $r = Invoke-RestMethod "$Server/api/v1/devices?search=$($mac -replace ':','')&limit=2" -TimeoutSec 6
    if ($r.count -gt 0) {
      $d = $r.devices[0]
      $freshEnough = $d.last_seen_age_s -ne $null -and
                     $d.last_seen_age_s -lt ((Get-Date) - $flashedAt).TotalSeconds
      if ($freshEnough -and $d.health -ne 'offline') { $found = $d; break }
    }
  } catch {}
  Write-Host -NoNewline "."
}
Write-Host ""

if (-not $found) {
  Say "NOT REGISTERED YET."
  Say "Open the Arduino IDE Serial Monitor on $Port at 115200 to see why."
  Say "Most common: wrong WiFi password in secrets.h, or the board is on a"
  Say "different SSID to the fleet server."
  exit 1
}

Say "*** REGISTERED ***"
Say ("  {0}  health={1}  ip={2}  via={3}  fw={4}" -f `
     $found.device_id, $found.health, $found.ip, $found.transport, $found.fw)
Say ("  metrics: " + (($found.metrics.PSObject.Properties |
     ForEach-Object { "$($_.Name)=$($_.Value)" }) -join "  "))

# --- 5. optional tagging ----------------------------------------------------
if ($Site -or $Group -or $Label) {
  $body = @{ action = "set_meta" }
  if ($Site)  { $body.site  = $Site }
  if ($Group) { $body.group = $Group }
  if ($Label) { $body.label = $Label }
  try {
    $res = Invoke-RestMethod "$Server/api/v1/devices/$($found.device_id)/cmd" `
             -Method Post -Body ($body | ConvertTo-Json) -ContentType 'application/json' -TimeoutSec 10
    if ($res.ok) { Say "tagged: site=$Site group=$Group label=$Label (device reboots to re-topic)" }
    else { Say "tagging needs MQTT: $($res.error)"; Say "start the broker with: docker compose up -d" }
  } catch { Say "tagging failed: $_" }
}

Say "done. Dashboard: $Server/"
