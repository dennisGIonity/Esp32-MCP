#!/usr/bin/env bash
# AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | test/run.sh
# Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
#
# Host verification of the firmware's portable code, no board needed:
#   1. unit tests under ASan + UBSan      2. the same under TSan (routing races)
#   3. randomised robustness runs         4. TypeScript <-> C++ interop (ProvClient, cbor-x)
# Needs: cmake, ninja, g++ and an ESP-IDF checkout (IDF_PATH, for cJSON); node >= 22.18
# and `npm install` at the repository root for step 4.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
FW="$(cd "$HERE/.." && pwd)"
REPO="$(cd "$FW/../.." && pwd)"
OUT="${BUILD_DIR:-$FW/build}"
: "${IDF_PATH:?set IDF_PATH to an ESP-IDF checkout (cJSON comes from it)}"

cmake -S "$HERE/host" -B "$OUT/host" -G Ninja >/dev/null
ninja -C "$OUT/host"
"$OUT/host/fw_tests"

cmake -S "$HERE/host" -B "$OUT/host-tsan" -G Ninja -DSANITIZER=thread >/dev/null
ninja -C "$OUT/host-tsan" fw_tests
# TSan needs a narrow address space: newer kernels (CI runners: vm.mmap_rnd_bits=32) make it
# abort with "unexpected memory mapping". Run it with ASLR off when the host allows that.
TSAN_RUN=()
if command -v setarch >/dev/null 2>&1 && setarch "$(uname -m)" -R true 2>/dev/null; then
    TSAN_RUN=(setarch "$(uname -m)" -R)
fi
TSAN_OPTIONS=halt_on_error=1 ${TSAN_RUN[@]+"${TSAN_RUN[@]}"} "$OUT/host-tsan/fw_tests" mcp_

"$OUT/host/fw_fuzz" "${FUZZ_ITERATIONS:-40000}"

cd "$REPO"
FW_HOST_BUILD="$OUT/host" node --experimental-strip-types --no-warnings --test "$HERE/interop/interop.test.mjs"
