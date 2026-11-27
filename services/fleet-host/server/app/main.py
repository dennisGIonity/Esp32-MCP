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
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings, ROOT, SERVICE_DIR
from app.storage.sqlite_store import SQLiteStore
from app.fleet.registry import FleetRegistry
from app.ingest.mqtt_bridge import MqttBridge
from app.ingest.dns_resolver import DnsService
from app.ingest.net_alerts import NetworkWatch
from app.ingest.discovery import DiscoveryService, primary_lan_ip
from app.mcp.server import FleetMCPServer
from app.api.routes import router, ws_clients
from app.api.netobserve_routes import router as net_router
from app.mcp.http_transport import router as mcp_router
from app.integrations.datadog import DatadogForwarder
from app.labels import LabelBook
from app.rules.engine import RuleEngine, RuleError
from app.dns.pipeline import DnsPipeline
from app.ewp.hub import EwpHub

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("ionity.main")

DASHBOARD_DIR = SERVICE_DIR / "dashboard"
# The board portal (apps/portal: flash, provision, fleet, live DNS, watchlist).
# Repo layout first, then the flat kit / container layout.
PORTAL_DIR = next((p for p in (ROOT / "apps" / "portal" / "dist", ROOT / "portal" / "dist") if p.exists()),
                  ROOT / "portal" / "dist")
# DEPRECATED: the 2.x React flasher, served only if someone still builds it.
FLASHER_DIR = next((p for p in (ROOT / "apps" / "flasher-legacy" / "dist", ROOT / "flasher" / "dist") if p.exists()),
                   ROOT / "flasher" / "dist")


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

    labels = LabelBook(Path(settings.labels_file))
    rules = RuleEngine(store)
    await rules.load()
    for dom in [d.strip() for d in settings.watch_domains.split(",") if d.strip()]:
        if not rules.match(dom.lstrip("*.")):
            try:
                await rules.add_rule(dom, "custom:env")
            except RuleError as e:
                log.warning("IONITY_WATCH_DOMAINS entry '%s' ignored: %s", dom, e)
    pipeline = DnsPipeline(store, rules, labels, settings)
    await pipeline.start()
    hub = EwpHub(registry, pipeline, rules, settings)

    app.state.store = store
    app.state.registry = registry
    app.state.labels = labels
    app.state.rules = rules
    app.state.pipeline = pipeline
    app.state.ewp = hub
    app.state.mqtt = None
    app.state.dns = None

    if settings.mqtt_enabled:
        bridge = MqttBridge(registry, hub)
        await bridge.start()
        app.state.mqtt = bridge

    netwatch = NetworkWatch(store, registry, settings, hub)
    app.state.netwatch = netwatch
    pipeline.flag_listeners.append(netwatch.on_flag)
    rules.listeners.append(hub.sync_all)
    hub.node_listeners.append(lambda hwid, state: pipeline.publish(
        {"type": "node", "device_id": hwid, "state": state}))
    if settings.dns_enabled:
        dns = DnsService(store, settings)
        dns.watch = netwatch                 # new-device alerts from the LAN sweep
        dns.pipeline = pipeline              # every query -> attribution, rules, storage, live feed
        dns.labels = labels
        await dns.start()
        app.state.dns = dns
    await netwatch.start()

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
                                   integrations=app.state.integrations,
                                   hub=hub, pipeline=pipeline, rules=rules, labels=labels)
    await app.state.mcp.start()

    bcast = asyncio.create_task(broadcaster(app))

    log.info("=" * 66)
    log.info(" IONITY ESP32-MCP FLEET SERVER  |  %s", settings.fleet_name)
    log.info(" HTTP      http://%s:%s", settings.host, settings.port)
    log.info(" Dashboard http://%s:%s/", settings.host, settings.port)
    log.info(" MCP       http://%s:%s/mcp  (Streamable HTTP; v2 JSON: /api/v1/mcp/rpc)", settings.host, settings.port)
    log.info(" Portal    http://%s:%s/portal/  (%s)", settings.host, settings.port,
             "built" if PORTAL_DIR.exists() else "not built: npm run build -w @ionity/portal")
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

    # Graceful shutdown (uvicorn turns SIGINT / SIGTERM into this): stop taking
    # traffic first, then drain every queue to disk, then close the database.
    bcast.cancel()
    for ws in list(ws_clients):
        try:
            await ws.close(code=1001)
        except Exception:                    # noqa: BLE001
            pass
    await netwatch.stop()
    await datadog.stop()
    await discovery.stop()
    if app.state.dns:
        await app.state.dns.stop()
    if app.state.mqtt:
        await app.state.mqtt.stop()
    await app.state.mcp.stop()
    await hub.stop()
    await pipeline.stop()
    await registry.stop()
    await store.close()
    log.info("Fleet server shut down cleanly.")


app = FastAPI(
    title="Ionity ESP32-MCP Fleet Server",
    description=(
        "Telemetry ingest, fleet registry, alerting and Model Context Protocol "
        "gateway for 1000+ ESP32 edge nodes. Policy 986 AED."
    ),
    version="2.2.0",
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
    expose_headers=["Mcp-Session-Id"],
)

app.include_router(router)
app.include_router(net_router)
app.include_router(mcp_router)

@app.middleware("http")
async def dashboard_no_cache(request, call_next):
    """Browsers must revalidate the dashboard, or they keep showing an old UI
    after an update (cheap: unchanged files come back as 304)."""
    response = await call_next(request)
    p = request.url.path
    if p == "/" or p.startswith("/static/") or p in ("/portal/", "/portal/index.html", "/flasher/", "/flasher/index.html"):
        response.headers["Cache-Control"] = "no-cache"
    return response


if DASHBOARD_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(DASHBOARD_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def dashboard_index():
        return FileResponse(str(DASHBOARD_DIR / "index.html"))


if PORTAL_DIR.exists():
    # The board portal. Web Serial works on http://localhost (and the host's own
    # page needs no CORS for the fleet API / WebSockets), so the lab needs no
    # certificate for it. Hashed assets are immutable; index.html revalidates.
    app.mount("/portal", StaticFiles(directory=str(PORTAL_DIR), html=True), name="portal")

    @app.get("/portal", include_in_schema=False)
    async def portal_slash():
        return RedirectResponse("/portal/", status_code=308)

if FLASHER_DIR.exists():
    app.mount("/flasher", StaticFiles(directory=str(FLASHER_DIR), html=True), name="flasher")
elif PORTAL_DIR.exists():
    # Old bookmarks and printed labels point at /flasher/: the portal replaces it.
    @app.get("/flasher", include_in_schema=False)
    @app.get("/flasher/{rest:path}", include_in_schema=False)
    async def flasher_moved(rest: str = ""):
        return RedirectResponse("/portal/", status_code=308)
