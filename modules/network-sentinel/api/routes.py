import hmac
import os
from typing import Any, Dict

from fastapi import APIRouter, Body, Header, HTTPException, Request

router = APIRouter(prefix="/api")

# Same convention as the fleet host: empty token = open (lab), otherwise
# "Authorization: Bearer <t>" / "X-Ionity-Token: <t>" for operator actions and
# "X-Fleet-Token: <t>" for the ESP32 sentinel's hardware feed.
ADMIN_TOKEN = os.getenv("SENTINEL_ADMIN_TOKEN", "")
FEED_TOKEN = os.getenv("SENTINEL_FEED_TOKEN", "")


def _require(token_wanted: str, authorization: str | None, x_token: str | None) -> None:
    if not token_wanted:
        return
    auth = authorization or ""
    got = auth[7:].strip() if auth.lower().startswith("bearer ") else (x_token or "")
    if not hmac.compare_digest(got, token_wanted):
        raise HTTPException(status_code=401, detail="token required")

@router.get("/telemetry/live")
async def get_live_telemetry(request: Request):
    analyzer = request.app.state.traffic_analyzer
    return await analyzer.get_live_telemetry()

@router.get("/telemetry/stability")
async def get_stability_metrics(request: Request):
    analyzer = request.app.state.traffic_analyzer
    telemetry = await analyzer.get_live_telemetry()
    return {
        "stability": telemetry["stability"],
        "hardware_sentinel": telemetry["hardware_sentinel"],
        "probe_targets": telemetry["probe_targets"]
    }

@router.get("/telemetry/traffic")
async def get_traffic_feed(request: Request):
    analyzer = request.app.state.traffic_analyzer
    telemetry = await analyzer.get_live_telemetry()
    return {
        "total_rx_mbps": telemetry["total_rx_mbps"],
        "total_tx_mbps": telemetry["total_tx_mbps"],
        "wan_interfaces": telemetry["wan_interfaces"],
        "floors": telemetry["floors"]
    }

@router.get("/telemetry/speedtest")
async def get_speedtest_metrics(request: Request):
    speedtest = request.app.state.speedtest_engine
    return speedtest.last_result

@router.post("/telemetry/speedtest/run")
async def trigger_active_speedtest(request: Request,
                                   authorization: str | None = Header(default=None),
                                   x_ionity_token: str | None = Header(default=None)):
    _require(ADMIN_TOKEN, authorization, x_ionity_token)
    return await request.app.state.speedtest_engine.run_active_speedtest()

@router.get("/telemetry/loadshedding")
async def get_loadshedding(request: Request):
    ls_engine = request.app.state.loadshedding_engine
    analyzer = request.app.state.traffic_analyzer
    status = await ls_engine.get_loadshedding_status()
    correlation = ls_engine.correlate_link_issue(
        wan_status="ONLINE",
        packet_loss_pct=0.0,
        mains_power_ok=analyzer.hardware_mains_ok
    )
    return {
        "status": status,
        "correlation": correlation
    }

@router.get("/telemetry/security")
async def get_security_threats(request: Request):
    sec = request.app.state.security_analyzer
    return {
        "recent_alerts": sec.get_recent_alerts(limit=20),
        "flagged_ips": sec.get_flagged_ips()
    }

@router.post("/telemetry/hardware-feed")
async def receive_esp32_hardware_feed(request: Request, payload: Dict[str, Any] = Body(...),
                                      x_fleet_token: str | None = Header(default=None)):
    """Ingests telemetry from physical ESP32-S3 sentinels over HTTP."""
    _require(FEED_TOKEN, None, x_fleet_token)
    mains_ok = bool(payload.get("mains_power_ok", True))
    request.app.state.traffic_analyzer.update_hardware_sentinel_state(mains_ok=mains_ok)
    return {"status": "ACK", "received_score": payload.get("stability_score")}

@router.post("/mcp/rpc")
async def mcp_json_rpc_endpoint(request: Request, body: Dict[str, Any] = Body(...)):
    """
    HTTP JSON-RPC 2.0 endpoint for MCP requests.
    """
    if not isinstance(body.get("params", {}), dict):
        return {"jsonrpc": "2.0", "id": body.get("id"),
                "error": {"code": -32600, "message": "params must be an object"}}
    return await request.app.state.mcp_server.handle_request(body)
