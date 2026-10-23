"""
AEDI - IONITY GLOBAL | Request authentication + rate limiting
Doc ID: DOC-2026-10-ESP32MCP-SEC | Policy 986 AED | License AED 900
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd

Three credentials, three jobs:
  IONITY_FLEET_TOKEN  nodes -> host ingest (X-Fleet-Token or "Authorization: Bearer");
                      enforced when IONITY_REQUIRE_TOKEN=true. Optional HMAC form:
                      X-Fleet-Signature: hex(HMAC-SHA256(token, "<ts>.<body>")) + X-Fleet-Ts
                      (+-300 s) so the token itself never crosses the network.
  IONITY_ADMIN_TOKEN  operators / MCP clients that CHANGE things (commands, watchlist, labels)
  IONITY_MCPO_API_KEY the OpenAPI gateway (tools/scripts/run_mcpo.py)

Rate limiting is a per-client-IP token bucket: cheap, in memory, and enough to
keep one misbehaving board or script from starving the event loop.
"""
from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import HTTPException, Request

from app.config import settings


class TokenBucket:
    def __init__(self, rate_per_s: float, burst: int, max_keys: int = 10_000):
        self.rate = rate_per_s
        self.burst = float(burst)
        self.max_keys = max_keys
        self._b: dict[str, tuple[float, float]] = {}

    def allow(self, key: str, cost: float = 1.0) -> bool:
        if self.rate <= 0:
            return True
        now = time.monotonic()
        tokens, last = self._b.get(key, (self.burst, now))
        tokens = min(self.burst, tokens + (now - last) * self.rate)
        if tokens < cost:
            self._b[key] = (tokens, now)
            return False
        self._b[key] = (tokens - cost, now)
        if len(self._b) > self.max_keys:                 # forget the idlest clients
            for k in sorted(self._b, key=lambda k: self._b[k][1])[: self.max_keys // 10]:
                self._b.pop(k, None)
        return True


ingest_limiter = TokenBucket(settings.ingest_rate_per_s, settings.ingest_burst)
mcp_limiter = TokenBucket(settings.mcp_rate_per_s, settings.mcp_burst)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def rate_limit(request: Request, bucket: TokenBucket) -> None:
    if not bucket.allow(client_ip(request)):
        raise HTTPException(status_code=429, detail="too many requests - slow down",
                            headers={"Retry-After": "1"})


def _bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    return auth[7:].strip() if auth.lower().startswith("bearer ") else ""


def is_admin(request: Request) -> bool:
    want = settings.admin_token
    if not want:
        return True
    got = _bearer(request) or request.headers.get("x-ionity-token", "")
    return hmac.compare_digest(got or "", want)


def require_admin(request: Request) -> None:
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="admin token required (Authorization: Bearer ...)")


def check_fleet_token(request: Request, body: bytes = b"") -> None:
    """Device ingest auth. Accepts the token itself or an HMAC of the body."""
    if not settings.require_token:
        return
    token = settings.fleet_token
    sig = request.headers.get("x-fleet-signature")
    if sig:
        ts = request.headers.get("x-fleet-ts", "")
        try:
            skew = abs(time.time() - float(ts))
        except ValueError:
            skew = 1e9
        want = hmac.new(token.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
        if skew <= 300 and hmac.compare_digest(sig.lower(), want):
            return
        raise HTTPException(status_code=401, detail="bad or stale X-Fleet-Signature")
    got = request.headers.get("x-fleet-token") or _bearer(request)
    if not hmac.compare_digest(got or "", token):
        raise HTTPException(status_code=401, detail="invalid or missing X-Fleet-Token")
