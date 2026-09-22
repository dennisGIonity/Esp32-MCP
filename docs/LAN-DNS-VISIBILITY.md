<!--
AEDI - IONITY GLOBAL | DOC-2026-09-ESP32MCP-007 | v1.0.0 | Policy 986 AED
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Classification: PUBLIC
-->

# LAN DNS visibility — what's going to what device

## Why this isn't on the ESP32

It was asked for as "let the ESP check all the DNS traffic and report what's
going to what device." That cannot work, and it is worth being precise about
why, because the reason is physics-of-the-medium, not a missing library.

1. **Per-client encryption keys.** On WPA2/WPA3 every station negotiates its
   own Pairwise Transient Key with the AP. An ESP32 joined as a client has no
   key material for your laptop's frames and cannot decrypt them.
2. **The AP is a switch, not a hub.** Unicast frames addressed to your phone
   are transmitted to your phone. They are never put on the air for the
   ESP32 to overhear, encrypted or not.
3. **Promiscuous mode doesn't rescue it.** The ESP32 *can* enter promiscuous
   mode, but it yields 802.11 headers only — source/destination MAC, frame
   length, RSSI. Never the DNS payload.

So instead of trying to overhear the traffic, **we make the traffic come to
us.** The resolver runs on the fleet server. Point the router's DHCP at it and
every device's DNS queries arrive by design, in the clear, correctly
attributed to the asking IP.

## What you get

| Question | Tool |
|---|---|
| What is my network doing right now? | `dns_summary` |
| **What's going to what device?** | `dns_by_device` — per IP: MAC, vendor, hostname, query count, distinct domains, top 5 domains |
| What are the busiest domains? | `dns_top_domains` |
| Has anything resolved *X*? | `dns_search` |
| Live tail | `dns_recent` |
| Who is on my network? | `list_lan_devices` |

Same surface over REST (`/api/v1/dns/*`, `/api/v1/lan/devices`), in the
dashboard's **LAN DNS** panel, and through MCP so Claude can answer
"which device has been talking to windowsupdate.com today" directly.

## How it works

```
  phone / laptop / TV / ESP32s
            │  UDP 53  (because DHCP told them to)
            ▼
  ┌───────────────────────────────────────┐
  │ DnsService  192.168.0.3:53           │
  │  · parse QNAME + qtype                │
  │  · cache hit?  answer immediately     │
  │  · else forward to 1.1.1.1 / 8.8.8.8  │
  │  · return the real answer to client   │
  │  · log (ts, client_ip, qname, answers)│
  └──────────────┬────────────────────────┘
                 │ batched every 2s
                 ▼
        dns_queries + lan_devices  (same SQLite as the fleet)
                 │
        REST · dashboard · MCP tools
```

Answers are cached for their real TTL (clamped 15 s – 1 h), so repeat lookups
are served locally — that is why browsing feels *faster*, not slower. Failures
fall through to the second upstream, then return SERVFAIL rather than hanging.

Device naming comes from ARP (MAC → vendor via OUI) plus a best-effort reverse
lookup. Modern phones randomise their MAC, so some will read
"randomised MAC (privacy)" — that is the device protecting itself, not a bug.
Use `label` on `lan_devices` to name those by hand once you identify them.

## Turning it on for the whole LAN

The resolver is already listening. Until the router hands out its address,
only traffic aimed at it explicitly gets logged.

**On the Afrihost router at `http://192.168.2.1`:**

1. Log in to the router admin.
2. Find **LAN → DHCP Server** (sometimes *Network → LAN*, or *Advanced → DHCP*).
3. Set the **Primary DNS** handed to clients to **`192.168.0.3`**.
4. Leave Secondary DNS **blank** if you want complete visibility. If you set a
   secondary, devices will silently use it whenever it answers first and you
   will see only part of the picture.
5. Save, then renew leases (`ipconfig /renew`, or just reboot the router).

Verify from any machine on the LAN:

```powershell
nslookup ionity.today 192.168.0.3     # should answer
Resolve-DnsName ionity.today            # then check it appears in the dashboard
```

### Two things to know before you flip it

- **This host becomes the LAN's DNS.** If the fleet server is off, name
  resolution stops for every device pointed at it. Set the service to start on
  boot before you rely on it, or keep a secondary DNS and accept partial data.
- **DNS-over-HTTPS bypasses you entirely.** Chrome, Firefox and iOS default to
  DoH in places, which tunnels DNS to the browser's own resolver over port 443.
  Those queries will not appear. To close that gap you have to block outbound
  DoH at the router, which is a separate decision.

## Configuration

| `.env` key | Default | Note |
|---|---|---|
| `IONITY_DNS_ENABLED` | `true` | Set false to disable the resolver entirely |
| `IONITY_DNS_BIND` | `0.0.0.0` | Listen address |
| `IONITY_DNS_PORT` | `53` | Use `5353` to test without touching the real DNS path |
| `IONITY_DNS_UPSTREAMS` | `1.1.1.1,8.8.8.8` | Tried in order |
| `IONITY_DNS_TIMEOUT_S` | `3.0` | Per-upstream timeout |
| `IONITY_DNS_RETENTION_DAYS` | `14` | Hourly pruner enforces this |

Windows does not reserve ports below 1024, so port 53 binds without elevation —
verified on this host.

## Deliberately not built (yet)

The schema carries a `blocked` column from day one, so turning this into a
filtering resolver — a Pi-hole, effectively — needs no migration. It is not
enabled, because blocking ads and trackers for a whole household or office is
a decision with real consequences when something legitimate breaks. Say the
word and it's a blocklist loader plus three lines in `handle()`.

---

*Governance: Policy 986 AED · © 2018-2026 Antwerp Designs | Ionity (Pty) Ltd*
