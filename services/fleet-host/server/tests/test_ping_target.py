"""
AEDI - IONITY GLOBAL | Fleet host: ping_target action / ping_from_node MCP tool (EWP ping_host 0x47)
The node side is answered with the Pi agent's own codec, as in test_netobserve_host.   Policy 986 AED
"""
import asyncio
import socket

import pytest

from app.ewp import ewp_protocol as P
from app.fleet.actions import ActionError, node_action, ping_args

from .test_netobserve_host import HW, cb, hello, host  # noqa: F401  (fixture)


def answer(host, result):
    """Reply to the next REQ the hub publishes with `result` (named PingHostResult fields)."""
    async def node():
        while not host.sent:
            await asyncio.sleep(0.01)
        hdr, body = cb.decode_frame(host.sent[-1][1])
        host.seen = (hdr, cb.named("PingHostArgs", body))
        host.hub.handle(f"ionity/ewp1/lab/{HW}/rs", cb.encode_frame(
            P.MSG_RES, cb.keyed("ResultBody", {"result": cb.keyed("PingHostResult", result)}),
            rid=hdr["rid"], tool=hdr["tool"]))
    return asyncio.create_task(node())


def test_ping_args_bounds_and_defaults():
    assert ping_args({"target": " 192.168.0.1 "}) == {"target": "192.168.0.1", "count": 4, "timeoutMs": 1000,
                                                     "intervalMs": 1000}
    for bad in ({}, {"target": "bad host"}, {"target": "x;reboot"}, {"target": "a" * 254},
                {"target": "ok.lan", "count": 0}, {"target": "ok.lan", "count": 11}, {"target": "ok.lan", "count": True},
                {"target": "ok.lan", "timeout_ms": 99}, {"target": "ok.lan", "interval_ms": 5001}):
        with pytest.raises(ActionError):
            ping_args(bad)


async def test_ping_target_round_trip(host):
    host.hub.handle(f"ionity/ewp1/lab/{HW}/hello", hello(tools=(P.TOOL_PING_HOST,)))
    host.sent.clear()
    t = answer(host, {"error": P.PING_NONE, "resolvedIp": socket.inet_aton("192.168.0.1"), "sent": 4,
                      "received": 3, "rttMinMs": 2, "rttAvgMs": 4, "rttMaxMs": 9})
    r = await node_action(HW, "ping_target", {"target": "192.168.0.1", "count": 4, "timeout_ms": 200,
                                              "interval_ms": 200}, registry=host.reg, hub=host.hub)
    await t
    hdr, args = host.seen
    assert hdr["tool"] == P.TOOL_PING_HOST
    assert args == {"target": "192.168.0.1", "count": 4, "timeoutMs": 200, "intervalMs": 200}
    assert hdr["deadline_ms"] >= 3 * 200 + 200                      # the node gets time to finish
    assert r["ok"] and r["reachable"] is True and r["loss_pct"] == 25.0
    assert r["result"]["error"] == "none" and r["result"]["resolvedIp"] == "192.168.0.1"


async def test_ping_target_unreachable_and_errors(host):
    host.hub.handle(f"ionity/ewp1/lab/{HW}/hello", hello(tools=(P.TOOL_PING_HOST,)))
    host.sent.clear()
    t = answer(host, {"error": P.PING_RESOLVE_FAILED, "sent": 0, "received": 0})
    r = await node_action(HW, "ping_target", {"target": "nowhere.example", "count": 1, "timeout_ms": 100},
                          registry=host.reg, hub=host.hub)
    await t
    assert r["reachable"] is False and r["result"]["error"] == "resolve_failed" and r["loss_pct"] is None
    with pytest.raises(ActionError):                                # nothing is sent for bad arguments
        await node_action(HW, "ping_target", {"target": "x;reboot"}, registry=host.reg, hub=host.hub)


async def test_ping_target_needs_a_node_that_offers_it(host):
    host.hub.handle(f"ionity/ewp1/lab/{HW}/hello", hello(tools=(P.TOOL_READ_TELEMETRY,)))
    r = await node_action(HW, "ping_target", {"target": "192.168.0.1"}, registry=host.reg, hub=host.hub)
    assert r["ok"] is False and "does not offer 'ping_host'" in r["error"]
