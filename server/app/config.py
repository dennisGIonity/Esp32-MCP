"""
AEDI - IONITY GLOBAL | ESP32-MCP Fleet Server configuration.
Governance: Policy 986 AED | (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd
"""
from __future__ import annotations

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"), env_prefix="IONITY_", extra="ignore"
    )

    # --- HTTP -------------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = 8099
    cors_origins: str = "*"

    # --- Identity ---------------------------------------------------------
    fleet_name: str = "Ionity ESP32-MCP Fleet"
    fleet_token: str = "dev-fleet-token-change-me"
    require_token: bool = False          # flip True once devices are provisioned
    # Operator token for anything that CHANGES devices (REST /cmd and MCP
    # send_command). Empty = open (lab default). Set it before the server is
    # reachable from a network you don't fully control.
    admin_token: str = ""

    # --- MQTT -------------------------------------------------------------
    mqtt_enabled: bool = True
    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_root: str = "ionity"
    mqtt_client_id: str = "ionity-fleet-server"

    # --- Storage ----------------------------------------------------------
    # Swap driver to "timescale" later; the Store interface does not change.
    storage_driver: str = "sqlite"
    sqlite_path: str = str(ROOT / "data" / "fleet.db")
    retention_days: int = 30

    # --- Fleet health -----------------------------------------------------
    # A device is STALE after this long without telemetry, OFFLINE after 3x.
    stale_after_s: int = 45
    offline_after_s: int = 135

    # --- Alert thresholds (evaluated on every reading) --------------------
    alert_rssi_dbm: int = -85
    alert_packet_loss_pct: float = 20.0
    alert_temp_c: float = 80.0
    alert_free_heap_bytes: int = 20000

    # --- Discovery --------------------------------------------------------
    # Boards resolve "ionity-fleet.local" rather than a compiled-in IP, so a
    # router or subnet change does not silently orphan the whole fleet.
    mdns_enabled: bool = True
    # The address boards are told to use. Empty = auto (the interface the OS
    # routes internet traffic through). On a multi-homed host that choice can
    # flip when a cable is plugged in - set this explicitly for a lab.
    mdns_advertise_ip: str = ""

    # --- LAN DNS visibility ----------------------------------------------
    # An ESP32 WiFi client cannot see other devices' DNS (per-client WPA keys
    # + switched unicast). So the resolver runs here instead: point the
    # router's DHCP at this host as LAN DNS and every query arrives by design.
    dns_enabled: bool = True
    dns_bind: str = "0.0.0.0"
    dns_port: int = 53                   # 5353 is handy for testing unprivileged
    dns_upstreams: str = "1.1.1.1,8.8.8.8"
    dns_timeout_s: float = 3.0
    dns_retention_days: int = 14

    # --- Datadog (fleet host forwards; boards never hold the key) --------
    dd_enabled: bool = False
    dd_api_key: str = ""
    dd_site: str = "datadoghq.com"       # datadoghq.eu, us3/us5.datadoghq.com, ap1.datadoghq.com
    dd_env: str = "lab"
    dd_service: str = "ionity-esp32-mcp"
    dd_metric_prefix: str = "ionity.esp32"
    dd_tags: str = ""                    # extra comma-separated tags, e.g. "team:iot,region:za"
    dd_flush_s: float = 15.0

    # --- On-device MCP bridge ---------------------------------------------
    # device_call_tool waits this long for the board's JSON-RPC reply over MQTT.
    device_rpc_timeout_s: float = 8.0

    # --- Flasher --------------------------------------------------------------
    # Images served to the React flasher at /flasher (built by firmware/build.py).
    firmware_dist: str = str(ROOT / "firmware" / "dist")
    # The host the flasher tells boards to report to. Empty = this host's LAN IP.
    public_host: str = ""

    # --- Dashboard broadcast ---------------------------------------------
    ws_broadcast_interval_s: float = 2.0

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
