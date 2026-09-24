import os
import yaml
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Set
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from core.mikrotik_collector import MikroTikCollector
from core.probe_engine import ProbeEngine
from core.loadshedding import LoadSheddingEngine
from core.speedtest_engine import SpeedtestEngine
from core.security_analyzer import SecurityAnalyzer
from core.traffic_analyzer import TrafficAnalyzer
from mcp.mcp_server import SentinelMCPServer
from api.routes import router as api_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("sentinel.main")

# Active WebSocket client connections
connected_websockets: Set[WebSocket] = set()

def load_config() -> dict:
    config_path = Path(__file__).resolve().parent.parent / "config" / "config.yaml"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Load config
    config = load_config()
    app.state.config = config

    # 2. Instantiate Core Engines
    collector = MikroTikCollector(config)
    probe_engine = ProbeEngine(config)
    loadshedding_engine = LoadSheddingEngine(config)
    speedtest_engine = SpeedtestEngine(config)
    security_analyzer = SecurityAnalyzer(config)
    traffic_analyzer = TrafficAnalyzer(
        config=config,
        collector=collector,
        probe_engine=probe_engine,
        loadshedding_engine=loadshedding_engine,
        speedtest_engine=speedtest_engine,
        security_analyzer=security_analyzer
    )
    mcp_server = SentinelMCPServer(traffic_analyzer, config)

    # Attach to app state
    app.state.collector = collector
    app.state.probe_engine = probe_engine
    app.state.loadshedding_engine = loadshedding_engine
    app.state.speedtest_engine = speedtest_engine
    app.state.security_analyzer = security_analyzer
    app.state.traffic_analyzer = traffic_analyzer
    app.state.mcp_server = mcp_server

    logger.info("🚀 Sentinel Core Engines & MCP Server initialized.")

    # 3. Background WebSocket Broadcast Loop
    broadcast_task = asyncio.create_task(telemetry_broadcaster(app))

    yield

    # Clean shutdown
    broadcast_task.cancel()
    try:
        await broadcast_task
    except asyncio.CancelledError:
        pass
    logger.info("🛑 Sentinel service shut down cleanly.")

async def telemetry_broadcaster(app: FastAPI):
    """
    Broadcasts live telemetry to all connected dashboard WebSockets every 1.5 seconds.
    """
    logger.info("📡 Real-time WebSocket broadcaster active.")
    while True:
        try:
            if connected_websockets:
                analyzer = app.state.traffic_analyzer
                data = await analyzer.get_live_telemetry()
                
                # Send to all connected sockets
                dead_sockets = set()
                for ws in list(connected_websockets):
                    try:
                        await ws.send_json(data)
                    except Exception:
                        dead_sockets.add(ws)
                
                for dead in dead_sockets:
                    connected_websockets.discard(dead)

            await asyncio.sleep(1.5)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in telemetry broadcast loop: {e}")
            await asyncio.sleep(2.0)

app = FastAPI(
    title="Kelvin Drive Network Sentinel",
    description="Edge Telemetry, Stability Sentinel, MCP Server, and Dashboard for MikroTik CRS326",
    version="2.4.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

@app.websocket("/ws/telemetry")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    connected_websockets.add(websocket)
    try:
        # Push initial state immediately
        analyzer = app.state.traffic_analyzer
        init_data = await analyzer.get_live_telemetry()
        await websocket.send_json(init_data)

        while True:
            # Keep socket alive and handle any incoming client messages
            msg = await websocket.receive_text()
            if msg == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        connected_websockets.discard(websocket)
    except Exception:
        connected_websockets.discard(websocket)

# Mount Dashboard Frontend
dashboard_path = Path(__file__).resolve().parent.parent / "dashboard"
if dashboard_path.exists():
    app.mount("/", StaticFiles(directory=str(dashboard_path), html=True), name="dashboard")
