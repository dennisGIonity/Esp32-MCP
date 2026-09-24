"""
MQTT bridge - primary ingest path.

Subscribes once with wildcards, so adding the 1001st device requires no
server change:
    ionity/+/+/telemetry
    ionity/+/+/status
    ionity/+/+/cmd/result
Publishes commands to:
    ionity/<site>/<device_id>/cmd      (or ionity/broadcast/cmd)

Why paho-mqtt on its own thread, not aiomqtt
--------------------------------------------
The first version used aiomqtt. aiomqtt drives paho through
loop.add_reader()/add_writer(), which Windows' default ProactorEventLoop does
not implement - so on this host the bridge could never connect, and because
no broker was running at the time the failure looked like an ordinary
"connection refused". Forcing a SelectorEventLoop would fix aiomqtt but break
asyncio subprocesses (the DNS service's ARP refresh needs those on Windows).

So the MQTT client runs on paho's own network thread and hands messages to the
asyncio loop with call_soon_threadsafe. It is independent of whichever event
loop uvicorn chose, on every platform.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading

import paho.mqtt.client as paho

from app.config import settings
from app.models import TelemetryIn, StatusIn
from app.fleet.registry import FleetRegistry

log = logging.getLogger("ionity.mqtt")


class MqttBridge:
    def __init__(self, registry: FleetRegistry):
        self.registry = registry
        self.loop: asyncio.AbstractEventLoop | None = None
        self.client: paho.Client | None = None
        self.connected = False
        self.received = 0
        self.errors = 0
        self.cmd_results = 0
        self._lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        try:
            c = paho.Client(callback_api_version=paho.CallbackAPIVersion.VERSION2,
                            client_id=settings.mqtt_client_id, clean_session=True)
        except AttributeError:                       # paho < 2.0
            c = paho.Client(client_id=settings.mqtt_client_id, clean_session=True)
        if settings.mqtt_username:
            c.username_pw_set(settings.mqtt_username, settings.mqtt_password)
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        c.on_message = self._on_message
        c.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client = c
        self.registry.command_publisher = self.publish_command

        # connect_async + loop_start: never blocks startup, and paho keeps
        # retrying on its own thread until the broker appears.
        c.connect_async(settings.mqtt_host, settings.mqtt_port, keepalive=60)
        c.loop_start()
        log.info("MQTT bridge started -> %s:%s (paho network thread)",
                 settings.mqtt_host, settings.mqtt_port)

    async def stop(self) -> None:
        if self.client:
            try:
                self.client.disconnect()
            finally:
                self.client.loop_stop()

    # -- paho callbacks (run on paho's thread) ---------------------------
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        ok = (reason_code == 0) if isinstance(reason_code, int) else not reason_code.is_failure
        if not ok:
            log.warning("MQTT connect refused: %s", reason_code)
            return
        self.connected = True
        root = settings.mqtt_root
        client.subscribe([(f"{root}/+/+/telemetry", 0),
                          (f"{root}/+/+/status", 1),
                          (f"{root}/+/+/cmd/result", 1)])
        log.info("MQTT connected to %s:%s", settings.mqtt_host, settings.mqtt_port)

    def _on_disconnect(self, client, userdata, *args):
        if self.connected:
            log.warning("MQTT disconnected - paho will reconnect; "
                        "devices fall back to HTTP in the meantime")
        self.connected = False

    def _on_message(self, client, userdata, msg):
        # Hand off to the asyncio loop; registry methods are not thread-safe.
        if self.loop:
            self.loop.call_soon_threadsafe(self._handle, msg.topic, msg.payload)

    # -- message handling (runs on the asyncio loop) ---------------------
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
                net = data.setdefault("net", {})
                net["transport"] = "mqtt"
                self.registry.ingest(TelemetryIn(**data))
            elif topic.endswith("/status"):
                self.registry.ingest_status(StatusIn(**data))
            elif topic.endswith("/cmd/result"):
                self.cmd_results += 1
                self.registry.record_cmd_result(data)
                log.info("cmd result from %s: %s (ok=%s)",
                         data.get("device_id"), data.get("detail"), data.get("ok"))
        except Exception:
            self.errors += 1
            log.debug("bad payload on %s", topic, exc_info=True)

    # -- outbound ----------------------------------------------------------
    async def publish_command(self, device_id: str, action: str, body: dict) -> bool:
        if not self.client or not self.connected:
            return False
        if device_id == "broadcast":
            topic = f"{settings.mqtt_root}/broadcast/cmd"
        else:
            d = self.registry.devices.get(device_id, {})
            site = d.get("site", "lab")
            topic = f"{settings.mqtt_root}/{site}/{device_id}/cmd"
        info = self.client.publish(topic, json.dumps(body), qos=1)
        ok = info.rc == paho.MQTT_ERR_SUCCESS
        log.info("cmd -> %s : %s (%s)", topic, action, "queued" if ok else f"rc={info.rc}")
        return ok

    def stats(self) -> dict:
        return {
            "connected": self.connected,
            "host": f"{settings.mqtt_host}:{settings.mqtt_port}",
            "received": self.received,
            "decode_errors": self.errors,
            "cmd_results": self.cmd_results,
            "client": "paho (threaded)",
        }
