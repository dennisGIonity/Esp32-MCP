"""
AEDI - IONITY GLOBAL | ewp_protocol.py - Edge Wire Protocol v1 (EWP/1)
Doc ID: DOC-2026-10-ESP32MCP-EWP1 | Policy 986 AED | License AED 900
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd

AUTO-GENERATED from packages/protocol (scripts/gen-py-module.ts). DO NOT EDIT.
Change the TypeScript contract and regenerate; CI rejects a stale copy.
"""
# fmt: off
# ruff: noqa

MAGIC = 229
VERSION = 1
HEADER_LEN = 16
FRAME_FLAGS_KNOWN = 31
RULE_FLAGS_KNOWN = 3
NO_TOOL = 0

# hard limits
LIMITS = {"downlinkFrame": 4096, "uplinkFrame": 8192, "maxInflight": 4, "inlineTensorBytes": 2048, "maxTensorRank": 4, "topK": 8, "actuatorChannels": 16, "models": 8, "customMetrics": 32, "taskStats": 12, "backtraceDepth": 16, "defaultDeadlineMs": 5000, "sleepMinS": 5, "sleepMaxS": 86400, "leaseMaxMs": 86400000, "watchRules": 128, "watchSuffixBytes": 96, "watchCategories": 32, "qnameBytes": 253, "hostnameBytes": 63, "localDevices": 128, "dnsUpstreams": 2, "dnsLogQuery": 32, "pingCountMax": 10, "pingTimeoutMinMs": 100, "pingTimeoutMaxMs": 5000, "pingIntervalMinMs": 200, "pingIntervalMaxMs": 5000}

# topics: ionity/ewp1/<site>/<hwid>/<channel>
TOPIC_ROOT = "ionity/ewp1"
BROADCAST_TOPIC = "ionity/ewp1/_all/dn"
CHANNEL = {"DOWN": "dn", "RESULT": "rs", "TELEMETRY": "tm", "EVENT": "ev", "HELLO": "hello", "STATUS": "st"}
CHANNEL_POLICY = {"dn": {"dir": "down", "qos": 1, "retain": False, "msgTypes": [1, 5]}, "rs": {"dir": "up", "qos": 1, "retain": False, "msgTypes": [2, 3, 4]}, "tm": {"dir": "up", "qos": 0, "retain": False, "msgTypes": [16]}, "ev": {"dir": "up", "qos": 1, "retain": False, "msgTypes": [17, 18, 19]}, "hello": {"dir": "up", "qos": 1, "retain": True, "msgTypes": [32]}, "st": {"dir": "up", "qos": 1, "retain": True, "msgTypes": [33]}}

# MSG
MSG_REQ = 1
MSG_ACK = 2
MSG_RES = 3
MSG_ERR = 4
MSG_CANCEL = 5
MSG_TELEMETRY = 16
MSG_EVENT = 17
MSG_LOG = 18
MSG_CRASH = 19
MSG_HELLO = 32
MSG_STATUS = 33
MSG_NAMES = {"1": "REQ", "2": "ACK", "3": "RES", "4": "ERR", "5": "CANCEL", "16": "TELEMETRY", "17": "EVENT", "18": "LOG", "19": "CRASH", "32": "HELLO", "33": "STATUS"}

# FLAG
FLAG_ACK_REQUESTED = 1
FLAG_BROADCAST = 2
FLAG_PRIORITY = 4
FLAG_BUFFERED = 8
FLAG_TRUNCATED = 16
FLAG_NAMES = {"1": "ACK_REQUESTED", "2": "BROADCAST", "4": "PRIORITY", "8": "BUFFERED", "16": "TRUNCATED"}

# TOOL
TOOL_GET_DEVICE_INFO = 1
TOOL_READ_TELEMETRY = 2
TOOL_RUN_INFERENCE = 3
TOOL_SET_ACTUATOR = 4
TOOL_SET_STATE_MODE = 5
TOOL_IDENTIFY = 6
TOOL_SET_WATCHLIST = 64
TOOL_GET_WATCHLIST = 65
TOOL_GET_NETWORK_STATS = 66
TOOL_GET_LOCAL_DEVICES = 67
TOOL_QUERY_DNS_LOG = 68
TOOL_SCAN_LAN = 69
TOOL_FLUSH_DNS_CACHE = 70
TOOL_PING_HOST = 71
TOOL_REBOOT = 240
TOOL_NAMES = {"1": "GET_DEVICE_INFO", "2": "READ_TELEMETRY", "3": "RUN_INFERENCE", "4": "SET_ACTUATOR", "5": "SET_STATE_MODE", "6": "IDENTIFY", "64": "SET_WATCHLIST", "65": "GET_WATCHLIST", "66": "GET_NETWORK_STATS", "67": "GET_LOCAL_DEVICES", "68": "QUERY_DNS_LOG", "69": "SCAN_LAN", "70": "FLUSH_DNS_CACHE", "71": "PING_HOST", "240": "REBOOT"}

# TOOL_FLAG
TOOL_FLAG_READ_ONLY = 1
TOOL_FLAG_DESTRUCTIVE = 2
TOOL_FLAG_LONG_RUNNING = 4
TOOL_FLAG_IDEMPOTENT = 8
TOOL_FLAG_NAMES = {"1": "READ_ONLY", "2": "DESTRUCTIVE", "4": "LONG_RUNNING", "8": "IDEMPOTENT"}

# ST
ST_OK = 0
ST_ACCEPTED = 1
ST_BAD_FRAME = 16
ST_UNSUPPORTED_VERSION = 17
ST_UNKNOWN_TOOL = 18
ST_INVALID_ARGS = 19
ST_TOO_LARGE = 20
ST_DUPLICATE = 21
ST_BUSY = 32
ST_DEADLINE_EXCEEDED = 33
ST_CANCELLED = 34
ST_FAILSAFE_LOCKED = 35
ST_NOT_READY = 36
ST_UNAUTHORIZED = 37
ST_RATE_LIMITED = 38
ST_TOOL_FAULT = 48
ST_OUT_OF_MEMORY = 49
ST_HW_FAULT = 50
ST_MODEL_ERROR = 51
ST_INTERNAL = 63
ST_NAMES = {"0": "OK", "1": "ACCEPTED", "16": "BAD_FRAME", "17": "UNSUPPORTED_VERSION", "18": "UNKNOWN_TOOL", "19": "INVALID_ARGS", "20": "TOO_LARGE", "21": "DUPLICATE", "32": "BUSY", "33": "DEADLINE_EXCEEDED", "34": "CANCELLED", "35": "FAILSAFE_LOCKED", "36": "NOT_READY", "37": "UNAUTHORIZED", "38": "RATE_LIMITED", "48": "TOOL_FAULT", "49": "OUT_OF_MEMORY", "50": "HW_FAULT", "51": "MODEL_ERROR", "63": "INTERNAL"}

# MODE
MODE_ACTIVE = 0
MODE_STANDBY = 1
MODE_INFERENCE_ACTIVE = 2
MODE_LOW_POWER_SLEEP = 3
MODE_FAILSAFE = 4
MODE_NAMES = {"0": "ACTIVE", "1": "STANDBY", "2": "INFERENCE_ACTIVE", "3": "LOW_POWER_SLEEP", "4": "FAILSAFE"}

# ACT
ACT_PWM = 1
ACT_RELAY = 2
ACT_GPIO = 3
ACT_SDM = 4
ACT_DAC = 5
ACT_NAMES = {"1": "PWM", "2": "RELAY", "3": "GPIO", "4": "SDM", "5": "DAC"}

# INPUT
INPUT_LIVE = 0
INPUT_INLINE = 1
INPUT_BLOB = 2
INPUT_NAMES = {"0": "LIVE", "1": "INLINE", "2": "BLOB"}

# DTYPE
DTYPE_I8 = 0
DTYPE_U8 = 1
DTYPE_I16 = 2
DTYPE_F32 = 3
DTYPE_NAMES = {"0": "I8", "1": "U8", "2": "I16", "3": "F32"}

# MODEL
MODEL_CLASSIFIER = 1
MODEL_ANOMALY = 2
MODEL_REGRESSOR = 3
MODEL_STATISTICAL = 4
MODEL_NAMES = {"1": "CLASSIFIER", "2": "ANOMALY", "3": "REGRESSOR", "4": "STATISTICAL"}

# LOG
LOG_ERROR = 1
LOG_WARN = 2
LOG_INFO = 3
LOG_DEBUG = 4
LOG_VERBOSE = 5
LOG_NAMES = {"1": "ERROR", "2": "WARN", "3": "INFO", "4": "DEBUG", "5": "VERBOSE"}

# RESET
RESET_UNKNOWN = 0
RESET_POWER_ON = 1
RESET_EXTERNAL = 2
RESET_SOFTWARE = 3
RESET_PANIC = 4
RESET_INT_WDT = 5
RESET_TASK_WDT = 6
RESET_OTHER_WDT = 7
RESET_DEEP_SLEEP = 8
RESET_BROWNOUT = 9
RESET_USB = 10
RESET_JTAG = 11
RESET_EFUSE = 12
RESET_POWER_GLITCH = 13
RESET_CPU_LOCKUP = 14
RESET_OTA_ROLLBACK = 15
RESET_NAMES = {"0": "UNKNOWN", "1": "POWER_ON", "2": "EXTERNAL", "3": "SOFTWARE", "4": "PANIC", "5": "INT_WDT", "6": "TASK_WDT", "7": "OTHER_WDT", "8": "DEEP_SLEEP", "9": "BROWNOUT", "10": "USB", "11": "JTAG", "12": "EFUSE", "13": "POWER_GLITCH", "14": "CPU_LOCKUP", "15": "OTA_ROLLBACK"}

# CHIP
CHIP_ESP32 = 1
CHIP_ESP32_S2 = 2
CHIP_ESP32_S3 = 3
CHIP_ESP32_C3 = 4
CHIP_ESP32_C6 = 5
CHIP_ESP32_C5 = 6
CHIP_ESP32_H2 = 7
CHIP_ESP32_P4 = 8
CHIP_ESP32_C2 = 9
CHIP_ESP32_C61 = 10
CHIP_OTHER = 255
CHIP_NAMES = {"1": "ESP32", "2": "ESP32_S2", "3": "ESP32_S3", "4": "ESP32_C3", "5": "ESP32_C6", "6": "ESP32_C5", "7": "ESP32_H2", "8": "ESP32_P4", "9": "ESP32_C2", "10": "ESP32_C61", "255": "OTHER"}

# STATE
STATE_OFFLINE = 0
STATE_ONLINE = 1
STATE_SLEEPING = 2
STATE_UPDATING = 3
STATE_REBOOTING = 4
STATE_NAMES = {"0": "OFFLINE", "1": "ONLINE", "2": "SLEEPING", "3": "UPDATING", "4": "REBOOTING"}

# EVENT
EVENT_INFERENCE_POSITIVE = 1
EVENT_MODE_CHANGED = 2
EVENT_LEASE_EXPIRED = 3
EVENT_FAILSAFE_TRIPPED = 4
EVENT_THRESHOLD = 5
EVENT_DNS_BATCH = 6
EVENT_DNS_FLAGGED = 7
EVENT_NAMES = {"1": "INFERENCE_POSITIVE", "2": "MODE_CHANGED", "3": "LEASE_EXPIRED", "4": "FAILSAFE_TRIPPED", "5": "THRESHOLD", "6": "DNS_BATCH", "7": "DNS_FLAGGED"}

# TM
TM_HEAP = 1
TM_NET = 2
TM_CPU = 4
TM_TASKS = 8
TM_SENSORS = 16
TM_ACTUATORS = 32
TM_INFERENCE = 64
TM_EXT = 128
TM_NAMES = {"1": "HEAP", "2": "NET", "4": "CPU", "8": "TASKS", "16": "SENSORS", "32": "ACTUATORS", "64": "INFERENCE", "128": "EXT"}

# DNS_MODE
DNS_MODE_OFF = 0
DNS_MODE_FORWARD = 1
DNS_MODE_NAMES = {"0": "OFF", "1": "FORWARD"}

# DNSQ
DNSQ_CACHED = 1
DNSQ_FLAGGED = 2
DNSQ_UPSTREAM_FAILED = 4
DNSQ_TRUNCATED = 8
DNSQ_NAMES = {"1": "CACHED", "2": "FLAGGED", "4": "UPSTREAM_FAILED", "8": "TRUNCATED"}

# RULE
RULE_ALERT = 1
RULE_SUBDOMAINS_ONLY = 2
RULE_NAMES = {"1": "ALERT", "2": "SUBDOMAINS_ONLY"}

# WATCH_OP
WATCH_OP_REPLACE = 0
WATCH_OP_APPEND = 1
WATCH_OP_NAMES = {"0": "REPLACE", "1": "APPEND"}

# PING
PING_NONE = 0
PING_RESOLVE_FAILED = 1
PING_TIMEOUT = 2
PING_NOT_PERMITTED = 3
PING_BUSY = 4
PING_NAMES = {"0": "NONE", "1": "RESOLVE_FAILED", "2": "TIMEOUT", "3": "NOT_PERMITTED", "4": "BUSY"}

# DISC
DISC_ARP = 1
DISC_DHCP = 2
DISC_MDNS = 4
DISC_DNS = 8
DISC_NAMES = {"1": "ARP", "2": "DHCP", "4": "MDNS", "8": "DNS"}

# NETCAP
NETCAP_DNS_FORWARD = 1
NETCAP_DNS_TCP = 2
NETCAP_MDNS_LISTEN = 4
NETCAP_DHCP_SNIFF = 8
NETCAP_ARP_SCAN = 16
NETCAP_DNS_CACHE = 32
NETCAP_NAMES = {"1": "DNS_FORWARD", "2": "DNS_TCP", "4": "MDNS_LISTEN", "8": "DHCP_SNIFF", "16": "ARP_SCAN", "32": "DNS_CACHE"}

# LLM-facing tool names by id
TOOL_NAME = {1: "get_device_info", 2: "read_telemetry", 3: "run_inference", 4: "set_actuator", 5: "set_state_mode", 6: "identify", 64: "set_watchlist", 65: "get_watchlist", 66: "get_network_stats", 67: "get_local_devices", 68: "query_dns_log", 69: "scan_lan", 70: "flush_dns_cache", 71: "ping_host", 240: "reboot"}
TOOL_ID = {v: k for k, v in TOOL_NAME.items()}
RETRYABLE_STATUS = frozenset({32, 33, 36, 38})

# CBOR map keys per body, field name -> integer key (all 0..23)
KEYS = {
    "AckBody": {"queueDepth": 1, "etaMs": 2},
    "ActuatorDescriptor": {"ch": 1, "name": 2, "kind": 3, "gpio": 4, "safe": 5, "resBits": 6, "hazardous": 7},
    "CancelBody": {"reason": 1},
    "ChipInfo": {"model": 1, "rev": 2, "cores": 3, "flashMb": 4, "psramMb": 5},
    "CrashBody": {"reason": 1, "task": 2, "pc": 3, "backtrace": 4, "btCorrupted": 5, "elfSha": 6, "cause": 7, "vaddr": 8, "msg": 9},
    "DnsBatch": {"seq": 1, "windowMs": 2, "clients": 3, "queries": 4, "dropped": 5, "more": 6},
    "DnsCounters": {"mode": 1, "queries": 2, "forwarded": 3, "cacheHits": 4, "upstreamFail": 5, "dropped": 6, "flagged": 7, "clients": 8, "lanDevices": 9},
    "DnsFlag": {"client": 1, "qname": 2, "qtype": 3, "category": 4, "rule": 5, "ageMs": 6, "watchVer": 7},
    "DnsLogResult": {"clients": 1, "queries": 2},
    "ErrorBody": {"detail": 1, "argKey": 2, "retryAfterMs": 3, "timing": 4},
    "EventBody": {"kind": 1, "rid": 2, "model": 3, "cls": 4, "p": 5, "from": 6, "to": 7, "cause": 8, "channel": 9, "revertedTo": 10, "reason": 11, "metric": 12, "value": 13, "dns": 14, "flag": 15},
    "ExecTiming": {"queueUs": 1, "execUs": 2, "heapDelta": 3},
    "FirmwareInfo": {"version": 1, "elfSha": 2, "idf": 3},
    "FlushDnsCacheResult": {"flushed": 1},
    "GetLocalDevicesArgs": {"limit": 1},
    "GetWatchlistResult": {"version": 1, "rules": 2, "categories": 3},
    "HeapStats": {"internalFree": 1, "internalMinFree": 2, "internalLargest": 3, "psramFree": 4, "psramMinFree": 5, "psramLargest": 6, "dmaFree": 7},
    "HelloBody": {"proto": 1, "hwid": 2, "uuid": 3, "fw": 4, "chip": 5, "limits": 6, "tools": 7, "models": 8, "actuators": 9, "metrics": 10, "tasks": 11, "mode": 12, "site": 13, "group": 14, "label": 15, "resetReason": 16, "bootCount": 17, "net": 18},
    "IdentifyArgs": {"durationMs": 1},
    "InferenceInput": {"source": 1, "windowMs": 2, "dtype": 3, "shape": 4, "data": 5, "blobId": 6, "sha256": 7},
    "InferenceResult": {"model": 1, "modelHash": 2, "classes": 3, "score": 4, "positive": 5, "stages": 6, "arenaPeakBytes": 7},
    "LastInference": {"model": 1, "positive": 2, "p": 3, "ageMs": 4},
    "LinkLimits": {"maxDownlink": 1, "maxUplink": 2, "maxInflight": 3},
    "LocalDevicesResult": {"devices": 1, "total": 2},
    "LogBody": {"level": 1, "tag": 2, "msg": 3, "rid": 4, "dropped": 5},
    "ModelDescriptor": {"id": 1, "name": 2, "hash": 3, "kind": 4, "dtype": 5, "shape": 6, "classes": 7, "labels": 8, "arenaBytes": 9},
    "NetObserveInfo": {"mode": 1, "port": 2, "upstreams": 3, "caps": 4, "watchVer": 5, "watchRules": 6},
    "NetStats": {"rssiDbm": 1, "channel": 2, "txFail": 3, "reconnects": 4, "uplinkQueued": 5},
    "NetworkStatsResult": {"dns": 1, "upstreams": 2, "cacheEntries": 3, "pending": 4, "lastClientQueryAgoMs": 5},
    "PingHostArgs": {"target": 1, "count": 2, "timeoutMs": 3, "intervalMs": 4},
    "PingHostResult": {"error": 1, "resolvedIp": 2, "sent": 3, "received": 4, "rttMinMs": 5, "rttAvgMs": 6, "rttMaxMs": 7},
    "QueryDnsLogArgs": {"limit": 1, "client": 2, "flaggedOnly": 3},
    "ReadTelemetryArgs": {"sections": 1},
    "RebootArgs": {"delayMs": 1},
    "RebootResult": {"inMs": 1},
    "ResultBody": {"timing": 1, "result": 2},
    "RunInferenceArgs": {"model": 1, "input": 2, "topK": 3, "threshold": 4},
    "ScanLanResult": {"targets": 1, "etaMs": 2},
    "SensorStats": {"dieTempC": 1, "adcMv": 2},
    "SetActuatorArgs": {"channel": 1, "value": 2, "rampMs": 3, "leaseMs": 4},
    "SetActuatorResult": {"channel": 1, "applied": 2, "previous": 3, "leaseRemainingMs": 4},
    "SetStateModeArgs": {"mode": 1, "model": 2, "periodMs": 3, "durationS": 4, "reason": 5},
    "SetStateModeResult": {"previous": 1, "current": 2, "effectiveInMs": 3, "latched": 4},
    "SetWatchlistArgs": {"op": 1, "version": 2, "rules": 3, "categories": 4},
    "StatusBody": {"state": 1, "mode": 2, "wakeAtS": 3, "ip": 4},
    "TelemetryBody": {"seq": 1, "uptimeS": 2, "unixS": 3, "ageMs": 4, "mode": 5, "heap": 6, "net": 7, "cpu": 8, "tasks": 9, "sensors": 10, "actuators": 11, "inference": 12, "ext": 13, "dns": 14},
    "WatchlistResult": {"version": 1, "rules": 2, "categories": 3},
}
NAMES = {body: {v: k for k, v in t.items()} for body, t in KEYS.items()}

# built-in watchlist (src/watchlist.ts)
DEFAULT_WATCHLIST_VERSION = 1
DEFAULT_CATEGORIES = [{"id": 1, "name": "video_streaming:youtube", "label": "YouTube"}, {"id": 2, "name": "video_streaming:tiktok", "label": "TikTok"}]
DEFAULT_RULES = [{"pattern": "youtube.com", "category": 1, "flags": 1}, {"pattern": "youtu.be", "category": 1, "flags": 1}, {"pattern": "*.googlevideo.com", "category": 1, "flags": 1}, {"pattern": "*.ytimg.com", "category": 1, "flags": 1}, {"pattern": "youtubei.googleapis.com", "category": 1, "flags": 1}, {"pattern": "youtube.googleapis.com", "category": 1, "flags": 1}, {"pattern": "youtube-nocookie.com", "category": 1, "flags": 1}, {"pattern": "youtubekids.com", "category": 1, "flags": 1}, {"pattern": "yt3.ggpht.com", "category": 1, "flags": 1}, {"pattern": "tiktok.com", "category": 2, "flags": 1}, {"pattern": "tiktokv.com", "category": 2, "flags": 1}, {"pattern": "tiktokcdn.com", "category": 2, "flags": 1}, {"pattern": "tiktokcdn-us.com", "category": 2, "flags": 1}]

# header layout: (field, offset, size)
HEADER_LAYOUT = (("magic", 0, 1), ("version", 1, 1), ("type", 2, 1), ("flags", 3, 1), ("rid", 4, 4), ("tool", 8, 1), ("status", 9, 1), ("deadlineMs", 10, 2), ("bodyLen", 12, 2), ("reserved", 14, 2),)
