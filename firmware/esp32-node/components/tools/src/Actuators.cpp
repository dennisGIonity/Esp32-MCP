/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | tools/Actuators.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
#include "tools/Actuators.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

namespace tools {

bool ActuatorBank::addChannel(const char* name, uint8_t kind, int gpio, float safe, bool hazardous) {
    if (gpio < 0 || gpio > 127 || count_ >= kMaxChannels || name == nullptr) return false;
    Channel& c = ch_[count_++];
    c = Channel{};
    snprintf(c.name, sizeof c.name, "%s", name);
    c.kind = kind;
    c.gpio = static_cast<int8_t>(gpio);
    c.hazardous = hazardous;
    c.safe = safe;
    c.value = safe;
    c.output = safe;
    return true;
}

bool ActuatorBank::begin(ActuatorHw& hw, ExpiryFn onExpiry, void* user) {
    if (!mu_.begin(false)) return false;
    hw_ = &hw;
    onExpiry_ = onExpiry;
    user_ = user;
    os::Lock lk(mu_);
    for (size_t i = 0; i < count_; ++i) {
        Channel& c = ch_[i];
        c.ready = hw.configure(static_cast<uint8_t>(i), c.kind, c.gpio, c.kind == EWP_ACT_PWM ? kPwmResBits : 0);
        if (c.ready) c.output = hw.write(static_cast<uint8_t>(i), c.kind, c.safe);
    }
    return true;
}

int ActuatorBank::find(const char* name) const {
    for (size_t i = 0; i < count_; ++i) {
        if (strcmp(ch_[i].name, name) == 0) return static_cast<int>(i);
    }
    return -1;
}

void ActuatorBank::apply(Channel& c, uint8_t ch, int64_t nowUs) {
    float target = c.value;
    if (c.rampEndUs > nowUs && c.rampEndUs > c.rampStartUs) {
        const float t = static_cast<float>(nowUs - c.rampStartUs) / static_cast<float>(c.rampEndUs - c.rampStartUs);
        target = c.rampFrom + (c.value - c.rampFrom) * (t < 0 ? 0 : t > 1 ? 1 : t);
    }
    if (c.pulseUntilUs > nowUs) target = 1.0f;  // the alert overlay wins while it lasts
    if (c.ready && hw_ != nullptr) c.output = hw_->write(ch, c.kind, target);
}

ActuatorBank::SetOutcome ActuatorBank::set(uint8_t ch, float value, uint32_t rampMs, uint32_t leaseMs, bool failsafe,
                                           int64_t nowUs) {
    SetOutcome r;
    if (ch >= count_) {
        r.status = EWP_ST_INVALID_ARGS;
        r.detail = "no such channel - see HELLO.actuators";
        r.argKey = EWP_K_SET_ACTUATOR_ARGS_CHANNEL;
        return r;
    }
    if (!isfinite(value) || value < 0.0f || value > 1.0f) {
        r.status = EWP_ST_INVALID_ARGS;
        r.detail = "value must be 0..1";
        r.argKey = EWP_K_SET_ACTUATOR_ARGS_VALUE;
        return r;
    }
    if (leaseMs > EWP_LIMIT_LEASE_MAX_MS) {
        r.status = EWP_ST_INVALID_ARGS;
        r.detail = "leaseMs is at most one day";
        r.argKey = EWP_K_SET_ACTUATOR_ARGS_LEASE_MS;
        return r;
    }
    os::Lock lk(mu_);
    Channel& c = ch_[ch];
    if (failsafe) {
        r.status = EWP_ST_FAILSAFE_LOCKED;
        r.detail = "FAILSAFE: outputs are locked at their safe values";
        return r;
    }
    if (!c.ready) {
        r.status = EWP_ST_HW_FAULT;
        r.detail = "this channel's pin could not be configured";
        return r;
    }
    if ((c.kind == EWP_ACT_RELAY || c.kind == EWP_ACT_GPIO) && value != 0.0f && value != 1.0f) {
        r.status = EWP_ST_INVALID_ARGS;
        r.detail = "a relay or GPIO channel takes exactly 0 or 1";
        r.argKey = EWP_K_SET_ACTUATOR_ARGS_VALUE;
        return r;
    }
    r.previous = c.output;
    const bool ramp = rampMs > 0 && c.kind == EWP_ACT_PWM;
    c.rampFrom = c.output;
    c.rampStartUs = nowUs;
    c.rampEndUs = ramp ? nowUs + static_cast<int64_t>(rampMs) * 1000 : 0;
    c.value = value;
    c.leaseUntilUs = leaseMs > 0 ? nowUs + static_cast<int64_t>(leaseMs) * 1000 : 0;
    apply(c, ch, nowUs);
    // `applied` is the quantised value the pin will settle at (the target, not a ramp step).
    r.applied = ramp ? value : c.output;
    if (c.pulseUntilUs > nowUs) r.applied = value;
    r.leaseRemainingMs = leaseMs;
    return r;
}

void ActuatorBank::pulse(uint8_t ch, uint32_t holdMs, int64_t nowUs) {
    os::Lock lk(mu_);
    if (ch >= count_) return;
    Channel& c = ch_[ch];
    c.pulseUntilUs = nowUs + static_cast<int64_t>(holdMs) * 1000;
    apply(c, ch, nowUs);
}

void ActuatorBank::allSafe(int64_t nowUs) {
    os::Lock lk(mu_);
    for (size_t i = 0; i < count_; ++i) {
        Channel& c = ch_[i];
        c.value = c.safe;
        c.rampEndUs = 0;
        c.leaseUntilUs = 0;
        c.pulseUntilUs = 0;
        apply(c, static_cast<uint8_t>(i), nowUs);
    }
}

void ActuatorBank::service(int64_t nowUs) {
    uint8_t expired[kMaxChannels];
    float reverted[kMaxChannels];
    size_t nExpired = 0;
    {
        os::Lock lk(mu_);
        for (size_t i = 0; i < count_; ++i) {
            Channel& c = ch_[i];
            bool changed = false;
            if (c.leaseUntilUs != 0 && nowUs >= c.leaseUntilUs) {
                c.leaseUntilUs = 0;
                c.value = c.safe;
                c.rampEndUs = 0;
                expired[nExpired] = static_cast<uint8_t>(i);
                reverted[nExpired++] = c.safe;
                changed = true;
            }
            if (c.pulseUntilUs != 0 && nowUs >= c.pulseUntilUs) {
                c.pulseUntilUs = 0;
                changed = true;
            }
            if (c.rampEndUs != 0) {
                changed = true;
                if (nowUs >= c.rampEndUs) c.rampEndUs = 0;
            }
            if (changed) apply(c, static_cast<uint8_t>(i), nowUs);
        }
    }
    for (size_t i = 0; i < nExpired && onExpiry_ != nullptr; ++i) onExpiry_(user_, expired[i], reverted[i]);
}

uint8_t ActuatorBank::values(float* out, size_t max) const {
    os::Lock lk(mu_);
    size_t n = 0;
    for (; n < count_ && n < max; ++n) out[n] = ch_[n].output;
    return static_cast<uint8_t>(n);
}

void ActuatorBank::encode(ewp::CborWriter& w) const {
    os::Lock lk(mu_);
    w.array(static_cast<uint32_t>(count_));
    for (size_t i = 0; i < count_; ++i) {
        const Channel& c = ch_[i];
        ewp::CborMap m(w);
        m.key(EWP_K_ACTUATOR_DESCRIPTOR_CH).uint(i);
        m.key(EWP_K_ACTUATOR_DESCRIPTOR_NAME).text(c.name);
        m.key(EWP_K_ACTUATOR_DESCRIPTOR_KIND).uint(c.kind);
        m.key(EWP_K_ACTUATOR_DESCRIPTOR_GPIO).uint(static_cast<uint8_t>(c.gpio));
        m.key(EWP_K_ACTUATOR_DESCRIPTOR_SAFE).f32(c.safe);
        if (c.kind == EWP_ACT_PWM) m.key(EWP_K_ACTUATOR_DESCRIPTOR_RES_BITS).uint(kPwmResBits);
        if (c.hazardous) m.key(EWP_K_ACTUATOR_DESCRIPTOR_HAZARDOUS).boolean(true);
    }
}

}  // namespace tools
