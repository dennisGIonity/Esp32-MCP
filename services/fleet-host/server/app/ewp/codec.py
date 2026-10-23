"""
AEDI - IONITY GLOBAL | EWP/1 codec for the fleet host
Doc ID: DOC-2026-10-ESP32MCP-EWPHOST | Policy 986 AED | License AED 900
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd

16-byte little-endian header + one CBOR body (RFC 8949) with small integer
keys. CBOR is handled by cbor2 - deliberately NOT the Pi agent's stdlib codec,
so the host is an independent decoder of what both node types send.
Numbers and keys come from ewp_protocol.py, generated from packages/protocol.
"""
from __future__ import annotations

import ipaddress
import struct
from typing import Any

import cbor2

from app.ewp import ewp_protocol as P

_HDR = struct.Struct("<BBBBIBBHHH")
assert _HDR.size == P.HEADER_LEN


class FrameError(ValueError):
    """A frame that fails the header checks of packages/protocol frame.ts."""


def encode_frame(msg_type: int, body: Any, *, rid: int = 0, tool: int = P.NO_TOOL,
                 status: int = P.ST_OK, flags: int = 0, deadline_ms: int = 0) -> bytes:
    raw = cbor2.dumps(_strip_none(body if body is not None else {}))
    if len(raw) > 0xFFFF:
        raise FrameError("body too large")
    return _HDR.pack(P.MAGIC, P.VERSION, msg_type, flags, rid & 0xFFFFFFFF, tool, status,
                     deadline_ms, len(raw), 0) + raw


def decode_frame(payload: bytes, max_len: int | None = None) -> tuple[dict, Any]:
    if len(payload) < P.HEADER_LEN:
        raise FrameError("shorter than the 16-byte header")
    magic, ver, typ, flags, rid, tool, status, deadline, body_len, reserved = _HDR.unpack_from(payload)
    if magic != P.MAGIC:
        raise FrameError("bad magic")
    if ver != P.VERSION:
        raise FrameError(f"unsupported EWP version {ver}")
    if flags & ~P.FRAME_FLAGS_KNOWN:
        raise FrameError("unknown flag bits")
    if reserved:
        raise FrameError("reserved bytes must be 0")
    if P.HEADER_LEN + body_len != len(payload):
        raise FrameError("bodyLen does not match the payload length")
    if max_len is not None and len(payload) > max_len:
        raise FrameError("frame larger than the uplink limit")
    try:
        body = cbor2.loads(payload[P.HEADER_LEN:]) if body_len else {}
    except Exception as e:                       # cbor2 raises several types
        raise FrameError(f"CBOR body: {e}") from None
    return ({"type": typ, "flags": flags, "rid": rid, "tool": tool, "status": status,
             "deadline_ms": deadline, "body_len": body_len}, body)


def _strip_none(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _strip_none(x) for k, x in v.items() if x is not None}
    if isinstance(v, (list, tuple)):
        return [_strip_none(x) for x in v]
    return v


def keyed(body: str, fields: dict) -> dict:
    t = P.KEYS[body]
    return {t[k]: v for k, v in fields.items() if v is not None}


def named(body: str, m: Any) -> dict:
    """Integer-keyed map -> field names; unknown keys ignored (forward compatible)."""
    if not isinstance(m, dict):
        return {}
    names = P.NAMES[body]
    return {names[k]: v for k, v in m.items() if k in names}


def ip4(b: Any) -> str | None:
    if isinstance(b, (bytes, bytearray)) and len(b) == 4:
        return str(ipaddress.IPv4Address(bytes(b)))
    if isinstance(b, (bytes, bytearray)) and len(b) == 16:
        return str(ipaddress.IPv6Address(bytes(b)))
    return None


def mac(b: Any) -> str | None:
    """6 raw bytes -> 'aa-bb-cc-dd-ee-ff' (the host's MAC spelling, as in device_labels.json)."""
    if isinstance(b, (bytes, bytearray)) and len(b) == 6 and any(b):
        return "-".join(f"{x:02x}" for x in b)
    return None


def num(v: Any) -> float | None:
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    return None
