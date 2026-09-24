from typing import Dict, Any, List

MCP_TOOLS_DEFINITIONS = [
    {
        "name": "get_link_stability",
        "description": "Returns real-time link stability score (0-100%), packet loss %, jitter (ms), latency, and WAN failover state for the Kelvin Drive site.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "detailed": {
                    "type": "boolean",
                    "description": "Whether to return target-by-target breakdown (Google DNS, Cloudflare, Liquid ISP gateways)."
                }
            }
        }
    },
    {
        "name": "get_traffic_feed",
        "description": "Returns live ingress/egress bandwidth (Mbps) across WAN1 (vlan 11 Liquid Fibre), WAN2 (ether9 Liquid Fibre), LAN Bonding (ether2-5), PBX VoIP (ether10), and all floor VLANs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "floor_id": {
                    "type": "string",
                    "description": "Optional filter by floor ID: 'ground_floor', 'first_floor', 'lower_ground', 'pbx_voip'."
                }
            }
        }
    },
    {
        "name": "run_speedtest",
        "description": "Executes an active network speed test or retrieves the latest bandwidth, jitter, and speed-bar measurement.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "force_fresh_test": {
                    "type": "boolean",
                    "description": "If true, performs a fresh multi-stream active speedtest."
                }
            }
        }
    },
    {
        "name": "get_loadshedding_status",
        "description": "Returns South African Eskom load shedding stage (0-8), area schedule for Sandton / Kelvin Drive, and outage correlation analysis (distinguishes power grid cuts from ISP fibre breaks).",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "get_security_alerts",
        "description": "Returns recent alerts and flagged IP addresses probing blocked ports (Telnet 23, FTP 21, HTTP 80, SSH 22, API 8728) as defined in the Kelvin Drive firewall policy.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of threat alerts to return (default 10)."
                }
            }
        }
    },
    {
        "name": "get_network_datasheet",
        "description": "Returns the complete hardware datasheet and network topology blueprint for the Kelvin Drive site (MikroTik CRS326, Liquid Fibre subnets, VLAN mappings, PBX forwardings).",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    }
]

MCP_RESOURCES_DEFINITIONS = [
    {
        "uri": "sentinel://kelvin-drive/datasheet",
        "name": "Kelvin Drive Network Blueprint & Datasheet",
        "description": "Complete network topology, subnets, firewall rules, and hardware specifications for the Kelvin Drive site.",
        "mimeType": "application/json"
    },
    {
        "uri": "sentinel://kelvin-drive/telemetry/live",
        "name": "Live Network Telemetry Stream",
        "description": "Real-time stream of all WAN, LAN, stability, and load shedding metrics.",
        "mimeType": "application/json"
    }
]
