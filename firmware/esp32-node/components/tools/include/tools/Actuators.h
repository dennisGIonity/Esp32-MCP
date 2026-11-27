/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | tools/Actuators.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Output channels (ActuatorDescriptor in hello.ts) and the set_actuator (0x04)
 * semantics, independent of the hardware:
 *
 *   ch 0  pwm0    PWM    alert output (fw 2.x red-light channel; NetObserve
 *                        drives it on a flagged ALERT match)
 *   ch 1  pwm1    PWM
 *   ch 2  relay0  RELAY  0 or 1 only
 *
 * Pins come from NVS (pins{pwm0,pwm1,relay0} via provisioning), else the
 * board default from Kconfig; a channel whose pin is -1 (or unsafe for the
 * chip) does not exist. Values are normalised 0..1.
 *
 *  - lease:   unless renewed, the channel reverts to its `safe` value after
 *             leaseMs (an agent that loses its session never leaves a relay on);
 *             a LEASE_EXPIRED event is reported through the expiry callback.
 *  - ramp:    PWM channels move linearly to the target over rampMs.
 *  - pulse:   a local alert overlays full-on for holdMs without changing the
 *             value the host set - when the pulse ends the host's value is back.
 *  - FAILSAFE: every channel at its safe value, set_actuator refused.
 *
 * THREADS: set() runs on the MCP worker, pulse() on the NetObserve task,
 * service() on a periodic timer - all under one mutex. The hardware hook is
 * only called with the mutex held, so it never sees two writers.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "mcp/os.h"
#include "tools/Identity.h"

namespace tools {

/** The pins/LEDC/GPIO behind the channels. */
class ActuatorHw {
public:
    /** Prepare a pin for a channel kind (claimed on first use). False: the channel is unusable. */
    virtual bool configure(uint8_t ch, uint8_t kind, int gpio, uint8_t resBits) = 0;
    /** Drive the pin; returns the value actually applied (quantised to the PWM resolution). */
    virtual float write(uint8_t ch, uint8_t kind, float value) = 0;

protected:
    ~ActuatorHw() = default;
};

class ActuatorBank final : public HelloSection {
public:
    static constexpr size_t kMaxChannels = 3;
    static constexpr uint8_t kPwmResBits = 10;

    struct SetOutcome {
        uint8_t status = EWP_ST_OK;
        const char* detail = nullptr;
        int argKey = -1;
        float applied = 0;
        float previous = 0;
        uint32_t leaseRemainingMs = 0;
    };
    /** LEASE_EXPIRED: channel, value it reverted to. Called with the bank unlocked. */
    using ExpiryFn = void (*)(void* user, uint8_t ch, float revertedTo);

    /** Boot: add channels in order (index = HELLO channel id). gpio < 0 skips the channel. */
    bool addChannel(const char* name, uint8_t kind, int gpio, float safe, bool hazardous);
    /** Boot: after addChannel(); drives every channel to its safe value. */
    bool begin(ActuatorHw& hw, ExpiryFn onExpiry, void* user);

    size_t count() const { return count_; }
    /** Channel index by fw 2.x name ("pwm0"), -1 if absent. */
    int find(const char* name) const;

    SetOutcome set(uint8_t ch, float value, uint32_t rampMs, uint32_t leaseMs, bool failsafe, int64_t nowUs);
    /** Local alert: channel fully on for holdMs (renewed by repeated calls). */
    void pulse(uint8_t ch, uint32_t holdMs, int64_t nowUs);
    /** Every channel to its safe value (entering FAILSAFE). */
    void allSafe(int64_t nowUs);
    /** Leases, ramps and pulses. Call every ~50 ms. */
    void service(int64_t nowUs);

    /** Current (target) values per channel, for TELEMETRY.actuators. */
    uint8_t values(float* out, size_t max) const;

    // HelloSection: HELLO.actuators = ActuatorDescriptor[]
    bool present() const override { return count_ > 0; }
    void encode(ewp::CborWriter& w) const override;

private:
    struct Channel {
        char name[16];
        uint8_t kind;
        int8_t gpio;
        bool hazardous;
        bool ready;
        float safe;
        float value;       // what the host asked for (ramp target)
        float output;      // what is on the pin right now (ramp position / pulse)
        float rampFrom;
        int64_t rampStartUs;
        int64_t rampEndUs;
        int64_t leaseUntilUs;  // 0 = no lease
        int64_t pulseUntilUs;  // 0 = no pulse
    };
    void apply(Channel& c, uint8_t ch, int64_t nowUs);

    mutable os::Mutex mu_;
    Channel ch_[kMaxChannels] = {};
    size_t count_ = 0;
    ActuatorHw* hw_ = nullptr;
    ExpiryFn onExpiry_ = nullptr;
    void* user_ = nullptr;
};

}  // namespace tools
