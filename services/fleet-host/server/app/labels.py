"""
AEDI - IONITY GLOBAL | Friendly names for LAN clients and fleet nodes
Doc ID: DOC-2026-10-ESP32MCP-LABELS | Policy 986 AED | License AED 900
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd

config/device_labels.json is the one hand-edited source of names. Keys:

    "aa-bb-cc-dd-ee-ff"   a MAC (any of aa:bb.. / AA-BB.. spellings) - preferred, follows DHCP changes
    "192.168.0.42"        an IPv4 address - for devices whose MAC is unknown / randomised
    "esp32-98a316e5d18c"  a fleet node id ("site"/"group" there seed serial-bridge boards)

    { "<key>": { "label": "Kitchen TV", "site": "lab", "group": "bench" } }

The file is re-read whenever its modification time changes (checked at most
every 2 s), so an edit - by hand, from the portal or through MCP
manage_device_labels - applies to the next DNS query without a restart.

resolve() merges, in order of trust: the file (by MAC, then IP), then names a
node discovered itself (DHCP option 12 / mDNS host name), then nothing.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("ionity.labels")

_MAC_RE = re.compile(r"^[0-9a-f]{2}([-:]?)[0-9a-f]{2}(\1[0-9a-f]{2}){4}$")
_NODE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,40}$")
MAX_LABEL = 64


def norm_mac(mac: str | None) -> str | None:
    if not mac:
        return None
    m = mac.strip().lower()
    if not _MAC_RE.match(m):
        return None
    h = re.sub(r"[-:]", "", m)
    return "-".join(h[i:i + 2] for i in range(0, 12, 2))


def key_kind(key: str) -> str:
    if norm_mac(key):
        return "mac"
    try:
        ipaddress.IPv4Address(key)
        return "ip"
    except ValueError:
        pass
    if _NODE_RE.match(key):
        return "node"
    raise ValueError(f"'{key}' is not a MAC, an IPv4 address or a node id")


class LabelBook:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._mtime: float | None = None
        self._checked = 0.0
        self._raw: dict[str, dict[str, Any]] = {}
        self.by_mac: dict[str, str] = {}
        self.by_ip: dict[str, str] = {}
        self.by_node: dict[str, dict[str, Any]] = {}
        self.error: str | None = None
        self.reload(force=True)

    # -- file ------------------------------------------------------------------
    def reload(self, force: bool = False) -> bool:
        """Re-read the file if it changed. Cheap enough to call per batch."""
        now = time.monotonic()
        if not force and now - self._checked < 2.0:
            return False
        self._checked = now
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            mtime = None
        if not force and mtime == self._mtime:
            return False
        raw: dict[str, dict[str, Any]] = {}
        if mtime is not None:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8-sig"))
                if not isinstance(data, dict):
                    raise ValueError("top level must be an object")
                raw = {str(k): v for k, v in data.items() if isinstance(v, dict)}
                self.error = None
            except (OSError, ValueError) as e:
                self.error = f"{self.path.name}: {e}"
                log.warning("device labels not reloaded - %s (keeping the previous names)", self.error)
                self._mtime = mtime
                return False
        by_mac, by_ip, by_node = {}, {}, {}
        for k, v in raw.items():
            label = str(v.get("label") or "").strip()[:MAX_LABEL]
            try:
                kind = key_kind(k)
            except ValueError:
                continue
            if kind == "mac" and label:
                by_mac[norm_mac(k)] = label
            elif kind == "ip" and label:
                by_ip[k] = label
            elif kind == "node":
                by_node[k] = dict(v)
        with self._lock:
            self._raw, self.by_mac, self.by_ip, self.by_node = raw, by_mac, by_ip, by_node
            self._mtime = mtime
        log.info("device labels: %d MAC, %d IP, %d node entries", len(by_mac), len(by_ip), len(by_node))
        return True

    def _write(self, raw: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".labels-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(raw, f, indent=2, ensure_ascii=False)
                f.write("\n")
            os.replace(tmp, self.path)              # atomic: a reader never sees half a file
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self.reload(force=True)

    # -- lookups ------------------------------------------------------------------
    def resolve(self, mac: str | None = None, ip: str | None = None,
                hostname: str | None = None) -> tuple[str | None, str]:
        """-> (label, source) with source in file-mac | file-ip | hostname | none."""
        self.reload()
        m = norm_mac(mac)
        with self._lock:
            if m and m in self.by_mac:
                return self.by_mac[m], "file-mac"
            if ip and ip in self.by_ip:
                return self.by_ip[ip], "file-ip"
        if hostname:
            return hostname, "hostname"
        return None, "none"

    def node(self, node_id: str) -> dict[str, Any]:
        self.reload()
        with self._lock:
            return dict(self.by_node.get(node_id, {}))

    def entries(self) -> list[dict[str, Any]]:
        self.reload()
        with self._lock:
            out = []
            for k, v in sorted(self._raw.items()):
                try:
                    kind = key_kind(k)
                except ValueError:
                    kind = "invalid"
                out.append({"key": k, "kind": kind, **v})
            return out

    # -- edits (portal / MCP) ---------------------------------------------------------
    def set(self, key: str, label: str, **extra: Any) -> dict[str, Any]:
        kind = key_kind(key.strip())
        k = norm_mac(key) if kind == "mac" else key.strip()
        label = (label or "").strip()
        if not label or len(label) > MAX_LABEL or any(ord(c) < 32 for c in label):
            raise ValueError(f"label must be 1-{MAX_LABEL} printable characters")
        self.reload(force=True)
        with self._lock:
            raw = {kk: dict(vv) for kk, vv in self._raw.items()}
        # one entry per device: an existing "aa:bb:.." spelling of the same MAC is replaced
        if kind == "mac":
            for old in [x for x in raw if norm_mac(x) == k and x != k]:
                raw.pop(old)
        entry = raw.get(k, {})
        entry["label"] = label
        for f in ("site", "group"):
            if extra.get(f):
                entry[f] = str(extra[f])
        raw[k] = entry
        self._write(raw)
        return {"key": k, "kind": kind, **entry}

    def remove(self, key: str) -> bool:
        self.reload(force=True)
        with self._lock:
            raw = {kk: dict(vv) for kk, vv in self._raw.items()}
        m = norm_mac(key)
        gone = [x for x in raw if x == key.strip() or (m and norm_mac(x) == m)]
        for x in gone:
            raw.pop(x)
        if gone:
            self._write(raw)
        return bool(gone)
