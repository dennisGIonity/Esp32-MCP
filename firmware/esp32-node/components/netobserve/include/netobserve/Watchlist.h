/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | netobserve/Watchlist.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * The domain watchlist a node flags DNS queries against (WatchRule in
 * packages/protocol/src/netobserve.ts):
 *
 *   [suffix, category 1..32, RuleFlag]   "youtube.com" matches youtube.com and
 *                                        every name below it, label by label
 *                                        (not notyoutube.com); SUBDOMAINS_ONLY
 *                                        ("*.googlevideo.com") skips the bare suffix.
 *
 * The most specific rule wins (longest matching suffix). A node boots with
 * the built-in list (EWP_DEFAULT_RULES, generated from watchlist.ts) and keeps
 * whatever the host sends with set_watchlist in NVS.
 *
 * The object is plain data (trivially copyable, no pointers), so a staged
 * copy can be built and swapped in whole: a set_watchlist that fails half-way
 * leaves the active list untouched. Suffix text lives in one shared pool.
 * THREADS: none here - NetObserve guards it with its mutex.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "ewp_protocol.h"

namespace netobs {

class Watchlist {
public:
    static constexpr size_t kMaxRules = EWP_LIMIT_WATCH_RULES;
    static constexpr size_t kMaxCategories = EWP_LIMIT_WATCH_CATEGORIES;
    static constexpr size_t kSuffixMax = EWP_LIMIT_WATCH_SUFFIX_BYTES;
    static constexpr size_t kCategoryNameMax = 48;
    /** Shared suffix text (with NULs). 128 rules of ~45 B; a list that needs more is refused. */
    static constexpr size_t kPoolBytes = 6144;

    struct Rule {
        uint32_t hash;   // FNV-1a of the suffix
        uint16_t off;    // into pool_
        uint8_t len;
        uint8_t category;
        uint8_t flags;
    };
    struct Match {
        int index = -1;
        uint8_t category = 0;
        uint8_t flags = 0;
    };

    /** Empty list with this version. */
    void clear(uint32_t version);
    /** The built-in list (EWP_DEFAULT_*), version EWP_DEFAULT_WATCHLIST_VERSION. */
    void loadDefaults();

    /**
     * Add or update a rule (an existing suffix gets the new category/flags).
     * `err` gets a static reason on failure. The suffix is validated:
     * lower-case LDH labels (plus '_'), 1..96 bytes, no empty label.
     */
    bool upsertRule(const char* suffix, size_t n, uint8_t category, uint8_t flags, const char** err);
    bool upsertCategory(uint8_t id, const char* name, size_t n, const char** err);

    /** Most specific rule for a lower-case query name. */
    bool match(const char* qname, size_t n, Match& m) const;

    uint32_t version() const { return version_; }
    uint16_t ruleCount() const { return ruleCount_; }
    uint8_t categoryCount() const { return categoryCount_; }
    const Rule& rule(size_t i) const { return rules_[i]; }
    const char* suffix(size_t i) const { return pool_ + rules_[i].off; }
    uint8_t categoryId(size_t i) const { return catIds_[i]; }
    const char* categoryName(size_t i) const { return catNames_[i]; }

    /**
     * Compact persistent form for NVS: "IWL1" + version + rules + categories.
     * Returns bytes written, 0 when cap is too small.
     */
    size_t serialize(uint8_t* out, size_t cap) const;
    /** False when the blob is not a valid serialisation - the list is then partial: load defaults. */
    bool deserialize(const uint8_t* in, size_t n);
    /** Upper bound of serialize() output. */
    static constexpr size_t kBlobMax = 4 + 4 + 2 + kMaxRules * (3 + kSuffixMax) + 1 + kMaxCategories * (2 + kCategoryNameMax);

    static bool validSuffix(const char* s, size_t n);

private:
    int findRule(const char* s, size_t n, uint32_t hash) const;

    uint32_t version_ = 0;
    uint16_t ruleCount_ = 0;
    uint16_t poolUsed_ = 0;
    uint8_t categoryCount_ = 0;
    Rule rules_[kMaxRules] = {};
    char pool_[kPoolBytes] = {};
    uint8_t catIds_[kMaxCategories] = {};
    char catNames_[kMaxCategories][kCategoryNameMax + 1] = {};
};

uint32_t fnv1a(const char* s, size_t n);

}  // namespace netobs
