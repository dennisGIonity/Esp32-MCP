"""
MQTT bridge - primary ingest path.

Subscribes once with wildcards, so adding the 1001st device requires no
server change:
    ionity/+/+/telemetry
    ionity/+/+/status
Publishes commands to:
    ionity/<site>/<device_id>/cmd      (or ionity/broadcast/cmd)
"""
from __future__ import annotations

import asyncio
import json
import logging

import aiomqtt

from app.config import settings
from app.models import TelemetryIn, StatusIn
from app.fleet.registry import FleetRegistry

log = logging.getLogger("ionity.mqtt")


class MqttBridge:
    def __init__(self, registry: FleetRegistry):
        self.registry = registry
        self._task: asyncio.Task | None = None
        self._client: aiomqtt.Client | None = None
        self.connected = False
        self.received = 0
        self.errors = 0

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())
        self.registry.command_publisher = self.publish_command

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        backoff = 1
        while True:
            try:
                kwargs = {}
                if settings.mqtt_username:
                    kwargs["username"] = settings.mqtt_username
                    kwargs["password"] = settings.mqtt_password
                async with aiomqtt.Client(
                    hostname=settings.mqtt_host,
                    port=settings.mqtt_port,
                    identifier=settings.mqtt_client_id,
                    keepalive=60,
                    **kwargs,
                ) as client:
                    self._client = client
                    self.connected = True
                    backoff = 1
                    log.info("MQTT connected to %s:%s", settings.mqtt_host, settings.mqtt_port)
                    await client.subscribe(f"{settings.mqtt_root}/+/+/telemetry", qos=0)
                    await client.subscribe(f"{settings.mqtt_root}/+/+/status", qos=1)
                    await client.subscribe(f"{settings.mqtt_root}/+/+/cmd/result", qos=1)

                    async for msg in client.messages:
                        self._handle(str(msg.topic), msg.payload)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.connected = False
                self._client = None
                log.warning("MQTT disconnected (%s) - retrying in %ss. "
                            "Devices will fall back to HTTP ingest.", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    def _handle(self, topic: str, payload: bytes) -> None:
        try:
            data = json.loads(payload.decode("utf-8", "replace") or "{}")
        except json.JSONDecodeError:
            self.errors += 1
            return
        if not isinstance(data, dict) or not data.get("device_id"):
            self.errors += 1
            return

        self.received += 1
        try:
            if topic.endswith("/telemetry"):
                self.registry.ingest(TelemetryIn(**data))
            elif topic.endswith("/status"):
                self.registry.ingest_status(StatusIn(**data))
            elif topic.endswith("/cmd/result"):
                log.info("cmd result from %s: %s", data.get("device_id"), data.get("detail"))
        except Exception:
            self.errors += 1
            log.debug("bad payload on %s", topic, exc_info=True)

    async def publish_command(self, device_id: str, action: str, body: dict) -> bool:
        if not self._client or not self.connected:
            return False
        if device_id == "broadcast":
            topic = f"{settings.mqtt_root}/broadcast/cmd"
        else:
            d = self.registry.devices.get(device_id, {})
            site = d.get("site", "ionity-local")
            topic = f"{settings.mqtt_root}/{site}/{device_id}/cmd"
        await self._client.publish(topic, json.dumps(body), qos=1)
        log.info("cmd -> %s : %s", topic, action)
        return True

    def stats(self) -> dict:
        return {
            "connected": self.connected,
            "host": f"{settings.mqtt_host}:{settings.mqtt_port}",
            "received": self.received,
            "decode_errors": self.errors,
        }
