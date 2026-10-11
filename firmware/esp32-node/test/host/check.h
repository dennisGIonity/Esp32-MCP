/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | test/host/check.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * A dependency-free test harness for the host build (-fno-exceptions, so no
 * gtest). TEST(name) registers a function; CHECK* record a failure and keep
 * going so one run reports every broken assertion.
 */
#pragma once

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include <string>

namespace check {

using TestFn = void (*)();

struct Registry {
    static constexpr int kMax = 512;
    const char* names[kMax];
    TestFn fns[kMax];
    int count = 0;
    int failures = 0;
    const char* current = "";
    static Registry& get() {
        static Registry r;
        return r;
    }
};

struct Registrar {
    Registrar(const char* name, TestFn fn) {
        Registry& r = Registry::get();
        if (r.count < Registry::kMax) {
            r.names[r.count] = name;
            r.fns[r.count] = fn;
            ++r.count;
        }
    }
};

inline void fail(const char* file, int line, const char* what) {
    Registry& r = Registry::get();
    ++r.failures;
    fprintf(stderr, "  FAIL %s:%d [%s] %s\n", file, line, r.current, what);
}

inline int runAll(const char* filter) {
    Registry& r = Registry::get();
    int ran = 0;
    for (int i = 0; i < r.count; ++i) {
        if (filter && !strstr(r.names[i], filter)) continue;
        r.current = r.names[i];
        const int before = r.failures;
        r.fns[i]();
        ++ran;
        printf("%s %s\n", r.failures == before ? "ok  " : "FAIL", r.names[i]);
    }
    printf("\n%d tests, %d failed assertions\n", ran, r.failures);
    return r.failures == 0 ? 0 : 1;
}

}  // namespace check

#define TEST(name)                                                        \
    static void name();                                                   \
    static const ::check::Registrar name##_registrar(#name, &name);       \
    static void name()

#define CHECK(cond)                                                       \
    do {                                                                  \
        if (!(cond)) ::check::fail(__FILE__, __LINE__, #cond);            \
    } while (0)

#define CHECK_EQ(a, b)                                                                              \
    do {                                                                                            \
        const auto check_a_ = (a);                                                                  \
        const auto check_b_ = (b);                                                                  \
        if (!(check_a_ == check_b_)) {                                                              \
            char check_msg_[256];                                                                   \
            snprintf(check_msg_, sizeof check_msg_, "%s == %s (%lld vs %lld)", #a, #b,              \
                     static_cast<long long>(check_a_), static_cast<long long>(check_b_));           \
            ::check::fail(__FILE__, __LINE__, check_msg_);                                          \
        }                                                                                           \
    } while (0)

namespace check {
// Copies into a std::string while the argument (often a temporary's c_str()) is still alive.
inline std::string str(const char* s) { return s ? std::string(s) : std::string("(null)"); }
inline std::string str(const std::string& s) { return s; }
}  // namespace check

#define CHECK_STR(a, b)                                                                             \
    do {                                                                                            \
        const std::string check_a_ = ::check::str(a);                                               \
        const std::string check_b_ = ::check::str(b);                                               \
        if (check_a_ != check_b_) {                                                                 \
            char check_msg_[1024];                                                                  \
            snprintf(check_msg_, sizeof check_msg_, "%s == %s (\"%.200s\" vs \"%.200s\")", #a, #b,  \
                     check_a_.c_str(), check_b_.c_str());                                           \
            ::check::fail(__FILE__, __LINE__, check_msg_);                                          \
        }                                                                                           \
    } while (0)

/** Compare a byte buffer with a hex string ("e5 01 ..."; spaces ignored). */
inline bool bytesEqualHex(const uint8_t* p, size_t n, const char* hex) {
    size_t i = 0;
    for (const char* h = hex; *h;) {
        if (*h == ' ') {
            ++h;
            continue;
        }
        unsigned v;
        if (sscanf(h, "%2x", &v) != 1) return false;
        if (i >= n || p[i] != v) return false;
        ++i;
        h += 2;
    }
    return i == n;
}

/** Parse a hex string into `out` (spaces ignored). Returns the byte count. */
inline size_t hexToBytes(const char* hex, uint8_t* out, size_t cap) {
    size_t i = 0;
    for (const char* h = hex; *h && i < cap;) {
        if (*h == ' ') {
            ++h;
            continue;
        }
        unsigned v;
        if (sscanf(h, "%2x", &v) != 1) break;
        out[i++] = static_cast<uint8_t>(v);
        h += 2;
    }
    return i;
}

#define CHECK_HEX(buf, len, hex)                                                  \
    do {                                                                          \
        if (!bytesEqualHex((buf), (len), (hex))) {                                \
            char check_msg_[512];                                                 \
            int o_ = snprintf(check_msg_, sizeof check_msg_, "bytes != %s; got ", hex); \
            for (size_t k_ = 0; k_ < (len) && o_ < 480; ++k_)                     \
                o_ += snprintf(check_msg_ + o_, sizeof check_msg_ - o_, "%02x ", (buf)[k_]); \
            ::check::fail(__FILE__, __LINE__, check_msg_);                        \
        }                                                                         \
    } while (0)
