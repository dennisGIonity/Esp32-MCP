<!-- AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | Policy 986 AED | License AED 900 | (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd -->

# ESP32-MCP node firmware 3 (`firmware/esp32-node`)

The on-device MCP server for ESP32-S3 (primary) and ESP32-C6 (secondary): EWP/1 over MQTT 5 to the
gateway, `ionity-prov/2` over the serial console to the portal. ESP-IDF 5.5 C++ project, built with
PlatformIO (`framework = espidf`) or `idf.py`. Design and test record:
`docs/architecture/PHASE-3-FIRMWARE.md`.

## Build and flash

```bash
# PlatformIO (envs: s3_usb, s3_uart, c6_usb, c6_uart)
cd firmware/esp32-node
pio run -e s3_usb -t upload && pio device monitor -e s3_usb

# ESP-IDF 5.5
. $IDF_PATH/export.sh
idf.py -D SDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.usb" set-target esp32s3 build flash monitor

# Release images + manifest for the portal (all four variants)
python release.py            # -> dist/*.bin, dist/manifest.json, dist/elf/
cp dist/*.bin dist/manifest.json ../../apps/portal/public/firmware/
```

Pick the variant by the port the board is plugged in on: `_usb` = the chip's native USB port
(USB-Serial-JTAG), `_uart` = the CH340/CP210x bridge port. The partition table is the fw 2.x
`min_spiffs` layout, so updating a fw 2.x board with "keep settings" keeps its Wi-Fi and MCP host
(`server` + `mqtt_port` become `host_url` on first boot).

## Layout

| Path | What |
|---|---|
| `main/main.cpp` | boot order and task topology (Core 0 network, Core 1 tools) |
| `main/NodeApp.*` | glue: applied settings, link supervisor, provisioning backend, telemetry hooks |
| `components/ewp` | EWP/1 header, bounded CBOR, CRC-32 (portable) |
| `components/mcp_server` | `MCPServer` (registry, job pool, worker), `Uplink` (frame pool, single publisher), `os.h` |
| `components/tools` | `read_telemetry`, `get_device_info`, `identify`, `set_actuator`, `reboot`; actuator bank (LEDC PWM / relay, lease + failsafe); TELEMETRY/HELLO/STATUS encoders; ESP probes |
| `components/netobserve` | LAN DNS forwarder + cache, query ring log, watchlist, client table (ARP/DHCP/mDNS), `scan_lan`, `ping_host` (esp_ping); tools 0x40-0x47; DNS_BATCH / DNS_FLAGGED encoders |
| `components/net` | Wi-Fi station manager, MQTT 5 link + `net_task`; reconnects with `Backoff` (1 s doubling to 60 s, ±20 % jitter, paused while Wi-Fi is down) |
| `components/provisioning` | `ionity-prov/2` handler, NVS config + fw 2.x migration, console + log hook |
| `test/host` | unit tests, fuzz loops, interop helpers (ASan/UBSan/TSan) |
| `test/interop` | TypeScript `ProvClient` and cbor-x against the C++ code |

The wire constants come from `packages/protocol/generated/ewp_protocol.h` (generated from the
TypeScript contract) - build inside the monorepo or set `EWP_PROTOCOL_DIR`.

## ping_host and reconnect back-off (fw 3.2)

- `ping_host` (EWP tool 0x47): ICMP echo from the board through lwIP `esp_ping`. 1-10 echoes, 100-5000 ms
  timeout, 200-5000 ms interval; one session at a time; a session that cannot fit the request deadline is
  refused up front (INVALID_ARGS). Result: resolved address, sent / received, RTT min / avg / max.
- MQTT: esp-mqtt auto-reconnect is off. A lost or failed connection arms the next attempt from
  `net/Backoff.h`; `net_task` calls `esp_mqtt_client_reconnect()` when it is due. The back-off resets on
  CONNECTED and does not grow while Wi-Fi has no address. `reconnects` in telemetry counts lost sessions.

## NetObserve (fw 3.1)

Off by default. With `dns_mode = "forward"` (prov/2 field, NVS `dns_mode`) the node listens on UDP 53
and forwards to `dns_upstream` (1-2 IPv4 resolvers, `""` = `1.1.1.1,8.8.8.8`); the router's DHCP must
hand out the node's IP as DNS. The node's own lookups go straight to the upstreams.

- Answers are cached (NOERROR with answers only, TTL clamped 15-3600 s); unanswered queries are
  retried after 1.5 s and answered SERVFAIL after 3 s. Only private/own-subnet clients are served.
- Every query lands in a byte ring (`CONFIG_IONITY_DNS_LOG_BYTES`). `DNS_BATCH` goes up every
  `CONFIG_IONITY_DNS_BATCH_PERIOD_S` (10 s) or when the ring is 75 % full, only while the MQTT link is up.
- A watchlist hit sends `DNS_FLAGGED` at once (same name + client at most every 30 s) and drives the
  local alert: a pulse on `pwm0` if configured, else the status LED, for `CONFIG_IONITY_ALERT_HOLD_S`.
- Watchlist: built-in defaults, replaced/appended with `set_watchlist`, stored in NVS (`watchlist`).
- HELLO carries `net` (capabilities), TELEMETRY carries `dns` counters.

Kconfig (`menuconfig` -> Ionity node): actuator pins `PWM0/PWM1/RELAY0` (-1 = none; NVS pins override),
`DNS_BATCH_PERIOD_S`, `ALERT_HOLD_S`, `DNS_CACHE_ENTRIES`, `DNS_LOG_BYTES`, `DNS_PENDING`.

## Test (no board needed)

```bash
IDF_PATH=~/esp/idf-v5.5.5 firmware/esp32-node/test/run.sh    # or: npm run test:firmware
```

Unit tests under ASan + UBSan, the routing tests again under TSan, randomised robustness runs on
every parser that sees network or serial input, and the TypeScript interop suite.

## Adding a tool

```cpp
static void myTool(mcp::ToolContext& ctx) {        // runs on mcp_worker (Core 1)
    // parse ctx.args() with tools::parseArgs(); on bad input: ctx.fail(EWP_ST_INVALID_ARGS, "...", key)
    ewp::CborMap m(ctx.out());                     // write exactly one CBOR item
    m.key(1).uint(42);
}
server.registerTool({0x07, 1, EWP_TOOL_FLAG_READ_ONLY, "my_tool", &myTool, nullptr});  // before server.begin()
```

The id, keys and result shape belong in `packages/protocol` first; regenerate the C header.
