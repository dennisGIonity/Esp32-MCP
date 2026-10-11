/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | prov/ProvHandler.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
#include "prov/ProvHandler.h"

#include <math.h>
#include <string.h>

#include "cJSON.h"
#include "ewp/crc32.h"

namespace prov {
namespace {

constexpr size_t kPrefixLen = 12;  // strlen("IONITY-PROV ")
constexpr size_t kSuffixLen = 11;  // " *xxxxxxxx\n"
constexpr int kMaxValues = 64;     // a full prov/2 set has ~20
constexpr int kMaxDepth = 4;       // {"pins":{...}} is the deepest legal request
constexpr uint32_t kTestDefaultMs = 15000;
constexpr uint32_t kTestMaxMs = 30000;
constexpr uint32_t kTestMinMs = 1000;
constexpr const char* kProduct = "ionity-esp32-mcp-node";

/** Deletes a cJSON tree after zeroing every string in it (secrets must not linger in freed heap). */
struct JsonTree {
    cJSON* root;
    ~JsonTree() {
        wipe(root);
        cJSON_Delete(root);
    }
    static void wipe(cJSON* n) {
        for (; n != nullptr; n = n->next) {
            if (cJSON_IsString(n) && n->valuestring != nullptr) {
                volatile char* p = n->valuestring;
                while (*p) *p++ = 0;
            }
            if (n->child != nullptr) wipe(n->child);
        }
    }
};

bool isEnvelope(const char* k) {
    return strcmp(k, "ionity") == 0 || strcmp(k, "op") == 0 || strcmp(k, "v") == 0 || strcmp(k, "id") == 0;
}

/** A JSON "\u0000" would silently cut a cJSON string short - refuse it outright. */
bool containsEscapedNul(const char* s, size_t n) {
    for (size_t i = 0; i + 5 < n; ++i) {
        if (s[i] == '\\' && s[i + 1] == 'u' && s[i + 2] == '0' && s[i + 3] == '0' && s[i + 4] == '0' && s[i + 5] == '0') {
            return true;
        }
    }
    return false;
}

bool onlyWhitespace(const char* p, const char* end) {
    for (; p < end; ++p) {
        if (*p != ' ' && *p != '\t' && *p != '\r' && *p != '\n') return false;
    }
    return true;
}

}  // namespace

bool ProvHandler::begin(ConfigStore& cfg, ProvBackend& backend, const BoardStatic& board, WriteFn write,
                        void* writeUser) {
    cfg_ = &cfg;
    backend_ = &backend;
    board_ = board;
    write_ = write;
    writeUser_ = writeUser;
    return mu_.begin(false);
}

bool ProvHandler::busy() const {
    os::Lock lk(mu_);
    return pending_ != LongOp::None;
}

/* ---- reply plumbing (mu_ held) -------------------------------------------- */

JsonWriter& ProvHandler::begin(bool ok, const char* op, const Ctx* ctx) {
    // [IONITY-PROV ][{...json...}][ *crc32hex\n]  - one buffer, one write.
    memcpy(out_, kPrefix, kPrefixLen);
    w_.reset(out_ + kPrefixLen, sizeof out_ - kPrefixLen - kSuffixLen);
    w_.beginObject();
    w_.kvBool("ok", ok);
    w_.kv("op", op);
    if (ctx != nullptr && ctx->hasId) w_.kvNum("id", ctx->id);
    return w_;
}

void ProvHandler::send() {
    w_.endObject();
    if (!w_.ok()) {
        // Programming error (a reply outgrew kReplyMax): say so instead of sending broken JSON.
        w_.reset(out_ + kPrefixLen, sizeof out_ - kPrefixLen - kSuffixLen);
        w_.beginObject();
        w_.kvBool("ok", false);
        w_.kv("op", "error");
        w_.kv("error", "reply too large");
        w_.endObject();
    }
    const size_t jsonLen = w_.size();
    char hex[9];
    ewp::crc32_hex(ewp::crc32(out_ + kPrefixLen, jsonLen), hex);
    char* tail = out_ + kPrefixLen + jsonLen;
    tail[0] = ' ';
    tail[1] = '*';
    memcpy(tail + 2, hex, 8);
    tail[10] = '\n';
    write_(writeUser_, out_, kPrefixLen + jsonLen + kSuffixLen);
}

void ProvHandler::sendError(const char* op, const Ctx* ctx, const char* error, const char* field) {
    begin(false, op, ctx);
    w_.kv("error", error);
    if (field != nullptr) w_.kv("field", field);
    send();
}

void ProvHandler::writeInfo(const NodeConfig& c, const LiveState& live, int mqttOverride) {
    w_.kvNum("prov", 2);
    w_.kvNum("line_max", static_cast<int64_t>(kLineMax));
    w_.key("set_fields");
    w_.beginArray();
    for (size_t i = 0; i < static_cast<size_t>(Field::Count); ++i) {
        const FieldInfo& f = fieldInfo(static_cast<Field>(i));
        if (f.advertised) w_.str(f.wire);
    }
    w_.endArray();
    w_.kv("hwid", board_.hwid);
    w_.kv("device_id", board_.hwid);  // fw 2.x name, for flasher 2.0 / provision.py
    w_.kv("fw", board_.fw);
    w_.kv("product", kProduct);
    w_.kv("chip", board_.chip);
    w_.kvNum("chip_rev", board_.chipRev);
    w_.kvNum("flash_mb", board_.flashMb);
    w_.kv("mac", board_.mac);
    w_.kvBool("provisioned", c.provisioned());
    w_.kv("ssid", c.ssid);
    // Secrets: presence only (obligation 7).
    w_.kvBool("has_pass", c.pass[0] != '\0');
    w_.kv("host_url", c.hostUrl);
    w_.kv("device_uuid", c.deviceUuid);
    w_.kvBool("has_ca", c.hasCa);
    w_.kvBool("has_mqtt_pass", c.mqttPass[0] != '\0');
    w_.kvBool("has_mcp_token", c.mcpToken[0] != '\0');
    w_.kvBool("has_fleet_token", c.fleetToken[0] != '\0');
    w_.kvBool("has_ota_pass", c.otaPass[0] != '\0');
    w_.kv("role", c.role);
    w_.kv("site", c.site);
    w_.kv("group", c.group);
    w_.kv("label", c.label);
    w_.kv("mode", modeName(live.mode));
    w_.kv("wifi", live.wifiUp ? "connected" : "down");
    if (!live.wifiUp && live.wifiError[0] != '\0') w_.kv("wifi_error", live.wifiError);
    if (live.wifiUp) {
        w_.kv("ip", live.ip);
        w_.kvNum("rssi", live.rssi);
    }
    w_.kvBool("mqtt", mqttOverride >= 0 ? mqttOverride != 0 : live.mqttUp);
    if (strncmp(c.hostUrl, "mqtt://", 7) == 0) {  // fw 2.x tools display server + port
        w_.kv("server", c.server);
        w_.kvNum("mqtt_port", c.mqttPort);
    }
}

/* ---- entry points ------------------------------------------------------- */

void ProvHandler::announce() {
    LiveState live;
    backend_->live(live);
    os::Lock lk(mu_);
    cfg_->snapshot(snap_);
    begin(true, "hello", nullptr);
    writeInfo(snap_, live);
    send();
}

void ProvHandler::onOverlong() {
    os::Lock lk(mu_);
    sendError("error", nullptr, "line too long");
}

void ProvHandler::onLine(const char* line, size_t len) {
    if (len == 0 || line[0] != '{') return;  // echo, typing, log noise: not for us
    if (len > kLineMax) {
        onOverlong();
        return;
    }
    // CRC suffix " *xxxxxxxx" (8 lowercase hex). Present -> verify; mismatch -> error, no action.
    size_t textLen = len;
    uint32_t want = 0;
    if (len >= 12 && line[len - 10] == ' ' && line[len - 9] == '*' && ewp::crc32_parse_hex(line + len - 8, &want)) {
        textLen = len - 10;
        if (ewp::crc32(line, textLen) != want) {
            os::Lock lk(mu_);
            sendError("error", nullptr, "crc mismatch");
            return;
        }
    }
    if (!jsonShapeOk(line, textLen, kMaxValues, kMaxDepth)) {
        os::Lock lk(mu_);
        sendError("error", nullptr, "request too complex");
        return;
    }
    const char* end = nullptr;
    JsonTree tree{cJSON_ParseWithLengthOpts(line, textLen, &end, false)};
    if (tree.root == nullptr || !cJSON_IsObject(tree.root)) return;  // not JSON: ordinary input
    if (end == nullptr || !onlyWhitespace(end, line + textLen)) return;  // trailing junk: not a request
    const cJSON* ion = cJSON_GetObjectItemCaseSensitive(tree.root, "ionity");
    if (!cJSON_IsString(ion) || strcmp(ion->valuestring, "prov") != 0) return;

    Ctx ctx;
    const cJSON* v = cJSON_GetObjectItemCaseSensitive(tree.root, "v");
    ctx.v2 = cJSON_IsNumber(v) && v->valuedouble == 2;
    const cJSON* id = cJSON_GetObjectItemCaseSensitive(tree.root, "id");
    if (cJSON_IsNumber(id) && isfinite(id->valuedouble) && floor(id->valuedouble) == id->valuedouble &&
        fabs(id->valuedouble) <= 9007199254740991.0) {
        ctx.hasId = true;
        ctx.id = static_cast<int64_t>(id->valuedouble);
    }
    const cJSON* opItem = cJSON_GetObjectItemCaseSensitive(tree.root, "op");
    const char* op = cJSON_IsString(opItem) ? opItem->valuestring : "";
    if (containsEscapedNul(line, textLen)) {
        os::Lock lk(mu_);
        sendError(op, &ctx, "control characters are not allowed");
        return;
    }
    dispatch(tree.root, op, ctx);
}

void ProvHandler::dispatch(const cJSON* req, const char* op, const Ctx& ctx) {
    if (strcmp(op, "hello") == 0 || strcmp(op, "get") == 0) {
        LiveState live;
        backend_->live(live);
        os::Lock lk(mu_);
        cfg_->snapshot(snap_);
        begin(true, op, &ctx);
        writeInfo(snap_, live);
        send();
    } else if (strcmp(op, "set") == 0) {
        handleSet(req, ctx);
    } else if (strcmp(op, "scan") == 0) {
        startLong(LongOp::Scan, "scan", ctx, 0);
    } else if (strcmp(op, "test") == 0) {
        uint32_t t = kTestDefaultMs;
        const cJSON* to = cJSON_GetObjectItemCaseSensitive(req, "timeout_ms");
        if (cJSON_IsNumber(to) && isfinite(to->valuedouble)) {
            const double d = to->valuedouble;
            t = d < kTestMinMs ? kTestMinMs : d > kTestMaxMs ? kTestMaxMs : static_cast<uint32_t>(d);
        }
        startLong(LongOp::Test, "test", ctx, t);
    } else if (strcmp(op, "reboot") == 0) {
        {
            os::Lock lk(mu_);
            begin(true, "reboot", &ctx);
            send();
        }
        backend_->restart();  // flushes the reply first (obligation 9)
    } else if (strcmp(op, "factory_reset") == 0) {
        const bool erased = cfg_->eraseAll();
        const bool wifiReset = erased && backend_->factoryResetWifi();
        {
            os::Lock lk(mu_);
            if (!erased || !wifiReset) {
                sendError("factory_reset", &ctx, "could not erase the settings");
                return;
            }
            begin(true, "factory_reset", &ctx);
            send();
        }
        backend_->restart();
    } else {
        os::Lock lk(mu_);
        sendError(op, &ctx, "unknown op");
    }
}

void ProvHandler::handleSet(const cJSON* req, const Ctx& ctx) {
    SetItem items[static_cast<size_t>(Field::Count)];
    size_t n = 0;
    uint32_t seen = 0;
    for (const cJSON* it = req->child; it != nullptr; it = it->next) {
        const char* k = it->string != nullptr ? it->string : "";
        if (isEnvelope(k)) continue;
        const Field f = fieldByName(k, strlen(k));
        const bool known = f != Field::Count && (!ctx.v2 || fieldInfo(f).advertised);
        if (!known) {
            if (ctx.v2) {  // prov/2: an unknown field is an error and nothing is written (obligation 6)
                os::Lock lk(mu_);
                sendError("set", &ctx, "unknown field", k);
                return;
            }
            continue;  // prov/1: fw 2.x ignored what it did not know
        }
        if (seen & fieldBit(f)) {
            os::Lock lk(mu_);
            sendError("set", &ctx, "duplicate field", k);
            return;
        }
        seen |= fieldBit(f);
        SetItem& si = items[n++];
        si = SetItem{};
        si.field = f;
        if (cJSON_IsString(it)) {
            si.isString = true;
            si.str = it->valuestring;
            si.len = strlen(it->valuestring);
        } else if (cJSON_IsNumber(it)) {
            si.isNumber = true;
            si.number = it->valuedouble;
        } else if (f == Field::Pins && cJSON_IsObject(it)) {
            static const char* const kPinNames[3] = {"pwm0", "pwm1", "relay0"};
            for (int p = 0; p < 3; ++p) {
                const cJSON* pin = cJSON_GetObjectItemCaseSensitive(it, kPinNames[p]);
                if (pin == nullptr) continue;
                if (!cJSON_IsNumber(pin) || floor(pin->valuedouble) != pin->valuedouble || fabs(pin->valuedouble) > 1000) {
                    os::Lock lk(mu_);
                    sendError("set", &ctx, "pins must be GPIO numbers (-1 = unused)", "pins");
                    return;
                }
                si.pins[p] = static_cast<int32_t>(pin->valuedouble);
            }
        } else if (f == Field::Pins) {
            os::Lock lk(mu_);
            sendError("set", &ctx, "pins must be an object {pwm0, pwm1, relay0}", "pins");
            return;
        }
    }

    const SetResult r = cfg_->apply(items, n);
    LiveState live;
    backend_->live(live);
    {
        os::Lock lk(mu_);
        if (!r.ok) {
            sendError("set", &ctx, r.error != nullptr ? r.error : "invalid value",
                      r.badField != Field::Count ? fieldInfo(r.badField).wire : nullptr);
            return;
        }
        cfg_->snapshot(snap_);
        begin(true, "set", &ctx);
        w_.key("changed");  // field NAMES only - values may be secrets
        w_.beginArray();
        for (size_t i = 0; i < n; ++i) w_.str(fieldInfo(items[i].field).wire);
        w_.endArray();
        w_.kvBool("reboot_required", r.changed != 0);
        writeInfo(snap_, live);
        send();
    }
    if (r.changed != 0) backend_->configChanged(r.changed);
}

void ProvHandler::startLong(LongOp op, const char* opName, const Ctx& ctx, uint32_t timeoutMs) {
    {
        os::Lock lk(mu_);
        if (pending_ != LongOp::None) {
            sendError(opName, &ctx, "busy: a scan or test is already running");
            return;
        }
        if (op == LongOp::Test) {
            cfg_->snapshot(snap_);
            if (!snap_.provisioned()) {
                sendError("test", &ctx, "no ssid provisioned");
                return;
            }
        }
        pending_ = op;
        pendingCtx_ = ctx;
    }
    // Outside the lock: a backend may complete synchronously (host tests) and call finish*().
    const bool started = op == LongOp::Test ? backend_->startTest(timeoutMs) : backend_->startScan();
    if (!started) {
        os::Lock lk(mu_);
        if (pending_ == op) pending_ = LongOp::None;
        sendError(opName, &ctx, "busy: the radio is in use - try again");
    }
}

void ProvHandler::finishScan(const ScanOutcome& r) {
    os::Lock lk(mu_);
    if (pending_ != LongOp::Scan) return;
    const Ctx ctx = pendingCtx_;
    pending_ = LongOp::None;
    begin(r.ok, "scan", &ctx);
    if (!r.ok) w_.kv("error", r.error != nullptr ? r.error : "scan failed (radio busy) - try again");
    w_.key("networks");
    w_.beginArray();
    for (uint8_t i = 0; i < r.count && i < ScanOutcome::kMax; ++i) {
        const ScanOutcome::Net& n = r.nets[i];
        w_.beginObject();
        w_.key("ssid");
        w_.str(n.ssid, n.ssidLen <= 32 ? n.ssidLen : 32);
        w_.kvNum("rssi", n.rssi);
        w_.kvNum("ch", n.ch);
        w_.kvBool("open", n.open);
        w_.endObject();
    }
    w_.endArray();
    send();
}

void ProvHandler::finishTest(const TestOutcome& r) {
    LiveState live;
    backend_->live(live);
    os::Lock lk(mu_);
    if (pending_ != LongOp::Test) return;
    const Ctx ctx = pendingCtx_;
    pending_ = LongOp::None;
    cfg_->snapshot(snap_);

    if (!ctx.v2) {  // prov/1 form (fw 2.x tools): flat
        if (!r.wifiOk) {
            sendError("test", &ctx, r.wifiError[0] ? r.wifiError : "timeout joining WiFi");
            return;
        }
        begin(true, "test", &ctx);
        writeInfo(snap_, live, r.brokerOk ? 1 : 0);  // "mqtt" = the test's broker outcome
        send();
        return;
    }
    const bool ok = r.wifiOk && (!r.brokerReported || r.brokerOk);
    begin(ok, "test", &ctx);
    w_.key("wifi");
    w_.beginObject();
    w_.kvBool("ok", r.wifiOk);
    if (r.wifiOk) {
        w_.kv("ip", r.ip);
        w_.kvNum("rssi", r.rssi);
    } else {
        w_.kv("error", r.wifiError[0] ? r.wifiError : "timeout joining WiFi");
    }
    w_.endObject();
    w_.key("broker");
    w_.beginObject();
    if (r.brokerReported) w_.kvBool("ok", r.brokerOk);
    if (r.brokerDetail[0] != '\0') w_.kv("detail", r.brokerDetail);
    w_.endObject();
    w_.key("board");
    w_.beginObject();
    w_.kvBool("ok", true);
    w_.kv("op", "get");
    writeInfo(snap_, live);
    w_.endObject();
    send();
}

}  // namespace prov
