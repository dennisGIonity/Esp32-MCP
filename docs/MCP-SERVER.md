<!--
AEDI - IONITY GLOBAL | DOC-2026-09-ESP32MCP-008 | v1.0.0 | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Classification: PUBLIC
-->

# MCP server — connection details

One MCP server fronts the whole fleet. An agent sees 13 tools and reasons
about every device as a single system, however many boards there are.

## Endpoints

| Transport | Address | Use for |
|---|---|---|
| **HTTP** | `POST http://192.168.0.3:8099/api/v1/mcp/rpc` | AEDi, dashboards, curl, anything on the LAN |
| **stdio** | `E:\.ESP32-MCP\.venv\Scripts\python.exe E:\.ESP32-MCP\server\mcp_stdio_proxy.py` | Claude Desktop, Claude Code — clients that spawn a subprocess |

Server identity: `ionity-esp32-fleet-mcp` v1.0.0, protocol `2024-11-05`.

## Claude Desktop — already wired up

Added to `%APPDATA%\Claude\claude_desktop_config.json` (a timestamped backup
of the previous file sits beside it):

```json
{
  "mcpServers": {
    "ionity-esp32-fleet": {
      "command": "E:\\.ESP32-MCP\\.venv\\Scripts\\python.exe",
      "args": ["E:\\.ESP32-MCP\\server\\mcp_stdio_proxy.py"],
      "env": { "IONITY_MCP_URL": "http://127.0.0.1:8099/api/v1/mcp/rpc" }
    }
  }
}
```

**Restart Claude Desktop** for it to pick this up.

### Why a bridge instead of running the MCP server directly

`mcp_stdio_proxy.py` is a thin JSON-RPC forwarder to the running fleet server.
That indirection is deliberate. The fleet server holds the live registry —
which devices are online *right now*, their current metrics, open alerts, and
the MQTT publisher that `send_command` needs. A second process opening the
SQLite file would only ever see a stale snapshot and could not command a
device. Forwarding means MCP always reflects reality.

If the fleet server is down, the bridge returns a clear JSON-RPC error
(`-32001`) naming the problem, instead of hanging until the client times out.

## The 13 tools

**Fleet**

| Tool | Answers |
|---|---|
| `fleet_summary` | Whole-fleet health in one call. Start here. |
| `list_devices` | Filter by site / group / health / free text, paged. |
| `get_device` | One device plus recent history. |
| `query_telemetry` | Raw time-series for a metric over a window. |
| `aggregate_metric` | Mean/min/max/count + top-10 devices for a metric. |
| `get_alerts` | Open or historical alerts. |
| `send_command` | reboot / identify / ping / set_meta — one device or `broadcast`. Needs MQTT. |

**LAN DNS**

| Tool | Answers |
|---|---|
| `dns_summary` | Queries, domains, devices, cache hit rate, latency. |
| `dns_by_device` | **What's going to what device** — per IP with its top domains. |
| `dns_top_domains` | Busiest domains, optionally scoped to one device. |
| `dns_search` | Has anything on the LAN resolved *X*, and who. |
| `dns_recent` | Live tail. |
| `list_lan_devices` | Who is on the network: IP, MAC, vendor, hostname. |

Resources: `ionity://fleet/summary`, `ionity://fleet/devices`,
`ionity://fleet/schema`.

## Things you can now ask

- "How is the ESP32 fleet doing?"
- "Which devices are alerting, and why?"
- "What's the average temperature across the fleet in the last hour?"
- "Which device on my network has been talking to windowsupdate.com today?"
- "Show me every device that resolved anything containing 'tiktok'."
- "Reboot esp32-98a316e5d18c." *(needs the MQTT broker running)*

## Prerequisite: the fleet server must be running

MCP, the dashboard and ingest all depend on it.

```powershell
E:\.ESP32-MCP\scripts\start_fleet.ps1        # starts it if it isn't already
```

To survive a reboot, register it as a scheduled task that runs at logon:

```powershell
schtasks /create /tn "Ionity Fleet Server" /sc onlogon /rl highest ^
  /tr "E:\.ESP32-MCP\.venv\Scripts\pythonw.exe E:\.ESP32-MCP\server\run.py"
```

Worth doing before you rely on the DNS resolver — once the router points the
LAN at `192.168.0.3` for DNS, this host going down stops name resolution for
every device pointed at it.

## Quick check without any client

```powershell
curl -X POST http://192.168.0.3:8099/api/v1/mcp/rpc ^
  -H "Content-Type: application/json" ^
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}"
```

---

*Governance: Policy 986 AED · © 2018-2026 Antwerp Designs | Ionity (Pty) Ltd*
