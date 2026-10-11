<!-- AEDI - IONITY GLOBAL | @ionity/portal | Policy 986 AED | (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd -->
# Firmware images for the portal's install panel

Copy `manifest.json` and the merged `*.bin` images here from `firmware/dist/` (written by
`firmware/build.py`); the page loads them as `firmware/manifest.json`. Git ignores everything in this folder
except this README, the same way `firmware/dist/` is ignored: images are built and published by CI, not
committed. Every image is checked against the manifest's size and SHA-256, and for the chip's bootloader,
before it is written to a board.
