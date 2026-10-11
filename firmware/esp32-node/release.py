#!/usr/bin/env python3
# AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | release.py
# Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
# (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
"""Build the release variants and write dist/manifest.json for the Ionity portal.

    . $IDF_PATH/export.sh            # ESP-IDF 5.5 environment (idf.py, esptool on PATH)
    python release.py                # all four variants
    python release.py esp32s3_usb    # only some

Output (dist/):
    <variant>.bin        merged image, flash at 0x0 (bootloader + partition table + otadata + app)
    manifest.json        what apps/portal reads (product, version, builds[], provisioning)
    elf/<variant>-<sha8>.elf   symbols for crash backtraces (HELLO.fw.elfSha selects the file)

Every image is checked before it is listed: second-stage bootloader magic at the
chip's bootloader offset, and the partition table at 0x8000 must be exactly the
fw 2.x "min_spiffs" layout - otherwise a "keep settings" USB update would lose
the board's Wi-Fi. Copy dist/* into apps/portal/public/firmware/ to serve them.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
DIST = HERE / "dist"
PRODUCT = "ionity-esp32-mcp-node"
APP_PARTITION_BYTES = 0x1E0000

VARIANTS = {
    "esp32s3_uart": dict(target="esp32s3", console="uart", chip_family="ESP32-S3", native_usb=False,
                         name="ESP32-S3 (CH340 / CP210x USB-UART port)"),
    "esp32s3_usb": dict(target="esp32s3", console="usb", chip_family="ESP32-S3", native_usb=True,
                        name="ESP32-S3 (native USB port)"),
    "esp32c6_uart": dict(target="esp32c6", console="uart", chip_family="ESP32-C6", native_usb=False,
                         name="ESP32-C6 (USB-UART port)"),
    "esp32c6_usb": dict(target="esp32c6", console="usb", chip_family="ESP32-C6", native_usb=True,
                        name="ESP32-C6 (native USB port)"),
}

# fw 2.1.1 (Arduino min_spiffs), read back from its images at 0x8000.
EXPECTED_PARTITIONS = [
    ("nvs", 1, 0x02, 0x9000, 0x5000),
    ("otadata", 1, 0x00, 0xE000, 0x2000),
    ("app0", 0, 0x10, 0x10000, 0x1E0000),
    ("app1", 0, 0x11, 0x1F0000, 0x1E0000),
    ("spiffs", 1, 0x82, 0x3D0000, 0x20000),
    ("coredump", 1, 0x03, 0x3F0000, 0x10000),
]

BOOTLOADER_OFFSET = {"esp32s3": 0x0, "esp32c6": 0x0}


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=HERE, check=True)


def partitions(image: bytes) -> list[tuple[str, int, int, int, int]]:
    out = []
    for i in range(0x8000, 0x8000 + 0xC00, 32):
        e = image[i:i + 32]
        if e[:2] != b"\xaa\x50":
            break
        typ, sub, off, size = e[2], e[3], *struct.unpack("<II", e[4:12])
        label = e[12:28].split(b"\x00")[0].split(b"\xff")[0].decode()
        out.append((label, typ, sub, off, size))
    return out


def build(variant: str) -> dict:
    v = VARIANTS[variant]
    bdir = BUILD / variant
    sdkconfig = BUILD / f"sdkconfig.{variant}"
    defaults = f"sdkconfig.defaults;sdkconfig.{v['console']}"
    BUILD.mkdir(exist_ok=True)
    if bdir.exists():
        shutil.rmtree(bdir)
    if sdkconfig.exists():
        sdkconfig.unlink()  # always start from the committed defaults
    common = ["idf.py", "-B", str(bdir), "-D", f"SDKCONFIG={sdkconfig}", "-D", f"SDKCONFIG_DEFAULTS={defaults}"]
    run(common + ["set-target", v["target"]])
    run(common + ["build"])

    merged = DIST / f"{variant}.bin"  # absolute: idf.py resolves relative names against the build dir
    run(common + ["merge-bin", "-o", str(merged)])
    if not merged.exists():
        raise SystemExit(f"{variant}: merged image missing")

    image = merged.read_bytes()
    boot = BOOTLOADER_OFFSET[v["target"]]
    if image[boot] != 0xE9:
        raise SystemExit(f"{variant}: no bootloader magic 0xE9 at {boot:#x}")
    table = partitions(image)
    if table != EXPECTED_PARTITIONS:
        raise SystemExit(f"{variant}: partition table differs from fw 2.x min_spiffs:\n{table}")

    desc = json.loads((bdir / "project_description.json").read_text())
    app_bytes = (bdir / desc["app_bin"]).stat().st_size
    elf = bdir / desc["app_elf"]
    elf_sha = hashlib.sha256(elf.read_bytes()).hexdigest()[:16]
    (DIST / "elf").mkdir(exist_ok=True)
    shutil.copy2(elf, DIST / "elf" / f"{variant}-{elf_sha}.elf")

    return {
        "variant": variant,
        "name": v["name"],
        "chip_family": v["chip_family"],
        "native_usb": v["native_usb"],
        "target": v["target"],
        "file": merged.name,
        "offset": 0,
        "size": len(image),
        "sha256": hashlib.sha256(image).hexdigest(),
        "app_bytes": app_bytes,
        "app_pct": round(100 * app_bytes / APP_PARTITION_BYTES),
        "idf": desc.get("git_revision", ""),
    }


def main(argv: list[str]) -> int:
    if "IDF_PATH" not in os.environ:
        print("Run inside an ESP-IDF 5.5 environment (. $IDF_PATH/export.sh).", file=sys.stderr)
        return 2
    wanted = argv or list(VARIANTS)
    unknown = [w for w in wanted if w not in VARIANTS]
    if unknown:
        print(f"unknown variant(s): {unknown}; choose from {list(VARIANTS)}", file=sys.stderr)
        return 2
    DIST.mkdir(exist_ok=True)
    builds = [build(v) for v in wanted]
    manifest = {
        "product": PRODUCT,
        "version": (HERE / "version.txt").read_text().strip(),
        "built_at": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat(),
        "policy": "Policy 986 AED | (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd",
        "provisioning": {"protocol": "ionity-prov/2", "baud": 115200, "prefix": "IONITY-PROV "},
        "builds": builds,
    }
    (DIST / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for b in builds:
        print(f"{b['variant']:14s} {b['size']:>8} B  app {b['app_pct']:>3}%  {b['sha256'][:16]}")
    print(f"wrote {DIST / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
