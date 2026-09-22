"""
AEDI - IONITY GLOBAL | ESP32-MCP Fleet Server
Doc ID: DOC-2026-09-ESP32MCP-SRV | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd - All Rights Reserved
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings, ROOT
from app.storage.sqlite_store import SQLiteStore
from app.fleet.registry import FleetRegistry
from app.ingest.mqtt_bridge import MqttBridge
from app.ingest.dns_resolver import DnsService
from app.ingest.discovery import DiscoveryService, primary_lan_ip
from app.mcp.server import FleetMCPServer
from app.api.routes import router, ws_clients

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("ionity.main")

DASHBOARD_DIR = ROOT / "dashboard"


async def broadcaster(app: FastAPI) -> None:
    """Push one compact fleet frame to every dashboard client."""
    while True:
        try:
            if ws_clients:
                payload = app.state.registry.dashboard_payload()
                dead = set()
                for ws in list(ws_clients):
                    try:
                        await ws.send_json(payload)
                    except Exception:
                        dead.add(ws)
                for d in dead:
                    ws_clients.discard(d)
            await asyncio.sleep(settings.ws_broadcast_interval_s)
        except asyncio.CancelledError:
            break
        except Exception:
            log.exception("broadcaster error")
            await asyncio.sleep(2.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = SQLiteStore(settings.sqlite_path)
    await store.init()

    registry = FleetRegistry(store)
    await registry.start()

    app.state.store = store
    app.state.registry = registry
    app.state.mqtt = None
    app.state.dns = None

    if settings.mqtt_enabled:
        bridge = MqttBridge(registry)
        await bridge.start()
        app.state.mqtt = bridge

    if settings.dns_enabled:
        dns = DnsService(store, settings)
        await dns.start()
        app.state.dns = dns

    discovery = DiscoveryService(settings)
    await discovery.start()
    app.state.discovery = discovery

    app.state.mcp = FleetMCPServer(registry, store, dns=app.state.dns)

    bcast = asyncio.create_task(broadcaster(app))

    log.info("=" * 66)
    log.info(" IONITY ESP32-MCP FLEET SERVER  |  %s", settings.fleet_name)
    log.info(" HTTP      http://%s:%s", settings.host, settings.port)
    log.info(" Dashboard http://%s:%s/", settings.host, settings.port)
    log.info(" MCP RPC   http://%s:%s/api/v1/mcp/rpc", settings.host, settings.port)
    log.info(" MQTT      %s:%s (enabled=%s)", settings.mqtt_host,
             settings.mqtt_port, settings.mqtt_enabled)
    log.info(" Storage   %s -> %s", settings.storage_driver, settings.sqlite_path)
    log.info(" LAN IP    %s   (boards reach us here)", primary_lan_ip())
    ds = discovery.stats()
    if ds["advertised_ip"]:
        log.info(" mDNS      %s -> %s  (boards resolve this, not a fixed IP)",
                 ds["hostname"], ds["advertised_ip"])
    elif ds["enabled"]:
        log.warning(" mDNS      unavailable: %s", ds["error"])
    if app.state.dns:
        st = app.state.dns.stats()
        log.info(" LAN DNS   %s (running=%s) upstreams %s",
                 st["bind"], st["running"], ",".join(st["upstreams"]))
        if not st["running"]:
            log.warning(" LAN DNS   bind failed: %s", st["bind_error"])
    log.info("=" * 66)

    yield

    bcast.cancel()
    await discovery.stop()
    if app.state.dns:
        await app.state.dns.stop()
    if app.state.mqtt:
        await app.state.mqtt.stop()
    await registry.stop()
    await store.close()
    log.info("Fleet server shut down cleanly.")


app = FastAPI(
    title="Ionity ESP32-MCP Fleet Server",
    description=(
        "Telemetry ingest, fleet registry, alerting and Model Context Protocol "
        "gateway for 1000+ ESP32 edge nodes. Policy 986 AED."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)

if DASHBOARD_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(DASHBOARD_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def dashboard_index():
        return FileResponse(str(DASHBOARD_DIR / "index.html"))
