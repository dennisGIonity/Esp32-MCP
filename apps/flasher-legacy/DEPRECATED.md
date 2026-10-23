<!--
AEDI - IONITY GLOBAL | ESP32-MCP | apps/flasher-legacy | Policy 986 AED | License AED 900
(c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd - All Rights Reserved - TM2
-->
# DEPRECATED - use `apps/portal`

This is the 2.x web flasher (React + esptool-js). It is **retired** as of the
NetObserve v3 release and kept only as a reference. Nothing builds or serves it:

| 2.x flasher                         | now in `apps/portal`                                            |
|-------------------------------------|-----------------------------------------------------------------|
| chip detection, image pick, flash   | `src/lib/flash/{installer,manifest,partitions}.ts`, `FlashPanel` |
| USB bridge names (`bridgeName`)     | `src/lib/serial/usb.ts` (`describePort`)                        |
| Wi-Fi + MCP host provisioning       | `@ionity/prov` (ionity-prov/2), `ProvisionForm`, `usePairingFlow` |
| host defaults (`/api/v1/provisioning/defaults`) | `FleetApi.provisioningDefaults()` -> form pre-fill   |
| firmware from the host (`/api/v1/firmware/manifest`) | `FlashPanel` source order: preference, host, bundled |
| "is the board listed" check         | `src/lib/host/verify.ts`                                        |
| serial log                          | `TerminalPanel` (xterm.js, secrets scrubbed)                    |
| -                                   | Fleet status, live DNS activity, flagged banner, watchlist      |

The fleet host serves the portal at `/portal/`; `/flasher/` redirects there.
The folder is not an npm workspace, CI no longer builds it, and the Docker
image no longer ships it. Delete it once nobody needs the reference.
