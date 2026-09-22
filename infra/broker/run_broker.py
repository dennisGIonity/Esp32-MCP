#!/usr/bin/env python3
"""
AEDI - IONITY GLOBAL | Lab MQTT broker (no Docker, no admin)
Doc ID: DOC-2026-09-ESP32MCP-BROKER | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd

A pure-Python MQTT 3.1.1 broker (amqtt) for the lab. Mosquitto via Docker is
still the production path (docker-compose.yml), but Docker Desktop on this host
had never completed first-run onboarding, and the lab should not depend on a
GUI sign-in to get commands to its devices.

Supports what the fleet uses: QoS 0/1, retained messages (device status),
Last Will (offline detection). Anonymous access - lab only.

Runs from its own venv (.venv-broker) so the broker's dependency pins can never
break the fleet server.
"""
from __future__ import annotations

import asyncio
import logging
import sys

from amqtt.broker import Broker

BIND = sys.argv[1] if len(sys.argv) > 1 else "0.0.0.0:1883"

CONFIG = {
    "listeners": {
        "default": {"type": "tcp", "bind": BIND, "max_connections": 0},
    },
    "sys_interval": 0,
    "auth": {"allow-anonymous": True, "plugins": ["auth_anonymous"]},
    "topic-check": {"enabled": False},
    # amqtt >= 0.12 reads plugin config from here; the keys above cover older
    # releases. Both are harmless if unused.
    "plugins": {
        "amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True},
    },
}


async def main() -> None:
    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s [broker] %(levelname)s %(message)s")
    log = logging.getLogger("ionity.broker")
    broker = Broker(CONFIG)
    await broker.start()
    log.warning("Ionity lab MQTT broker listening on %s (anonymous, lab only)", BIND)
    try:
        await asyncio.Event().wait()
    finally:
        await broker.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
