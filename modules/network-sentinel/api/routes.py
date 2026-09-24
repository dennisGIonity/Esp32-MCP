from fastapi import APIRouter, HTTPException, Request, Body
from typing import Dict, Any, Optional

router = APIRouter(prefix="/api")

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
async def trigger_active_speedtest(request: Request):
    speedtest = request.app.state.speedtest_engine
    res = await speedtest.run_active_speedtest()
    return res

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
async def receive_esp32_hardware_feed(request: Request, payload: Dict[str, Any] = Body(...)):
    """
    Ingests telemetry from physical ESP32-S3 CoreBoard sentinels over HTTP.
    """
    analyzer = request.app.state.traffic_analyzer
    mains_ok = payload.get("mains_power_ok", True)
    analyzer.update_hardware_sentinel_state(mains_ok=mains_ok)
    return {"status": "ACK", "received_score": payload.get("stability_score")}

@router.post("/mcp/rpc")
async def mcp_json_rpc_endpoint(request: Request, body: Dict[str, Any] = Body(...)):
    """
    HTTP JSON-RPC 2.0 endpoint for MCP requests.
    """
    mcp_server = request.app.state.mcp_server
    return await mcp_server.handle_request(body)
