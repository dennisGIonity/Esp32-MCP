/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | net/MqttLink.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
#include "net/MqttLink.h"

#include <stdio.h>
#include <string.h>

#include "esp_crt_bundle.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "ewp/frame.h"
#include "mqtt5_client.h"
#include "net/WifiManager.h"
#include "tools/Identity.h"

namespace net {
namespace {

const char* const TAG = "mqtt";
constexpr EventBits_t kConnected = BIT0;
constexpr EventBits_t kError = BIT1;
constexpr EventBits_t kNeedPresence = BIT2;

/** Downlink frame limit + room for the PUBLISH fixed header, topic and MQTT 5 properties. */
constexpr int kRxBuffer = EWP_LIMIT_DOWNLINK_FRAME + 512;
/** Our uplink frames are <= 2 KiB (HELLO maxUplink) + topic/properties. */
constexpr int kTxBuffer = 2048 + 512;
/** esp-mqtt outbox cap: bounded heap use while the broker is slow (QoS 1 in flight). */
constexpr uint64_t kOutboxLimit = 16 * 1024;

bool topicIs(const char* topic, int topicLen, const char* want) {
    const size_t n = strlen(want);
    return topicLen >= 0 && static_cast<size_t>(topicLen) == n && memcmp(topic, want, n) == 0;
}

}  // namespace

MqttLink& MqttLink::instance() {
    static MqttLink l;
    return l;
}

bool MqttLink::begin(mcp::MCPServer& server, mcp::Uplink& uplink, tools::EdgeTools& edge) {
    server_ = &server;
    uplink_ = &uplink;
    edge_ = &edge;
    ev_ = xEventGroupCreate();
    return ev_ != nullptr && clientMu_.begin(false);
}

bool MqttLink::started() const {
    os::Lock lk(clientMu_);
    return client_ != nullptr;
}

const char* MqttLink::topicFor(ewp::Channel ch) const {
    switch (ch) {
        case ewp::Channel::Result: return tRs_;
        case ewp::Channel::Telemetry: return tTm_;
        case ewp::Channel::Event: return tEv_;
        case ewp::Channel::Hello: return tHello_;
        case ewp::Channel::Status: return tSt_;
        default: return nullptr;
    }
}

void MqttLink::stop() {
    os::Lock lk(clientMu_);
    if (client_ == nullptr) return;
    // esp_mqtt_client_stop() waits for the MQTT task to exit; must not run in that task.
    esp_mqtt_client_stop(client_);
    esp_mqtt_client_destroy(client_);
    client_ = nullptr;
    connected_.store(false, std::memory_order_release);
    xEventGroupClearBits(ev_, kConnected | kError | kNeedPresence);
}

bool MqttLink::start(const LinkConfig& cfg) {
    stop();
    os::Lock lk(clientMu_);
    cfg_ = cfg;
    snprintf(tDn_, sizeof tDn_, "%s/%s/%s/%s", EWP_TOPIC_ROOT, cfg_.site, cfg_.hwid, EWP_CH_DOWN);
    snprintf(tRs_, sizeof tRs_, "%s/%s/%s/%s", EWP_TOPIC_ROOT, cfg_.site, cfg_.hwid, EWP_CH_RESULT);
    snprintf(tTm_, sizeof tTm_, "%s/%s/%s/%s", EWP_TOPIC_ROOT, cfg_.site, cfg_.hwid, EWP_CH_TELEMETRY);
    snprintf(tEv_, sizeof tEv_, "%s/%s/%s/%s", EWP_TOPIC_ROOT, cfg_.site, cfg_.hwid, EWP_CH_EVENT);
    snprintf(tHello_, sizeof tHello_, "%s/%s/%s/%s", EWP_TOPIC_ROOT, cfg_.site, cfg_.hwid, EWP_CH_HELLO);
    snprintf(tSt_, sizeof tSt_, "%s/%s/%s/%s", EWP_TOPIC_ROOT, cfg_.site, cfg_.hwid, EWP_CH_STATUS);

    // Last Will = STATUS OFFLINE (binary: the length is explicit, the frame contains NULs).
    willLen_ = tools::buildDeviceFrame(will_, sizeof will_, EWP_MSG_STATUS, 0, [](ewp::CborWriter& w) {
        tools::encodeStatusBody(w, EWP_STATE_OFFLINE, -1, nullptr, 0, 0);
    });

    esp_mqtt_client_config_t mc = {};
    mc.broker.address.uri = cfg_.uri;
    if (strncmp(cfg_.uri, "mqtts://", 8) == 0) {
        if (cfg_.caPem != nullptr && cfg_.caPem[0] != '\0') {
            mc.broker.verification.certificate = cfg_.caPem;  // len 0 = NUL-terminated PEM
        } else {
            mc.broker.verification.crt_bundle_attach = esp_crt_bundle_attach;
        }
    }
    mc.credentials.client_id = cfg_.hwid;
    if (cfg_.user[0] != '\0') mc.credentials.username = cfg_.user;
    if (cfg_.pass[0] != '\0') mc.credentials.authentication.password = cfg_.pass;
    mc.session.protocol_ver = MQTT_PROTOCOL_V_5;
    mc.session.disable_clean_session = false;  // clean start
    mc.session.keepalive = 60;
    mc.session.last_will.topic = tSt_;
    mc.session.last_will.msg = reinterpret_cast<const char*>(will_);
    mc.session.last_will.msg_len = static_cast<int>(willLen_);
    mc.session.last_will.qos = EWP_CH_STATUS_QOS;
    mc.session.last_will.retain = EWP_CH_STATUS_RETAIN;
    mc.network.reconnect_timeout_ms = 5000;
    mc.network.timeout_ms = 10000;
    mc.buffer.size = kRxBuffer;
    mc.buffer.out_size = kTxBuffer;
    mc.outbox.limit = kOutboxLimit;

    client_ = esp_mqtt_client_init(&mc);
    if (client_ == nullptr) {
        ESP_LOGE(TAG, "client init failed (out of memory?)");
        return false;
    }
    esp_mqtt5_connection_property_config_t props = {};
    props.session_expiry_interval = 0;               // nothing is kept for us while offline
    props.maximum_packet_size = kRxBuffer;           // the broker must not send us more than we can hold
    props.receive_maximum = EWP_LIMIT_MAX_INFLIGHT * 2;
    props.request_problem_info = true;
    esp_mqtt5_client_set_connect_property(client_, &props);
    esp_mqtt_client_register_event(client_, MQTT_EVENT_ANY, &MqttLink::onEvent, this);
    errKind_.store(0);
    xEventGroupClearBits(ev_, kConnected | kError | kNeedPresence);
    if (esp_mqtt_client_start(client_) != ESP_OK) {
        esp_mqtt_client_destroy(client_);
        client_ = nullptr;
        return false;
    }
    ESP_LOGI(TAG, "connecting to %s as %s", cfg_.uri, cfg_.hwid);  // never the password
    return true;
}

void MqttLink::onEvent(void* arg, esp_event_base_t, int32_t id, void* data) {
    // MQTT task context (Core 0). Never block here: it is the receive path of every request.
    MqttLink* self = static_cast<MqttLink*>(arg);
    auto* e = static_cast<esp_mqtt_event_handle_t>(data);
    switch (static_cast<esp_mqtt_event_id_t>(id)) {
        case MQTT_EVENT_CONNECTED:
            // Same task: subscribe is safe here (the API lock is recursive).
            esp_mqtt_client_subscribe(e->client, self->tDn_, EWP_CH_DOWN_QOS);
            esp_mqtt_client_subscribe(e->client, EWP_TOPIC_BROADCAST, EWP_CH_DOWN_QOS);
            self->connected_.store(true, std::memory_order_release);
            self->errKind_.store(0);
            self->everConnected_.store(true);
            xEventGroupSetBits(self->ev_, kConnected | kNeedPresence);  // net_task publishes HELLO + STATUS
            ESP_LOGI(TAG, "connected; listening on %s", self->tDn_);
            break;
        case MQTT_EVENT_DISCONNECTED:
            if (self->connected_.exchange(false)) self->reconnects_.fetch_add(1, std::memory_order_relaxed);
            xEventGroupClearBits(self->ev_, kConnected);
            break;
        case MQTT_EVENT_DATA: {
            const ewp::Topic t = topicIs(e->topic, e->topic_len, self->tDn_)               ? ewp::Topic::Unicast
                                 : topicIs(e->topic, e->topic_len, EWP_TOPIC_BROADCAST) ? ewp::Topic::Broadcast
                                                                                         : static_cast<ewp::Topic>(0xFF);
            const bool first = e->current_data_offset == 0;
            const bool whole = first && e->data_len == e->total_data_len;
            if (!whole) {
                // Bigger than our buffer (the broker ignored Maximum Packet Size): answer
                // TOO_LARGE from the header in the first fragment, ignore the rest.
                if (first && static_cast<uint8_t>(t) != 0xFF) {
                    self->server_->onOversized(reinterpret_cast<const uint8_t*>(e->data), static_cast<size_t>(e->data_len), t);
                }
                break;
            }
            if (static_cast<uint8_t>(t) == 0xFF) break;  // not one of our topics (topic is only in the first event)
            self->server_->onDownlink(reinterpret_cast<const uint8_t*>(e->data), static_cast<size_t>(e->data_len), t);
            break;
        }
        case MQTT_EVENT_ERROR:
            if (e->error_handle != nullptr) {
                if (e->error_handle->error_type == MQTT_ERROR_TYPE_CONNECTION_REFUSED) {
                    self->errCode_.store(static_cast<int32_t>(e->error_handle->connect_return_code));
                    self->errKind_.store(2);
                } else {
                    const int32_t code = e->error_handle->esp_transport_sock_errno != 0
                                             ? e->error_handle->esp_transport_sock_errno
                                             : static_cast<int32_t>(e->error_handle->esp_tls_last_esp_err);
                    self->errCode_.store(code);
                    self->errKind_.store(1);
                }
            }
            xEventGroupSetBits(self->ev_, kError);
            break;
        default: break;
    }
}

bool MqttLink::waitConnected(uint32_t timeoutMs, char* detail, size_t cap) {
    const int64_t until = esp_timer_get_time() + static_cast<int64_t>(timeoutMs) * 1000;
    for (;;) {
        if (connected()) {
            snprintf(detail, cap, "connected to %s", cfg_.uri);
            return true;
        }
        const int64_t leftMs = (until - esp_timer_get_time()) / 1000;
        if (leftMs <= 0) break;
        const uint32_t ms = leftMs > 500 ? 500u : static_cast<uint32_t>(leftMs);
        const TickType_t t = pdMS_TO_TICKS(ms) == 0 ? 1 : pdMS_TO_TICKS(ms);
        const EventBits_t b = xEventGroupWaitBits(ev_, kConnected | kError, pdFALSE, pdFALSE, t);
        if (b & kConnected) continue;
        if (b & kError) {
            if (errKind_.load() == 2) break;  // the broker said no: retrying will not help
            xEventGroupClearBits(ev_, kError);
        }
    }
    const uint32_t kind = errKind_.load();
    const int32_t code = errCode_.load();
    if (kind == 2) {
        // MQTT 5 CONNACK reason codes (0x86/0x87) and their MQTT 3.1.1 equivalents (4/5).
        const char* why = (code == 0x86 || code == 4)   ? "bad user name or password"
                          : (code == 0x87 || code == 5) ? "not authorised"
                                                        : "connection refused";
        snprintf(detail, cap, "%s refused the connection: %s (code 0x%02x)", cfg_.uri, why, static_cast<unsigned>(code));
    } else if (kind == 1) {
        snprintf(detail, cap, "cannot reach %s (network/TLS error %d)", cfg_.uri, static_cast<int>(code));
    } else {
        snprintf(detail, cap, "cannot reach %s (no answer)", cfg_.uri);
    }
    return false;
}

bool MqttLink::publishFrame(const uint8_t* frame, size_t len, ewp::Channel ch) {
    const char* topic = topicFor(ch);
    if (client_ == nullptr || topic == nullptr) return false;
    // enqueue = copy into the outbox; the MQTT task does the socket I/O. store=true is
    // required for QoS 0 frames to be accepted by enqueue at all.
    const int id = esp_mqtt_client_enqueue(client_, topic, reinterpret_cast<const char*>(frame), static_cast<int>(len),
                                           ewp::channelQos(ch), ewp::channelRetain(ch) ? 1 : 0, true);
    if (id < 0) {
        txFail_.fetch_add(1, std::memory_order_relaxed);
        return false;
    }
    return true;
}

void MqttLink::publishPresence() {
    // HELLO (retained, capabilities) then STATUS ONLINE (retained, overwrites the Last Will).
    const int slot = uplink_->acquire(200);
    if (slot < 0) {
        xEventGroupSetBits(ev_, kNeedPresence);  // try again on the next loop
        return;
    }
    uint8_t* buf = uplink_->buffer(slot);
    os::Lock lk(clientMu_);
    size_t len = tools::buildDeviceFrame(buf, uplink_->slotSize(), EWP_MSG_HELLO, edge_->nextDeviceRid(),
                                         [this](ewp::CborWriter& w) { edge_->encodeHello(w); });
    bool ok = len > 0 && publishFrame(buf, len, ewp::Channel::Hello);
    uint8_t ip[4];
    const bool hasIp = WifiManager::instance().ipv4(ip);
    len = tools::buildDeviceFrame(buf, uplink_->slotSize(), EWP_MSG_STATUS, edge_->nextDeviceRid(), [&](ewp::CborWriter& w) {
        tools::encodeStatusBody(w, EWP_STATE_ONLINE, edge_->mode(), hasIp ? ip : nullptr, 4, 0);
    });
    ok = ok && len > 0 && publishFrame(buf, len, ewp::Channel::Status);
    uplink_->release(slot);
    if (!ok) xEventGroupSetBits(ev_, kNeedPresence);
}

void MqttLink::announceReboot(uint32_t waitMs) {
    if (!connected()) return;
    uint8_t frame[32];
    const size_t len = tools::buildDeviceFrame(frame, sizeof frame, EWP_MSG_STATUS, edge_->nextDeviceRid(), [this](ewp::CborWriter& w) {
        tools::encodeStatusBody(w, EWP_STATE_REBOOTING, edge_->mode(), nullptr, 0, 0);
    });
    {
        os::Lock lk(clientMu_, 200);
        if (!lk.owns() || len == 0 || !publishFrame(frame, len, ewp::Channel::Status)) return;
    }
    os::delayMs(waitMs);  // the MQTT task sends it from the outbox
}

bool MqttLink::startTask(unsigned priority, int core, uint32_t stackBytes) {
    return os::spawn("net_task", &MqttLink::taskEntry, this, stackBytes, priority, core, nullptr);
}

void MqttLink::taskEntry(void* self) {
    static_cast<MqttLink*>(self)->taskLoop();
    os::exitTask();
}

void MqttLink::taskLoop() {
    os::wdtSubscribe();
    for (;;) {
        os::wdtReset();
        const int64_t t0 = esp_timer_get_time();
        if (supervisor_ != nullptr) supervisor_(supervisorUser_);
        if (connected() && (xEventGroupClearBits(ev_, kNeedPresence) & kNeedPresence)) publishPresence();

        mcp::TxItem it;
        if (!uplink_->nextDue(it, 500)) continue;  // <= 500 ms: the TWDT is fed every loop
        const uint8_t* frame = uplink_->buffer(it.slot);
        {
            os::Lock lk(clientMu_);
            if (!connected()) {
                // Results for a gateway we cannot reach are worthless later (it has timed
                // out and will CANCEL); telemetry is QoS 0 by design. Drop and count.
                droppedOffline_.fetch_add(1, std::memory_order_relaxed);
            } else {
                publishFrame(frame, it.len, static_cast<ewp::Channel>(it.channel));
            }
        }
        uplink_->release(it.slot);
        const int64_t dt = esp_timer_get_time() - t0;
        const uint32_t us = dt > 0xFFFFFFFF ? 0xFFFFFFFFu : static_cast<uint32_t>(dt);
        if (us > runUsMax_.load(std::memory_order_relaxed)) runUsMax_.store(us, std::memory_order_relaxed);
    }
}

}  // namespace net
