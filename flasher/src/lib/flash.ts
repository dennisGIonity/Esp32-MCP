// ===========================================================================
// AEDI - IONITY GLOBAL | esptool-js wrapper | Policy 986 AED
// ===========================================================================
import { ESPLoader, Transport, type IEspLoaderTerminal } from "esptool-js";
import type { LogFn } from "./serial";
import { chunksKeepingNvs } from "./partitions";

export interface ChipInfo {
  description: string;   // "ESP32-S3 (QFN56) (revision v0.2)"
  name: string;          // loader.chip.CHIP_NAME
  mac?: string;
  flashSize?: string;
  features?: string[];
  vid?: number;
  pid?: number;
}

export class Flasher {
  private transport?: Transport;
  private loader?: ESPLoader;

  constructor(private port: SerialPort, private log: LogFn) {}

  private terminal(): IEspLoaderTerminal {
    let partial = "";
    return {
      clean: () => {},
      write: (d: string) => { partial += d; },
      writeLine: (d: string) => { this.log(partial + d, "info"); partial = ""; },
    };
  }

  /** Put the chip in the ROM bootloader and identify it. */
  async connect(baud = 921600): Promise<ChipInfo> {
    this.transport = new Transport(this.port, false);
    this.loader = new ESPLoader({ transport: this.transport, baudrate: baud, terminal: this.terminal(), romBaudrate: 115200 });
    const description = await this.loader.main();
    const info = this.port.getInfo();
    const chip: ChipInfo = {
      description,
      name: this.loader.chip.CHIP_NAME,
      vid: info.usbVendorId,
      pid: info.usbProductId,
    };
    try {
      chip.mac = await this.loader.chip.readMac(this.loader);
    } catch { /* optional */ }
    try {
      chip.flashSize = await this.loader.detectFlashSize();
    } catch { /* optional */ }
    try {
      chip.features = await this.loader.chip.getChipFeatures(this.loader);
    } catch { /* optional */ }
    return chip;
  }

  async flash(image: Uint8Array, opts: { eraseAll: boolean; keepSettings?: boolean; onProgress: (pct: number) => void }): Promise<void> {
    if (!this.loader) throw new Error("not connected");
    if (image[0] !== 0xe9) throw new Error("not an ESP image (first byte must be 0xE9) - use the *.bin from firmware/dist");
    if (opts.eraseAll) {
      this.log("Erasing the whole flash (this also clears old WiFi / NVS)…", "info");
      await this.loader.eraseFlash();
    }
    const fileArray = opts.keepSettings && !opts.eraseAll ? chunksKeepingNvs(image) : [{ data: image, address: 0 }];
    if (fileArray.length > 1)
      this.log(`Keeping board settings: writing ${fileArray.map((c) => "0x" + c.address.toString(16)).join(" + ")}, skipping NVS`, "info");
    const total = fileArray.reduce((n, c) => n + c.data.length, 0);
    const before = fileArray.map((_, i) => fileArray.slice(0, i).reduce((n, c) => n + c.data.length, 0));
    await this.loader.writeFlash({
      fileArray,
      flashMode: "keep",
      flashFreq: "keep",
      flashSize: "keep",
      eraseAll: false,
      compress: true,
      reportProgress: (i, written) => opts.onProgress(Math.round(((before[i] + written) / total) * 100)),
    });
  }

  /** Reset into the new app and give the port back. */
  async resetAndRelease(): Promise<void> {
    try {
      await this.loader?.after("hard_reset");
    } finally {
      try { await this.transport?.disconnect(); } catch { /* ignore */ }
    }
  }

  async release(): Promise<void> {
    try { await this.transport?.disconnect(); } catch { /* ignore */ }
  }
}

export async function sha256Hex(data: Uint8Array): Promise<string> {
  const d = await crypto.subtle.digest("SHA-256", data as unknown as ArrayBuffer);
  return Array.from(new Uint8Array(d), (b) => b.toString(16).padStart(2, "0")).join("");
}
