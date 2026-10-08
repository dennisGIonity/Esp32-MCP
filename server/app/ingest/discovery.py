"""
AEDI - IONITY GLOBAL | mDNS service advertisement
Doc ID: DOC-2026-09-ESP32MCP-MDNS | Policy 986 AED
Author: Johan Wilhelm van Antwerp | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd | AEDI
Governance: Policy 986 AED | License: AED 900 | CC BY-NC-SA 4.0 where stated
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd - All Rights Reserved - TM2
Owner: github.com/Ionity-Global | www.ionity.today | ai@ionity.today

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


def local_ipv4s() -> set[str]:
    """Every IPv4 this host currently holds (all adapters)."""
    ips: set[str] = {"127.0.0.1"}
    try:
        ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    ips.add(primary_lan_ip())
    return ips


def choose_advertise_ip(configured: str) -> tuple[str, str | None]:
    """The pinned IONITY_MDNS_ADVERTISE_IP if this host really has it, else the
    routed LAN IP - plus a warning explaining the fallback.

    Found 2026-09-29: .env pinned 192.168.124.4 (the H3C lab NIC). With that
    cable out, mDNS kept telling every board to use an address nothing could
    reach, so the whole fleet went offline while the server looked healthy."""
    configured = (configured or "").strip()
    if not configured:
        return primary_lan_ip(), None
    local = local_ipv4s()
    if configured in local:
        return configured, None
    # Same /24 first: the pin says WHICH network the boards live on (the lab),
    # even when DHCP handed this host a different address on it (.2 vs .4).
    # Falling back to the default-route NIC would point lab boards at the
    # household WiFi, which they cannot reach.
    prefix = configured.rsplit(".", 1)[0] + "."
    same = sorted(ip for ip in local if ip.startswith(prefix) and ip != "127.0.0.1")
    if same:
        return same[0], (f"IONITY_MDNS_ADVERTISE_IP={configured} is not on this host; using "
                         f"{same[0]} on the same network {prefix}0/24 (pin it in the H3C DHCP)")
    ip = primary_lan_ip()
    return ip, (f"IONITY_MDNS_ADVERTISE_IP={configured} is not on any adapter of this host "
                f"(cable out / network changed) - advertising {ip} instead")


class DiscoveryService:
    def __init__(self, settings):
        self.s = settings
        self.zc = None
        self.info = None
        self.ip: str | None = None
        self.error: str | None = None
        self.warning: str | None = None

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
            self.ip, self.warning = choose_advertise_ip(self.s.mdns_advertise_ip)
            if self.warning:
                log.warning("mDNS: %s", self.warning)
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
            if type(e).__name__ == "NonUniqueNameException":
                self.error = (f"another fleet server already advertises {MDNS_HOSTNAME}.local on this "
                              "network; run one fleet server per network (boards use whichever answers)")
            else:
                self.error = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            log.warning("mDNS advertisement failed: %s", self.error)

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
            "warning": self.warning,
        }
