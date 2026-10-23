"""
AEDI - IONITY GLOBAL | EWP/1 wire codec for the Raspberry Pi agent
Doc ID: DOC-2026-10-ESP32MCP-PIAGENT2 | Policy 986 AED | License AED 900
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd

The Pi speaks EWP/1 exactly like the ESP-IDF firmware: a 16-byte little-endian
header followed by one CBOR body with small integer keys (packages/protocol).
This module is the standard-library-only codec for that: a Pi Zero needs no
pip install to join the fleet.

CBOR subset (RFC 8949), the same one the firmware's CborReader accepts:
  unsigned/negative integers, byte strings, UTF-8 text, arrays, maps,
  false/true/null, float16/32/64. Definite lengths only; depth <= 16.
Encoder rule (gen-c-header.ts / scalars.ts): floats are encoded as binary32
when that is exact, so a 0.5 duty cycle costs 5 bytes, not 9.
"""
from __future__ import annotations

import math
import struct
from typing import Any

import ewp_protocol as P

MAX_DEPTH = 16


class CborError(ValueError):
    """Malformed or unsupported CBOR."""


class FrameError(ValueError):
    """Malformed EWP frame (header checks of packages/protocol frame.ts)."""


# ---------------------------------------------------------------------------
# CBOR
# ---------------------------------------------------------------------------
def _head(major: int, n: int) -> bytes:
    if n < 24:
        return bytes([(major << 5) | n])
    if n < 0x100:
        return bytes([(major << 5) | 24, n])
    if n < 0x10000:
        return bytes([(major << 5) | 25]) + struct.pack(">H", n)
    if n < 0x1_0000_0000:
        return bytes([(major << 5) | 26]) + struct.pack(">I", n)
    if n < 0x1_0000_0000_0000_0000:
        return bytes([(major << 5) | 27]) + struct.pack(">Q", n)
    raise CborError("integer does not fit 64 bits")


def _enc(v: Any, out: bytearray, depth: int) -> None:
    if depth > MAX_DEPTH:
        raise CborError("nesting too deep")
    if v is None:
        out.append(0xF6)
    elif v is True:
        out.append(0xF5)
    elif v is False:
        out.append(0xF4)
    elif isinstance(v, int):
        out += _head(0, v) if v >= 0 else _head(1, -1 - v)
    elif isinstance(v, float):
        if not math.isfinite(v):
            raise CborError("non-finite floats are not allowed on the wire")
        f32 = struct.unpack(">f", struct.pack(">f", v))[0]
        if f32 == v:
            out += b"\xFA" + struct.pack(">f", v)
        else:
            out += b"\xFB" + struct.pack(">d", v)
    elif isinstance(v, (bytes, bytearray, memoryview)):
        b = bytes(v)
        out += _head(2, len(b)) + b
    elif isinstance(v, str):
        if "\x00" in v:
            raise CborError("embedded NUL is not allowed on the wire")
        b = v.encode("utf-8")
        out += _head(3, len(b)) + b
    elif isinstance(v, (list, tuple)):
        out += _head(4, len(v))
        for x in v:
            _enc(x, out, depth + 1)
    elif isinstance(v, dict):
        items = [(k, x) for k, x in v.items() if x is not None]   # None = absent optional field
        out += _head(5, len(items))
        for k, x in items:
            _enc(k, out, depth + 1)
            _enc(x, out, depth + 1)
    else:
        raise CborError(f"cannot encode {type(v).__name__}")


def dumps(v: Any) -> bytes:
    out = bytearray()
    _enc(v, out, 0)
    return bytes(out)


class _Reader:
    def __init__(self, b: bytes):
        self.b = memoryview(b)
        self.p = 0

    def take(self, n: int) -> bytes:
        if n < 0 or self.p + n > len(self.b):
            raise CborError("truncated")
        s = bytes(self.b[self.p:self.p + n])
        self.p += n
        return s

    def arg(self, info: int) -> int:
        if info < 24:
            return info
        if info == 24:
            return self.take(1)[0]
        if info == 25:
            return struct.unpack(">H", self.take(2))[0]
        if info == 26:
            return struct.unpack(">I", self.take(4))[0]
        if info == 27:
            return struct.unpack(">Q", self.take(8))[0]
        raise CborError("indefinite lengths are not supported")

    def item(self, depth: int) -> Any:
        if depth > MAX_DEPTH:
            raise CborError("nesting too deep")
        ib = self.take(1)[0]
        major, info = ib >> 5, ib & 0x1F
        if major == 0:
            return self.arg(info)
        if major == 1:
            return -1 - self.arg(info)
        if major == 2:
            return self.take(self.arg(info))
        if major == 3:
            try:
                return self.take(self.arg(info)).decode("utf-8")
            except UnicodeDecodeError as e:
                raise CborError("invalid UTF-8") from e
        if major == 4:
            n = self.arg(info)
            if n > len(self.b):
                raise CborError("array longer than the input")
            return [self.item(depth + 1) for _ in range(n)]
        if major == 5:
            n = self.arg(info)
            if n > len(self.b):
                raise CborError("map longer than the input")
            out: dict = {}
            for _ in range(n):
                k = self.item(depth + 1)
                if isinstance(k, (list, dict, bytes)):
                    raise CborError("unsupported map key")
                if k in out:
                    raise CborError("duplicate map key")
                out[k] = self.item(depth + 1)
            return out
        if major == 7:
            if info == 20:
                return False
            if info == 21:
                return True
            if info in (22, 23):
                return None
            if info == 25:
                return _half(struct.unpack(">H", self.take(2))[0])
            if info == 26:
                return struct.unpack(">f", self.take(4))[0]
            if info == 27:
                return struct.unpack(">d", self.take(8))[0]
        raise CborError(f"unsupported CBOR item 0x{ib:02x}")


def _half(h: int) -> float:
    s, e, f = (h >> 15) & 1, (h >> 10) & 0x1F, h & 0x3FF
    if e == 0:
        v = f * 2.0 ** -24
    elif e == 31:
        v = math.inf if f == 0 else math.nan
    else:
        v = (1 + f / 1024.0) * 2.0 ** (e - 15)
    return -v if s else v


def loads(b: bytes) -> Any:
    r = _Reader(b)
    v = r.item(0)
    if r.p != len(r.b):
        raise CborError("trailing bytes after the CBOR item")
    return v


# ---------------------------------------------------------------------------
# EWP/1 frames
# ---------------------------------------------------------------------------
_HDR = struct.Struct("<BBBBIBBHHH")          # magic ver type flags rid tool status deadline bodyLen reserved
assert _HDR.size == P.HEADER_LEN


def encode_frame(msg_type: int, body: Any, *, rid: int = 0, tool: int = P.NO_TOOL,
                 status: int = P.ST_OK, flags: int = 0, deadline_ms: int = 0) -> bytes:
    raw = dumps(body if body is not None else {})
    if len(raw) > 0xFFFF:
        raise FrameError("body too large")
    return _HDR.pack(P.MAGIC, P.VERSION, msg_type, flags, rid & 0xFFFFFFFF, tool, status,
                     deadline_ms, len(raw), 0) + raw


def decode_frame(payload: bytes, *, max_len: int | None = None) -> tuple[dict, Any]:
    """-> (header dict, decoded body). Validation order of frame.ts."""
    if len(payload) < P.HEADER_LEN:
        raise FrameError("shorter than the 16-byte header")
    magic, ver, typ, flags, rid, tool, status, deadline, body_len, reserved = _HDR.unpack_from(payload)
    if magic != P.MAGIC:
        raise FrameError("bad magic")
    if ver != P.VERSION:
        raise FrameError("unsupported version")
    if flags & ~P.FRAME_FLAGS_KNOWN:
        raise FrameError("unknown flag bits")
    if reserved:
        raise FrameError("reserved must be 0")
    if P.HEADER_LEN + body_len != len(payload):
        raise FrameError("bodyLen does not match the payload")
    if max_len is not None and len(payload) > max_len:
        raise FrameError("frame too large")
    hdr = {"type": typ, "flags": flags, "rid": rid, "tool": tool, "status": status,
           "deadline_ms": deadline, "body_len": body_len}
    try:
        body = loads(payload[P.HEADER_LEN:]) if body_len else {}
    except CborError as e:
        raise FrameError(f"body: {e}") from e
    return hdr, body


def keyed(body: str, fields: dict) -> dict:
    """{"seq": 1, ...} -> {1: 1, ...} with the integer keys of `body` (None = absent)."""
    table = P.KEYS[body]
    out = {}
    for name, value in fields.items():
        if value is None:
            continue
        out[table[name]] = value
    return out


def named(body: str, m: Any) -> dict:
    """{1: 1, ...} -> {"seq": 1, ...}; unknown keys are ignored (forward compatible)."""
    if not isinstance(m, dict):
        return {}
    names = P.NAMES[body]
    return {names[k]: v for k, v in m.items() if k in names}
