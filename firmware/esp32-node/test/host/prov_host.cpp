/*
 * AEDI - IONITY GLOBAL | ESP32-MCP node firmware 3 | test/host/prov_host.cpp
 * Doc ID: DOC-2026-10-ESP32MCP-FW3 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * The firmware's ionity-prov/2 board side on a pipe: stdin = bytes from the
 * host, stdout = what the board prints (boot log, announcement, replies).
 * The TypeScript interop test drives it with the real @ionity/prov ProvClient,
 * so both ends of the protocol are the production code.
 *
 *   ./prov_host [--fw2-nvs] [--broker-down]
 *     --fw2-nvs      start from what a fw 2.1.1 board leaves in NVS (migration path)
 *     --broker-down  the MCP host is unreachable (test -> broker.ok false)
 */
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include "prov/LineAssembler.h"
#include "prov/ProvHandler.h"
#include "prov_fakes.h"

namespace {

void writeOut(void*, const char* data, size_t len) {
    fwrite(data, 1, len, stdout);
    fflush(stdout);
}

}  // namespace

int main(int argc, char** argv) {
    pf::MemKv kv;
    pf::FakeBackend backend;
    for (int i = 1; i < argc; ++i) {
        if (strcmp(argv[i], "--fw2-nvs") == 0) {
            kv.setStr("wifi_ssid", "IONITY-LAB-IOT");
            kv.setStr("wifi_pass", "lab-pass-123");
            kv.setStr("server", "192.168.0.3");
            kv.setU16("mqtt_port", 1883);
            kv.setStr("site", "lab");
            kv.setU8("seeded", 1);
        } else if (strcmp(argv[i], "--broker-down") == 0) {
            backend.brokerReachable = false;
        }
    }
    prov::ConfigStore cfg;
    cfg.begin(kv);
    prov::LoadReport rep;
    cfg.load(rep);
    prov::ProvHandler handler;
    backend.handler = &handler;
    backend.cfg = &cfg;
    handler.begin(cfg, backend, pf::board(), &writeOut, nullptr);

    // What a booting board prints before the announcement: ordinary log lines.
    printf("ESP-ROM:esp32s3-20210327\r\n");
    printf("I (312) ionity: esp32-98a316e5d18c fw 3.0.0 (IDF v5.5.5) on ESP32-S3 rev 2\n");
    if (rep.migratedHostUrl) printf("I (318) ionity: fw 2.x settings migrated\n");
    fflush(stdout);
    handler.announce();

    static prov::LineAssembler<prov::ProvHandler::kLineMax> lines;
    uint8_t buf[512];
    for (;;) {
        const ssize_t n = read(0, buf, sizeof buf);
        if (n <= 0) break;
        lines.feed(
            buf, static_cast<size_t>(n), [&](const char* l, size_t len) { handler.onLine(l, len); },
            [&] { handler.onOverlong(); });
        if (backend.restarts > 0) {
            // A restart: the board reboots, prints its banner and announces again.
            backend.restarts = 0;
            printf("I (35) ionity: restarted\n");
            fflush(stdout);
            cfg.load(rep);
            handler.announce();
        }
    }
    return 0;
}
