"""
AEDI - IONITY GLOBAL | ESP32-MCP Fleet Server
Doc ID: DOC-2026-09-ESP32MCP-SRV | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd - All Rights Reserved
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager

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
from app.integrations.datadog import DatadogForwarder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("ionity.main")

DASHBOARD_DIR = ROOT / "dashboard"
FLASHER_DIR = ROOT / "flasher" / "dist"


async def broadcaster(app: FastAPI) -> None:
    """Push one compact fleet frame to every dashboard client."""
    while True:
        try:
            if ws_clients:
                # Serialise once, not once per client.
                frame = json.dumps(app.state.registry.dashboard_payload(), default=str)
                dead = set()
                for ws in list(ws_clients):
                    try:
                        await ws.send_text(frame)
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

    datadog = DatadogForwarder(settings, registry)
    await datadog.start()
    registry.sinks.append(datadog)
    app.state.datadog = datadog
    app.state.integrations = {"datadog": datadog}
    if app.state.mqtt:
        app.state.integrations["mqtt"] = app.state.mqtt

    app.state.mcp = FleetMCPServer(registry, store, dns=app.state.dns,
                                   integrations=app.state.integrations)

    bcast = asyncio.create_task(broadcaster(app))

    log.info("=" * 66)
    log.info(" IONITY ESP32-MCP FLEET SERVER  |  %s", settings.fleet_name)
    log.info(" HTTP      http://%s:%s", settings.host, settings.port)
    log.info(" Dashboard http://%s:%s/", settings.host, settings.port)
    log.info(" MCP RPC   http://%s:%s/api/v1/mcp/rpc", settings.host, settings.port)
    log.info(" Flasher   http://%s:%s/flasher/  (%s)", settings.host, settings.port,
             "built" if FLASHER_DIR.exists() else "not built: cd flasher && npm run build")
    log.info(" Datadog   %s", f"-> api.{settings.dd_site} env={settings.dd_env}"
             if datadog.enabled else "off (IONITY_DD_ENABLED / IONITY_DD_API_KEY)")
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
    await datadog.stop()
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
    version="2.0.1",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    # Bearer tokens travel in a header, not a cookie: credentials mode is not
    # needed, and "*" + credentials is an invalid combination per Fetch spec.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)

@app.middleware("http")
async def dashboard_no_cache(request, call_next):
    """Browsers must revalidate the dashboard, or they keep showing an old UI
    after an update (cheap: unchanged files come back as 304)."""
    response = await call_next(request)
    p = request.url.path
    if p == "/" or p.startswith("/static/") or p == "/flasher/" or p == "/flasher/index.html":
        response.headers["Cache-Control"] = "no-cache"
    return response


if DASHBOARD_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(DASHBOARD_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def dashboard_index():
        return FileResponse(str(DASHBOARD_DIR / "index.html"))


if FLASHER_DIR.exists():
    # The React flasher (flasher/, `npm run build`). Web Serial works on
    # http://localhost, so the lab needs no certificate for it.
    app.mount("/flasher", StaticFiles(directory=str(FLASHER_DIR), html=True), name="flasher")
