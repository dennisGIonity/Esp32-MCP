#!/usr/bin/env python3
"""
AEDI - IONITY GLOBAL | Ionity Lab MQTT broker (no Docker, no admin)
Doc ID: DOC-2026-09-IONITY-LAB | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd

A pure-Python MQTT 3.1.1 broker (amqtt) shared by every project in the lab.
Mosquitto via Docker stays the production path; the lab must not depend on a
GUI sign-in (Docker Desktop onboarding) to get commands to its devices.

Supports what the projects use: QoS 0/1, retained messages (device status),
Last Will (offline detection). Anonymous access - lab network only.

Runs from the lab's own venv (.venv) so its dependency pins can never break a
project's server. Testers package: started by START.cmd (tools\tester.ps1). In the lab: lab.ps1 start
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
