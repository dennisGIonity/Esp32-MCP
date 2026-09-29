#!/usr/bin/env python3
"""
AEDI - IONITY GLOBAL | Build flasher images for the ESP32-MCP node
Doc ID: DOC-2026-09-ESP32MCP-BUILD | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd

Compiles firmware-arduino/Esp32_MCP_Node once per board variant with
arduino-cli and writes, for the Ionity Flasher:

    firmware/dist/<variant>.bin      merged image (bootloader+partitions+app), flash at 0x0
    firmware/dist/manifest.json      version, chip family, USB mode, size, sha256

Images are built WITHOUT secrets.h: WiFi, the MCP host and tokens are written
into NVS by the flasher, so the same image is safe to publish.

    python firmware/build.py --lab esp32s3_uart
        LAB image: includes the git-ignored secrets.h, so a board flashed with a
        full erase seeds its NVS with the lab WiFi on first boot (no password is
        typed anywhere). Written to firmware/dist-lab/ - never publish these.

    python firmware/build.py                 # all variants
    python firmware/build.py esp32s3_uart    # one
    python firmware/build.py --copy-to flasher/public/firmware

arduino-cli is taken from $ARDUINO_CLI, then PATH, then the Arduino IDE 2 bundle.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKETCH = ROOT / "firmware-arduino" / "Esp32_MCP_Node"
DIST = ROOT / "firmware" / "dist"
CACHE = ROOT / "firmware" / ".build"
DIST_LAB = ROOT / "firmware" / "dist-lab"

# variant -> (fqbn, esptool chip family, serial on native USB?, human name)
VARIANTS: dict[str, tuple[str, str, bool, str]] = {
    "esp32s3_uart": ("esp32:esp32:esp32s3:FlashSize=4M,PartitionScheme=min_spiffs,CDCOnBoot=default",
                     "ESP32-S3", False, "ESP32-S3 (CH340 / CP210x USB-UART port)"),
    "esp32s3_usb": ("esp32:esp32:esp32s3:FlashSize=4M,PartitionScheme=min_spiffs,CDCOnBoot=cdc",
                    "ESP32-S3", True, "ESP32-S3 (native USB port)"),
    "esp32_classic": ("esp32:esp32:esp32:FlashSize=4M,PartitionScheme=min_spiffs",
                      "ESP32", False, "ESP32 / WROOM-32"),
    "esp32c3_usb": ("esp32:esp32:esp32c3:FlashSize=4M,PartitionScheme=min_spiffs,CDCOnBoot=cdc",
                    "ESP32-C3", True, "ESP32-C3 (native USB, e.g. Super Mini)"),
}

IDE_BUNDLES = [
    r"E:\Program Files (x86)\ArduinoIDE\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe",
    r"C:\Program Files\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe"),
]


def find_cli() -> str:
    for c in [os.environ.get("ARDUINO_CLI"), shutil.which("arduino-cli"), *IDE_BUNDLES]:
        if c and Path(c).exists():
            return c
    sys.exit("arduino-cli not found - set ARDUINO_CLI")


def fw_version() -> str:
    m = re.search(r'#define\s+FW_VERSION\s+"([^"]+)"', (SKETCH / "config.h").read_text())
    return m.group(1) if m else "0.0.0"


def trim_ff(img: bytes) -> bytes:
    """The merged image is padded with 0xFF to the full flash size (4 MB of
    mostly nothing). Cut the tail back to the last used 4 KB sector: the
    download shrinks ~3x and the flasher erases what it writes anyway."""
    end = len(img.rstrip(b"\xff"))
    end = (end + 0xFFF) & ~0xFFF
    return img[:max(end, 0x10000)]


def build(cli: str, variant: str, work: Path, lab: bool = False) -> dict:
    fqbn, family, usb, name = VARIANTS[variant]
    # Copy the sketch without secrets.h so no credential is baked into a public image.
    src = work / "Esp32_MCP_Node"
    if src.exists():
        shutil.rmtree(src)
    skip = ("*.log", ".build-*", ".theia") if lab else ("secrets.h", "*.log", ".build-*", ".theia")
    shutil.copytree(SKETCH, src, ignore=shutil.ignore_patterns(*skip))
    if lab and not (src / "secrets.h").exists():
        raise SystemExit("--lab needs firmware-arduino/Esp32_MCP_Node/secrets.h (copy secrets.h.example)")
    # Persistent build dir per variant: arduino-cli caches the compiled core
    # there, so a rebuild takes seconds instead of ~10 minutes per variant.
    out = CACHE / variant          # shared with --lab: the compiled core is reused
    print(f"== {variant}: {fqbn}", flush=True)
    r = subprocess.run([cli, "compile", "--fqbn", fqbn, "--build-path", str(out),
                        "--warnings", "default", str(src)], capture_output=True, text=True)
    sys.stdout.write(r.stdout[-1500:])
    if r.returncode != 0:
        sys.stderr.write(r.stderr[-6000:])
        raise SystemExit(f"{variant}: compile failed")
    merged = next(out.glob("*.merged.bin"), None)
    if merged is None:
        raise SystemExit(f"{variant}: no *.merged.bin in {out} (esp32 core >= 3.0 required)")
    dist = DIST_LAB if lab else DIST
    dist.mkdir(parents=True, exist_ok=True)
    dest = dist / f"{variant}.bin"
    data = trim_ff(merged.read_bytes())
    dest.write_bytes(data)
    usage = re.search(r"Sketch uses (\d+) bytes \((\d+)%\)", r.stdout)
    return {
        "variant": variant, "name": name, "chip_family": family, "native_usb": usb,
        "fqbn": fqbn, "file": dest.name, "offset": 0, "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "app_bytes": int(usage.group(1)) if usage else None,
        "app_pct": int(usage.group(2)) if usage else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("variants", nargs="*", default=list(VARIANTS))
    ap.add_argument("--copy-to", help="also copy images + manifest here (e.g. flasher/public/firmware)")
    ap.add_argument("--lab", action="store_true", help="bake in secrets.h -> firmware/dist-lab (private)")
    a = ap.parse_args()
    cli = find_cli()
    if a.lab:
        work = CACHE / "src-lab"
        work.mkdir(parents=True, exist_ok=True)
        for v in (a.variants if a.variants != list(VARIANTS) else ["esp32s3_uart", "esp32s3_usb"]):
            b = build(cli, v, work, lab=True)
            print(f"LAB image {DIST_LAB / b['file']} ({b['size']} bytes) - contains WiFi credentials, do not share")
        return
    version = fw_version()
    manifest_path = DIST / "manifest.json"
    old = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    builds = {b["variant"]: b for b in old.get("builds", [])} if old.get("version") == version else {}
    work = CACHE / "src"                  # fixed path, so the sketch cache stays valid too
    work.mkdir(parents=True, exist_ok=True)
    for v in a.variants:
        if v not in VARIANTS:
            raise SystemExit(f"unknown variant {v}; choose from {', '.join(VARIANTS)}")
        builds[v] = build(cli, v, work)
    manifest = {
        "product": "ionity-esp32-mcp-node",
        "version": version,
        "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "policy": "Policy 986 AED | (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd",
        "provisioning": {"protocol": "ionity-prov/1", "baud": 115200, "prefix": "IONITY-PROV "},
        "builds": [builds[k] for k in VARIANTS if k in builds],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"wrote {manifest_path} ({len(manifest['builds'])} images, fw {version})")
    if a.copy_to:
        dest = (ROOT / a.copy_to) if not Path(a.copy_to).is_absolute() else Path(a.copy_to)
        dest.mkdir(parents=True, exist_ok=True)
        for b in manifest["builds"]:
            shutil.copyfile(DIST / b["file"], dest / b["file"])
        (dest / "manifest.json").write_text(json.dumps(manifest, indent=2))
        print(f"copied to {dest}")


if __name__ == "__main__":
    main()
