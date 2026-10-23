/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | netobserve/Watchlist.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
#include "netobserve/Watchlist.h"

#include <string.h>

#include "ewp/cbor.h"  // ewp::utf8_valid

namespace netobs {
namespace {

constexpr uint8_t kMagic[4] = {'I', 'W', 'L', '1'};

bool labelChar(char c) { return (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-' || c == '_'; }

}  // namespace

uint32_t fnv1a(const char* s, size_t n) {
    uint32_t h = 2166136261u;
    for (size_t i = 0; i < n; ++i) {
        h ^= static_cast<uint8_t>(s[i]);
        h *= 16777619u;
    }
    return h;
}

bool Watchlist::validSuffix(const char* s, size_t n) {
    if (s == nullptr || n == 0 || n > kSuffixMax) return false;
    size_t label = 0;
    for (size_t i = 0; i < n; ++i) {
        if (s[i] == '.') {
            if (label == 0) return false;  // leading dot or ".."
            label = 0;
            continue;
        }
        if (!labelChar(s[i]) || ++label > 63) return false;
    }
    return label > 0;  // no trailing dot
}

void Watchlist::clear(uint32_t version) {
    version_ = version;
    ruleCount_ = 0;
    poolUsed_ = 0;
    categoryCount_ = 0;
}

void Watchlist::loadDefaults() {
    clear(EWP_DEFAULT_WATCHLIST_VERSION);
    const char* err = nullptr;
    for (size_t i = 0; i < EWP_DEFAULT_CATEGORY_COUNT; ++i) {
        const ewp_default_category_t& c = EWP_DEFAULT_CATEGORIES[i];
        upsertCategory(c.id, c.name, strlen(c.name), &err);
    }
    for (size_t i = 0; i < EWP_DEFAULT_RULE_COUNT; ++i) {
        const ewp_default_rule_t& r = EWP_DEFAULT_RULES[i];
        upsertRule(r.suffix, strlen(r.suffix), r.category, r.flags, &err);
    }
}

int Watchlist::findRule(const char* s, size_t n, uint32_t hash) const {
    for (uint16_t i = 0; i < ruleCount_; ++i) {
        const Rule& r = rules_[i];
        if (r.hash == hash && r.len == n && memcmp(pool_ + r.off, s, n) == 0) return i;
    }
    return -1;
}

bool Watchlist::upsertRule(const char* s, size_t n, uint8_t category, uint8_t flags, const char** err) {
    if (!validSuffix(s, n)) {
        *err = "a rule suffix must be a lower-case domain like youtube.com (1-96 bytes)";
        return false;
    }
    if (category == 0 || category > kMaxCategories) {
        *err = "rule category must be 1-32";
        return false;
    }
    if ((flags & ~EWP_RULE_FLAGS_KNOWN) != 0) {
        *err = "unknown rule flag bits";
        return false;
    }
    const uint32_t h = fnv1a(s, n);
    const int at = findRule(s, n, h);
    if (at >= 0) {
        rules_[at].category = category;
        rules_[at].flags = flags;
        return true;
    }
    if (ruleCount_ >= kMaxRules) {
        *err = "the watchlist holds at most 128 rules";
        return false;
    }
    if (poolUsed_ + n + 1 > kPoolBytes) {
        *err = "the watchlist text exceeds the node's 6 KiB rule pool";
        return false;
    }
    Rule& r = rules_[ruleCount_++];
    r.hash = h;
    r.off = poolUsed_;
    r.len = static_cast<uint8_t>(n);
    r.category = category;
    r.flags = flags;
    memcpy(pool_ + poolUsed_, s, n);
    pool_[poolUsed_ + n] = '\0';
    poolUsed_ = static_cast<uint16_t>(poolUsed_ + n + 1);
    return true;
}

bool Watchlist::upsertCategory(uint8_t id, const char* name, size_t n, const char** err) {
    if (id == 0 || id > kMaxCategories) {
        *err = "category id must be 1-32";
        return false;
    }
    if (n == 0 || n > kCategoryNameMax || !ewp::utf8_valid(name, n)) {
        *err = "category name must be 1-48 bytes of UTF-8";
        return false;
    }
    for (size_t i = 0; i < n; ++i) {
        if (static_cast<uint8_t>(name[i]) < 0x20 || name[i] == 0x7F) {
            *err = "category name contains control characters";
            return false;
        }
    }
    size_t at = categoryCount_;
    for (size_t i = 0; i < categoryCount_; ++i) {
        if (catIds_[i] == id) at = i;
    }
    if (at == categoryCount_) {
        if (categoryCount_ >= kMaxCategories) {
            *err = "at most 32 categories";
            return false;
        }
        ++categoryCount_;
    }
    catIds_[at] = id;
    memcpy(catNames_[at], name, n);
    catNames_[at][n] = '\0';
    return true;
}

bool Watchlist::match(const char* q, size_t n, Match& m) const {
    m = Match{};
    if (ruleCount_ == 0 || n == 0) return false;
    // Candidates from the most specific (the whole name) to the least (the TLD):
    // the first hit is the longest matching suffix.
    size_t start = 0;
    for (;;) {
        const size_t len = n - start;
        if (len <= kSuffixMax) {
            const int at = findRule(q + start, len, fnv1a(q + start, len));
            if (at >= 0) {
                const Rule& r = rules_[at];
                const bool bare = start == 0;  // the rule's suffix IS the whole name
                if (!(bare && (r.flags & EWP_RULE_SUBDOMAINS_ONLY))) {
                    m.index = at;
                    m.category = r.category;
                    m.flags = r.flags;
                    return true;
                }
            }
        }
        const char* dot = static_cast<const char*>(memchr(q + start, '.', len));
        if (dot == nullptr) return false;
        start = static_cast<size_t>(dot - q) + 1;
        if (start >= n) return false;
    }
}

size_t Watchlist::serialize(uint8_t* out, size_t cap) const {
    size_t need = 4 + 4 + 2 + 1;
    for (uint16_t i = 0; i < ruleCount_; ++i) need += 3u + rules_[i].len;
    for (uint8_t i = 0; i < categoryCount_; ++i) need += 2u + strlen(catNames_[i]);
    if (need > cap) return 0;
    size_t o = 0;
    memcpy(out, kMagic, 4);
    o += 4;
    out[o++] = static_cast<uint8_t>(version_ >> 24);
    out[o++] = static_cast<uint8_t>(version_ >> 16);
    out[o++] = static_cast<uint8_t>(version_ >> 8);
    out[o++] = static_cast<uint8_t>(version_);
    out[o++] = static_cast<uint8_t>(ruleCount_ >> 8);
    out[o++] = static_cast<uint8_t>(ruleCount_);
    for (uint16_t i = 0; i < ruleCount_; ++i) {
        const Rule& r = rules_[i];
        out[o++] = r.category;
        out[o++] = r.flags;
        out[o++] = r.len;
        memcpy(out + o, pool_ + r.off, r.len);
        o += r.len;
    }
    out[o++] = categoryCount_;
    for (uint8_t i = 0; i < categoryCount_; ++i) {
        const size_t len = strlen(catNames_[i]);
        out[o++] = catIds_[i];
        out[o++] = static_cast<uint8_t>(len);
        memcpy(out + o, catNames_[i], len);
        o += len;
    }
    return o;
}

bool Watchlist::deserialize(const uint8_t* in, size_t n) {
    if (in == nullptr || n < 11 || memcmp(in, kMagic, 4) != 0) return false;
    Watchlist& scratch = *this;  // on failure the caller reloads the defaults
    size_t o = 4;
    const uint32_t ver = (static_cast<uint32_t>(in[4]) << 24) | (static_cast<uint32_t>(in[5]) << 16) |
                         (static_cast<uint32_t>(in[6]) << 8) | in[7];
    o = 8;
    scratch.clear(ver);
    const uint16_t rules = static_cast<uint16_t>((in[o] << 8) | in[o + 1]);
    o += 2;
    const char* err = nullptr;
    for (uint16_t i = 0; i < rules; ++i) {
        if (o + 3 > n) return false;
        const uint8_t cat = in[o];
        const uint8_t flags = in[o + 1];
        const uint8_t len = in[o + 2];
        o += 3;
        if (o + len > n) return false;
        if (!scratch.upsertRule(reinterpret_cast<const char*>(in + o), len, cat, flags, &err)) return false;
        o += len;
    }
    if (o + 1 > n) return false;
    const uint8_t cats = in[o++];
    for (uint8_t i = 0; i < cats; ++i) {
        if (o + 2 > n) return false;
        const uint8_t id = in[o];
        const uint8_t len = in[o + 1];
        o += 2;
        if (o + len > n) return false;
        if (!scratch.upsertCategory(id, reinterpret_cast<const char*>(in + o), len, &err)) return false;
        o += len;
    }
    return o == n;
}

}  // namespace netobs
