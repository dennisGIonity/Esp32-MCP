"""
AEDI - IONITY GLOBAL | mDNS service advertisement
Doc ID: DOC-2026-09-ESP32MCP-MDNS | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd

Why this exists
---------------
The first firmware had the server's address compiled in as 192.168.2.11.
Swapping the router moved the whole LAN to 192.168.0.x and every board was
instantly talking to an address that no longer existed - silently, because a
failed POST looks the same as a quiet sensor.

Hardcoding an IP into 1000 devices is a single point of failure you cannot
reach to fix. So the server now announces itself over mDNS as
`ionity-fleet.local` and the boards resolve that name at boot. Change routers,
change subnets, re-address the host: the fleet follows.

Advertises:
  * host record   ionity-fleet.local        -> this machine's LAN IP
  * service       _ionity-fleet._tcp.local. -> port + TXT metadata
"""
from __future__ import annotations

import logging
import socket
from typing import Any

log = logging.getLogger("ionity.mdns")

MDNS_HOSTNAME = "ionity-fleet"
SERVICE_TYPE = "_ionity-fleet._tcp.local."


def primary_lan_ip() -> str:
    """The address other devices on the LAN would actually reach us on.

    Opening a UDP socket to a remote address makes the OS pick the interface
    it would really route through - more reliable than taking the first
    address off the adapter list, which on this host also returns a second,
    unrelated 192.168.124.x network.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return socket.gethostbyname(socket.gethostname())
    finally:
        s.close()


class DiscoveryService:
    def __init__(self, settings):
        self.s = settings
        self.zc = None
        self.info = None
        self.ip: str | None = None
        self.error: str | None = None

    async def start(self) -> None:
        if not self.s.mdns_enabled:
            return
        try:
            from zeroconf import ServiceInfo
            from zeroconf.asyncio import AsyncZeroconf
        except ImportError:
            self.error = "zeroconf not importable (install it, or if Windows blocked its DLLs: set SKIP_CYTHON=1 and pip install --force-reinstall --no-binary zeroconf zeroconf)"
            log.warning("mDNS disabled: %s", self.error)
            return

        try:
            self.ip = self.s.mdns_advertise_ip.strip() or primary_lan_ip()
            self.zc = AsyncZeroconf()
            self.info = ServiceInfo(
                SERVICE_TYPE,
                f"{MDNS_HOSTNAME}.{SERVICE_TYPE}",
                addresses=[socket.inet_aton(self.ip)],
                port=self.s.port,
                properties={
                    "path": "/api/v1/telemetry",
                    "mcp": "/api/v1/mcp/rpc",
                    "mqtt": str(self.s.mqtt_port),
                    "fleet": self.s.fleet_name,
                },
                server=f"{MDNS_HOSTNAME}.local.",
            )
            await self.zc.async_register_service(self.info)
            log.info("mDNS advertising %s.local -> %s:%s",
                     MDNS_HOSTNAME, self.ip, self.s.port)
        except Exception as e:                        # noqa: BLE001
            self.error = str(e)
            log.warning("mDNS advertisement failed: %s", e)

    async def stop(self) -> None:
        try:
            if self.zc and self.info:
                await self.zc.async_unregister_service(self.info)
            if self.zc:
                await self.zc.async_close()
        except Exception:
            pass

    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self.s.mdns_enabled,
            "hostname": f"{MDNS_HOSTNAME}.local",
            "advertised_ip": self.ip,
            "service": SERVICE_TYPE,
            "error": self.error,
        }
