/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | test/host/main.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 *   ./fw_tests            run every test
 *   ./fw_tests cbor_      run the tests whose name contains "cbor_"
 */
#include "check.h"

int main(int argc, char** argv) { return check::runAll(argc > 1 ? argv[1] : nullptr); }
