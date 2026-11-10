"""
AEDI - IONITY GLOBAL | Watchlist rule engine
Doc ID: DOC-2026-10-ESP32MCP-RULES | Policy 986 AED | License AED 900
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd

Domain rules with categories, matched per DNS label from the right:

    "youtube.com"        youtube.com and every subdomain (www., m., music. ...)
    "*.googlevideo.com"  subdomains only - not googlevideo.com itself

The compiled form is a reversed-label trie (dict of dicts), so a lookup costs
one dict step per label of the QNAME - independent of the number of rules -
and the MOST specific rule wins ("music.youtube.com" can carry its own
category under a general "youtube.com" rule).

The host is authoritative: it matches every query it stores (from every node
and from its own resolver) and pushes the same rules to each node as an EWP/1
watchlist (SET_WATCHLIST, REPLACE + APPEND frames), so nodes can flag and
light their alert LED without a round trip. Built-in rules come from
packages/protocol/src/watchlist.ts (via ewp_protocol.py): they can be
disabled, never deleted.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re

from dataclasses import dataclass, field
from typing import Any
from collections.abc import Awaitable, Callable

from app.ewp import ewp_protocol as P

log = logging.getLogger("ionity.rules")

_LABEL = r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?"
_SUFFIX_RE = re.compile(rf"^{_LABEL}(?:\.{_LABEL})*$")
_CATEGORY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,23}(?::[a-z0-9][a-z0-9_.-]{0,22})?$")
MAX_CATEGORY_ID = P.LIMITS["watchCategories"]


class RuleError(ValueError):
    """A rule or category the engine refuses (reported to API / MCP callers verbatim)."""


def normalize_pattern(pattern: str) -> tuple[str, str, bool]:
    """-> (canonical pattern, suffix, subdomains_only). Raises RuleError."""
    if not isinstance(pattern, str):
        raise RuleError("pattern must be a string")
    p = pattern.strip().lower().rstrip(".")
    if p.startswith("http://") or p.startswith("https://") or "/" in p:
        raise RuleError("give a domain (youtube.com), not a URL")
    sub = p.startswith("*.")
    suffix = p[2:] if sub else p
    if "*" in suffix:
        raise RuleError("only a leading '*.' wildcard is supported")
    if len(suffix.encode()) > P.LIMITS["watchSuffixBytes"] or not _SUFFIX_RE.match(suffix):
        raise RuleError(f"'{pattern}' is not a domain name (letters, digits, '-', '.'; <= 96 bytes)")
    if "." not in suffix:
        raise RuleError("a rule needs at least two labels (e.g. youtube.com) - a bare TLD would flag half the internet")
    return (f"*.{suffix}" if sub else suffix), suffix, sub


@dataclass
class Category:
    id: int
    name: str            # "video_streaming:youtube" | "social_media" | "custom:work"
    label: str
    builtin: bool = False


@dataclass
class Rule:
    id: int
    pattern: str
    suffix: str
    subdomains_only: bool
    category_id: int
    alert: bool = True
    enabled: bool = True
    builtin: bool = False
    created_at: float = 0.0
    hits: int = 0

    def device_flags(self) -> int:
        return (P.RULE_ALERT if self.alert else 0) | (P.RULE_SUBDOMAINS_ONLY if self.subdomains_only else 0)


@dataclass
class Match:
    rule: Rule
    category: Category


@dataclass
class _Node:
    children: dict[str, _Node] = field(default_factory=dict)
    exact: Rule | None = None          # rule that matches this name AND its subdomains
    sub_only: Rule | None = None       # rule that matches subdomains only


class RuleEngine:
    def __init__(self, store):
        self.store = store
        self.categories: dict[int, Category] = {}
        self.rules: dict[int, Rule] = {}
        self._root = _Node()
        self.version = 0
        self._lock = asyncio.Lock()
        self.listeners: list[Callable[[], Awaitable[None] | None]] = []

    # -- load / compile -------------------------------------------------------
    async def load(self) -> None:
        await self.store.seed_watchlist(P.DEFAULT_CATEGORIES, [
            dict(zip(("pattern", "suffix", "sub"), normalize_pattern(r["pattern"]), strict=True),
                 category=r["category"], alert=bool(r["flags"] & P.RULE_ALERT))
            for r in P.DEFAULT_RULES])
        await self._reload()

    async def _reload(self) -> None:
        cats, rules = await self.store.list_watchlist()
        self.categories = {c["id"]: Category(c["id"], c["name"], c["label"] or c["name"], bool(c["builtin"]))
                           for c in cats}
        self.rules = {r["id"]: Rule(r["id"], r["pattern"], r["suffix"], bool(r["subdomains_only"]),
                                    r["category_id"], bool(r["alert"]), bool(r["enabled"]), bool(r["builtin"]),
                                    r["created_at"] or 0.0, r["hits"] or 0) for r in rules}
        self._compile()

    def _compile(self) -> None:
        root = _Node()
        for r in self.rules.values():
            if not r.enabled or r.category_id not in self.categories:
                continue
            n = root
            for label in reversed(r.suffix.split(".")):
                n = n.children.setdefault(label, _Node())
            if r.subdomains_only:
                n.sub_only = r
            else:
                n.exact = r
        self._root = root
        canon = sorted((r.suffix, r.category_id, r.device_flags())
                       for r in self.rules.values() if r.enabled and r.category_id in self.categories)
        canon_c = sorted((c.id, c.name) for c in self.categories.values())
        digest = hashlib.sha256(repr((canon, canon_c)).encode()).digest()
        # u32, never 0 and never the firmware's built-in version 1 (that would hide a needed push)
        v = int.from_bytes(digest[:4], "little") or 2
        self.version = v if v != P.DEFAULT_WATCHLIST_VERSION else 2

    # -- matching ---------------------------------------------------------------
    def match(self, qname: str) -> Match | None:
        labels = (qname or "").lower().rstrip(".").split(".")
        n = self._root
        best: Rule | None = None
        depth = 0
        for label in reversed(labels):
            nxt = n.children.get(label)
            if nxt is None:
                break
            depth += 1
            n = nxt
            remaining = len(labels) - depth
            if n.exact is not None:
                best = n.exact
            if n.sub_only is not None and remaining > 0:
                best = n.sub_only
        if best is None:
            return None
        return Match(best, self.categories[best.category_id])

    # -- device form --------------------------------------------------------------
    def device_rules(self) -> list[list]:
        return [[r.suffix, r.category_id, r.device_flags()] for r in sorted(self.rules.values(), key=lambda x: x.id)
                if r.enabled and r.category_id in self.categories]

    def device_frames(self, max_body: int = P.LIMITS["downlinkFrame"] - P.HEADER_LEN - 32) -> list[dict]:
        """SET_WATCHLIST argument bodies (integer keys): REPLACE then APPENDs, each
        fitting one downlink frame. Rules beyond the node limit are left out."""
        import cbor2
        K = P.KEYS["SetWatchlistArgs"]
        rules = self.device_rules()[: P.LIMITS["watchRules"]]
        cats = [[c.id, c.name[:48]] for c in sorted(self.categories.values(), key=lambda c: c.id)]
        frames: list[dict] = []
        cur: list = []
        for r in rules:
            trial = {K["op"]: 0, K["version"]: self.version, K["rules"]: cur + [r],
                     K["categories"]: cats if not frames else None}
            if cur and len(cbor2.dumps({k: v for k, v in trial.items() if v is not None})) > max_body:
                frames.append(cur)
                cur = [r]
            else:
                cur.append(r)
        frames.append(cur)
        out = []
        for i, chunk in enumerate(frames):
            body = {K["op"]: P.WATCH_OP_REPLACE if i == 0 else P.WATCH_OP_APPEND,
                    K["version"]: self.version, K["rules"]: chunk}
            if i == 0:
                body[K["categories"]] = cats
            out.append(body)
        return out

    # -- views ------------------------------------------------------------------------
    def category_by_name(self, name: str) -> Category | None:
        n = (name or "").strip().lower()
        for c in self.categories.values():
            if c.name == n or c.name.split(":")[-1] == n or c.label.lower() == n:
                return c
        return None

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "categories": [{"id": c.id, "name": c.name, "label": c.label, "builtin": c.builtin,
                            "rules": sum(1 for r in self.rules.values() if r.category_id == c.id)}
                           for c in sorted(self.categories.values(), key=lambda c: c.id)],
            "rules": [self.rule_view(r) for r in sorted(self.rules.values(), key=lambda r: r.id)],
        }

    def rule_view(self, r: Rule) -> dict[str, Any]:
        c = self.categories.get(r.category_id)
        return {"id": r.id, "pattern": r.pattern, "category": c.name if c else None,
                "category_label": c.label if c else None, "alert": r.alert, "enabled": r.enabled,
                "builtin": r.builtin, "hits": r.hits, "created_at": r.created_at}

    # -- changes ----------------------------------------------------------------------
    async def _changed(self) -> None:
        await self._reload()
        log.info("watchlist now v%08x: %d rules in %d categories", self.version,
                 sum(1 for r in self.rules.values() if r.enabled), len(self.categories))
        for fn in list(self.listeners):
            try:
                res = fn()
                if asyncio.iscoroutine(res):
                    await res
            except Exception:                                  # noqa: BLE001
                log.exception("watchlist listener failed")

    async def add_category(self, name: str, label: str | None = None) -> Category:
        n = (name or "").strip().lower()
        if not _CATEGORY_RE.match(n):
            raise RuleError("category: lowercase 'group' or 'group:service', e.g. social_media or video_streaming:netflix")
        async with self._lock:
            existing = self.category_by_name(n)
            if existing and existing.name == n:
                return existing
            used = set(self.categories)
            free = next((i for i in range(1, MAX_CATEGORY_ID + 1) if i not in used), None)
            if free is None:
                raise RuleError(f"at most {MAX_CATEGORY_ID} categories (the node limit)")
            await self.store.insert_category(free, n, (label or n.split(":")[-1].replace("_", " ").title())[:48])
            await self._changed()
            return self.categories[free]

    async def remove_category(self, name: str) -> dict:
        async with self._lock:
            c = self.category_by_name(name)
            if not c:
                raise RuleError(f"no category '{name}'")
            if c.builtin:
                raise RuleError(f"'{c.name}' is built in: disable its rules instead")
            if any(r.category_id == c.id for r in self.rules.values()):
                raise RuleError(f"'{c.name}' still has rules: remove them first")
            await self.store.delete_category(c.id)
            await self._changed()
            return {"removed": c.name}

    async def add_rule(self, pattern: str, category: str, alert: bool = True) -> Rule:
        canon, suffix, sub = normalize_pattern(pattern)
        cat = self.category_by_name(category) or await self.add_category(category)
        async with self._lock:
            for r in self.rules.values():
                if r.pattern == canon:
                    await self.store.update_rule(r.id, category_id=cat.id, alert=int(alert), enabled=1)
                    await self._changed()
                    return self.rules[r.id]
            if sum(1 for r in self.rules.values() if r.enabled) >= P.LIMITS["watchRules"]:
                raise RuleError(f"at most {P.LIMITS['watchRules']} enabled rules (the node limit)")
            rid = await self.store.insert_rule(canon, suffix, sub, cat.id, alert)
            await self._changed()
            return self.rules[rid]

    def _find(self, rule: int | str) -> Rule:
        if isinstance(rule, int) or (isinstance(rule, str) and rule.isdigit()):
            r = self.rules.get(int(rule))
        else:
            canon = normalize_pattern(str(rule))[0]
            r = next((x for x in self.rules.values() if x.pattern == canon), None)
        if r is None:
            raise RuleError(f"no rule '{rule}'")
        return r

    async def remove_rule(self, rule: int | str) -> dict:
        async with self._lock:
            r = self._find(rule)
            if r.builtin:
                await self.store.update_rule(r.id, enabled=0)
                await self._changed()
                return {"disabled": r.pattern, "note": "built-in rules are disabled, not deleted"}
            await self.store.delete_rule(r.id)
            await self._changed()
            return {"removed": r.pattern}

    async def set_enabled(self, rule: int | str, enabled: bool) -> Rule:
        async with self._lock:
            r = self._find(rule)
            await self.store.update_rule(r.id, enabled=int(enabled))
            await self._changed()
            return self.rules[r.id]

    async def count_hits(self, counts: dict[int, int]) -> None:
        if counts:
            await self.store.add_rule_hits(counts)
            for rid, n in counts.items():
                if rid in self.rules:
                    self.rules[rid].hits += n


