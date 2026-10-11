<!-- AEDI - IONITY GLOBAL | @ionity/portal | Policy 986 AED | License AED 900 | (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd -->

# Ionity Board Portal (`@ionity/portal`)

Flash, provision and watch ESP32-MCP boards from the browser over Web Serial — the successor of
`flasher/`. React 19 · Vite 8 · Tailwind 4 · xterm.js 6 · esptool-js 0.7. Design and test record:
`docs/architecture/PHASE-2-PORTAL.md`.

## Use

```bash
npm install            # at the repository root (npm workspaces)
npm run dev:portal     # http://localhost:5173 - Chrome, Edge or Opera on a desktop
```

- **Connect board** → pick the USB port. The board identifies itself (firmware, provisioning version).
- Fill in **Wi-Fi**, **MCP host URL** and **Device UUID** (Generate makes a UUIDv7) → **Write to board**.
  The checklist shows each step: settings written, Wi-Fi joined, host reached, restarted, online, listed.
- The **serial console** shows the board's output live; passwords and tokens you typed are masked.
  **Save log** downloads the session (masked too).
- **Firmware**: install a release from `public/firmware/manifest.json` or a local `.bin`; "Keep board
  settings" is on by default.

No board at hand? Open `http://localhost:5173/?simulate=v2-usb` (also `v1`, `v2`). A banner marks the
simulated board; its demo network is `IONITY-LAB-IOT` with password `lab-pass-123`.

## Develop and test

```bash
npm run typecheck -w @ionity/portal
npm test -w @ionity/portal                               # vitest: serial session, scrubbing, flashing logic
npm run build -w @ionity/portal && npm run e2e:portal    # Playwright + axe-core against the build
```

| Folder | Contents |
|---|---|
| `src/features/device/` | `DeviceConsole` (the core component), `useDevice`, connection bar, board card |
| `src/features/provision/` | form, field component, pairing flow and checklist |
| `src/features/console/` | xterm.js hook and panel |
| `src/features/flash/` | firmware install panel |
| `src/lib/serial/` | `SerialSession` (single port owner), line splitter, secret scrubber, USB ids |
| `src/lib/flash/` | partition table, manifest, esptool-js installer |
| `src/sim/` | simulated Web Serial port + board |

Firmware images: copy `manifest.json` and the `*.bin` files from `firmware/dist/` (output of
`firmware/build.py`) into `public/firmware/`; git ignores them there, like `firmware/dist/`. Served under any
path: the build uses relative URLs.
