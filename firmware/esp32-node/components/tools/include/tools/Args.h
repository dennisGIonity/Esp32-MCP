/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | tools/Args.h
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Strict-but-forward-compatible parsing of a tool's CBOR argument map:
 *   - the body must be ONE map (or empty = no arguments) with unsigned keys;
 *   - a key that appears twice is INVALID_ARGS (argKey = that key);
 *   - unknown keys are skipped (a newer gateway may add optional arguments -
 *     Phase 1 §4.6), malformed values are INVALID_ARGS;
 *   - trailing bytes after the map are INVALID_ARGS.
 * The callback reads the value of each key from the reader and returns false
 * after calling ctx.fail(); it must consume exactly one item for every key.
 */
#pragma once

#include <stdint.h>

#include "ewp/cbor.h"
#include "mcp/MCPServer.h"

namespace tools {

template <typename OnKey>
bool parseArgs(mcp::ToolContext& ctx, OnKey&& onKey) {
    if (ctx.argsLen() == 0) return true;
    ewp::CborReader r(ctx.args(), ctx.argsLen());
    uint32_t n = 0;
    if (!r.readMap(n)) {
        ctx.fail(EWP_ST_INVALID_ARGS, "arguments must be a CBOR map");
        return false;
    }
    uint32_t seen = 0;  // keys 0..31 (EWP keys are 1..23)
    for (uint32_t i = 0; i < n; ++i) {
        uint64_t key = 0;
        if (!r.readUint(key)) {
            ctx.fail(EWP_ST_INVALID_ARGS, "argument keys must be unsigned integers");
            return false;
        }
        const int argKey = key <= 255 ? static_cast<int>(key) : -1;
        if (key < 32) {
            const uint32_t bit = 1u << key;
            if (seen & bit) {
                ctx.fail(EWP_ST_INVALID_ARGS, "duplicate argument key", argKey);
                return false;
            }
            seen |= bit;
        }
        if (!onKey(key, r)) {
            if (!ctx.failed()) ctx.fail(EWP_ST_INVALID_ARGS, "malformed argument", argKey);
            return false;
        }
    }
    if (!r.atEnd()) {
        ctx.fail(EWP_ST_INVALID_ARGS, "trailing bytes after the arguments");
        return false;
    }
    return true;
}

/** For the default branch of an OnKey callback: skip a value we do not know. */
inline bool skipUnknownArg(ewp::CborReader& r) { return r.skip(); }

}  // namespace tools
