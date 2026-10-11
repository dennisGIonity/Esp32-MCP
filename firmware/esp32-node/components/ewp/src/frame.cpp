/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | ewp/frame.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
#include "ewp/frame.h"

namespace ewp {

void encodeHeader(const Header& h, uint8_t* out) {
    out[0] = EWP_MAGIC;
    out[1] = EWP_VERSION;
    out[2] = h.type;
    out[3] = h.flags;
    storeLe32(out + 4, h.rid);
    out[8] = h.tool;
    out[9] = h.status;
    storeLe16(out + 10, h.deadlineMs);
    storeLe16(out + 12, h.bodyLen);
    storeLe16(out + 14, 0);
}

bool decodeHeader(const uint8_t* p, size_t len, RawHeader& out) {
    if (p == nullptr || len < EWP_HEADER_LEN) return false;
    out.magic = p[0];
    out.version = p[1];
    out.h.type = p[2];
    out.h.flags = p[3];
    out.h.rid = loadLe32(p + 4);
    out.h.tool = p[8];
    out.h.status = p[9];
    out.h.deadlineMs = loadLe16(p + 10);
    out.h.bodyLen = loadLe16(p + 12);
    out.reserved = loadLe16(p + 14);
    return true;
}

DownlinkCheck checkDownlink(const uint8_t* p, size_t len, Topic topic, size_t maxFrame) {
    DownlinkCheck c{Verdict::Drop, EWP_ST_BAD_FRAME, "shorter than the 16-byte header", Header{}};
    RawHeader raw;
    if (!decodeHeader(p, len, raw)) return c;  // 1. len >= 16
    if (raw.magic != EWP_MAGIC) {              // 2. magic (legacy JSON starts with '{')
        c.detail = "not an EWP frame (bad magic)";
        return c;
    }
    // From here on the rid is readable, so problems are answered, not dropped.
    c.h = raw.h;
    c.verdict = Verdict::Reject;
    if (raw.version != EWP_VERSION) {  // 3. version
        c.status = EWP_ST_UNSUPPORTED_VERSION;
        c.detail = "unsupported EWP version";
        return c;
    }
    c.status = EWP_ST_BAD_FRAME;
    if ((raw.h.flags & ~EWP_FRAME_FLAGS_KNOWN) != 0) {  // 4. unknown flags are a safety stop
        c.detail = "unknown header flag bits";
        return c;
    }
    if (raw.reserved != 0) {  // 5. reserved
        c.detail = "reserved header field is not zero";
        return c;
    }
    if (static_cast<size_t>(EWP_HEADER_LEN) + raw.h.bodyLen != len) {  // 6. length (both sides < 2^17)
        c.detail = "bodyLen does not match the payload length";
        return c;
    }
    if (len > maxFrame) {  // 7. size limit of this build
        c.status = EWP_ST_TOO_LARGE;
        c.detail = "frame larger than the device downlink limit";
        return c;
    }
    // 8. type allowed on this topic: dn and _all/dn carry REQ and CANCEL only.
    if (raw.h.type != EWP_MSG_REQ && raw.h.type != EWP_MSG_CANCEL) {
        c.detail = "frame type not allowed on the downlink";
        return c;
    }
    if (topic == Topic::Broadcast && (raw.h.flags & EWP_FLAG_BROADCAST) == 0) {
        c.detail = "broadcast topic requires FLAG_BROADCAST";
        return c;
    }
    if (raw.h.status != EWP_ST_OK) {
        c.detail = "downlink status must be OK";
        return c;
    }
    c.verdict = Verdict::Accept;
    c.status = EWP_ST_OK;
    c.detail = "";
    return c;
}

Channel channelOf(uint8_t t) {
    switch (t) {
        case EWP_MSG_ACK:
        case EWP_MSG_RES:
        case EWP_MSG_ERR: return Channel::Result;
        case EWP_MSG_TELEMETRY: return Channel::Telemetry;
        case EWP_MSG_EVENT:
        case EWP_MSG_LOG:
        case EWP_MSG_CRASH: return Channel::Event;
        case EWP_MSG_HELLO: return Channel::Hello;
        case EWP_MSG_STATUS: return Channel::Status;
        default: return Channel::Invalid;
    }
}

const char* channelName(Channel ch) {
    switch (ch) {
        case Channel::Result: return EWP_CH_RESULT;
        case Channel::Telemetry: return EWP_CH_TELEMETRY;
        case Channel::Event: return EWP_CH_EVENT;
        case Channel::Hello: return EWP_CH_HELLO;
        case Channel::Status: return EWP_CH_STATUS;
        default: return nullptr;
    }
}

int channelQos(Channel ch) {
    switch (ch) {
        case Channel::Result: return EWP_CH_RESULT_QOS;
        case Channel::Telemetry: return EWP_CH_TELEMETRY_QOS;
        case Channel::Event: return EWP_CH_EVENT_QOS;
        case Channel::Hello: return EWP_CH_HELLO_QOS;
        case Channel::Status: return EWP_CH_STATUS_QOS;
        default: return 0;
    }
}

bool channelRetain(Channel ch) {
    switch (ch) {
        case Channel::Hello: return EWP_CH_HELLO_RETAIN != 0;
        case Channel::Status: return EWP_CH_STATUS_RETAIN != 0;
        default: return false;
    }
}

const char* statusName(uint8_t s) {
    switch (s) {
        case EWP_ST_OK: return "OK";
        case EWP_ST_ACCEPTED: return "ACCEPTED";
        case EWP_ST_BAD_FRAME: return "BAD_FRAME";
        case EWP_ST_UNSUPPORTED_VERSION: return "UNSUPPORTED_VERSION";
        case EWP_ST_UNKNOWN_TOOL: return "UNKNOWN_TOOL";
        case EWP_ST_INVALID_ARGS: return "INVALID_ARGS";
        case EWP_ST_TOO_LARGE: return "TOO_LARGE";
        case EWP_ST_DUPLICATE: return "DUPLICATE";
        case EWP_ST_BUSY: return "BUSY";
        case EWP_ST_DEADLINE_EXCEEDED: return "DEADLINE_EXCEEDED";
        case EWP_ST_CANCELLED: return "CANCELLED";
        case EWP_ST_FAILSAFE_LOCKED: return "FAILSAFE_LOCKED";
        case EWP_ST_NOT_READY: return "NOT_READY";
        case EWP_ST_UNAUTHORIZED: return "UNAUTHORIZED";
        case EWP_ST_RATE_LIMITED: return "RATE_LIMITED";
        case EWP_ST_TOOL_FAULT: return "TOOL_FAULT";
        case EWP_ST_OUT_OF_MEMORY: return "OUT_OF_MEMORY";
        case EWP_ST_HW_FAULT: return "HW_FAULT";
        case EWP_ST_MODEL_ERROR: return "MODEL_ERROR";
        case EWP_ST_INTERNAL: return "INTERNAL";
        default: return "UNKNOWN_STATUS";
    }
}

}  // namespace ewp
