/*
 * AEDI - IONITY GLOBAL | @ionity/portal | installer.ts | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Firmware installation with esptool-js, through SerialSession.lease():
 * the console reader is stopped and the port closed, esptool-js opens it at the
 * ROM baud rate, and the session takes the port back afterwards (re-enumeration
 * included). Loaded on demand - esptool-js is the largest dependency.
 *
 * FLASH-SAFETY:
 *  - Images from a manifest are checked against their SHA-256 and size before a
 *    single byte is written; a truncated download never reaches the chip.
 *  - The bootloader magic is checked at the chip's bootloader offset, so an
 *    ESP32-C6 image is never written to an ESP32-S3 (that board would not boot).
 *  - "Keep board settings" skips the NVS + NVS-keys partitions (partitions.ts).
 *  - On any failure after the chip entered the bootloader, a hard reset is still
 *    attempted so the board is not left sitting in download mode.
 */
import { ESPLoader, Transport, type IEspLoaderTerminal } from 'esptool-js';
import type { SerialSession } from '../serial/session.ts';
import { checkImageForChip, chipFamily, parseManifest, pickBuild, sha256Hex, type BuildInfo, type Manifest } from './manifest.ts';
import { chunksKeepingSettings, type FlashChunk } from './partitions.ts';

export type InstallStage = 'connect' | 'select' | 'download' | 'verify' | 'erase' | 'write' | 'reset';

export type ImageSource =
  | { readonly kind: 'manifest'; readonly manifest: Manifest; readonly baseUrl: string }
  | { readonly kind: 'file'; readonly name: string; readonly data: Uint8Array; readonly offset: number };

export interface InstallOptions {
  readonly source: ImageSource;
  readonly keepSettings: boolean;
  readonly eraseAll: boolean;
  /** stub baud rate after sync; the ROM always starts at 115200 */
  readonly baud?: number;
  readonly onStage: (stage: InstallStage, detail: string) => void;
  readonly onProgress: (pct: number) => void;
  readonly onLog: (text: string) => void;
  readonly fetchImpl?: typeof fetch;
}

export interface InstallResult {
  readonly chip: string;
  readonly family: string;
  readonly mac?: string;
  readonly flashSize?: string;
  readonly build?: BuildInfo;
  readonly bytesWritten: number;
  readonly keptSettings: boolean;
}

export async function fetchManifest(url: string, fetchImpl: typeof fetch = fetch): Promise<Manifest> {
  const r = await fetchImpl(url, { cache: 'no-cache' });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const type = r.headers.get('content-type') ?? '';
  // Static hosts answer unknown paths with index.html: say so instead of a JSON parse error.
  if (!type.includes('json')) throw new Error(`no manifest there (the server sent ${type.split(';')[0] || 'no content type'})`);
  return parseManifest(await r.json());
}

export async function downloadImage(url: string, expectedSize: number, onProgress: (pct: number) => void, fetchImpl: typeof fetch = fetch): Promise<Uint8Array> {
  const r = await fetchImpl(url, { cache: 'no-cache' });
  if (!r.ok) throw new Error(`download ${url}: HTTP ${r.status}`);
  if (!r.body) return new Uint8Array(await r.arrayBuffer());
  // MEMORY-SAFETY: never trust Content-Length for the allocation; the manifest's size
  // bounds the buffer and a longer body is an error, not a heap overrun.
  const out = new Uint8Array(expectedSize);
  const reader = r.body.getReader();
  let got = 0;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    if (got + value.byteLength > expectedSize) {
      await reader.cancel();
      throw new Error(`download ${url}: larger than the manifest says (${expectedSize} bytes)`);
    }
    out.set(value, got);
    got += value.byteLength;
    onProgress(Math.round((got / expectedSize) * 100));
  }
  if (got !== expectedSize) throw new Error(`download ${url}: got ${got} of ${expectedSize} bytes`);
  return out;
}

function loaderTerminal(onLog: (t: string) => void): IEspLoaderTerminal {
  let partial = '';
  return {
    clean: () => undefined,
    write: (d: string) => {
      partial += d;
    },
    writeLine: (d: string) => {
      const line = (partial + d).trimEnd();
      partial = '';
      if (line) onLog(line);
    },
  };
}

/** Flash the board behind `session`. Resolves after the chip was reset into the new app. */
export async function installFirmware(session: SerialSession, opts: InstallOptions): Promise<InstallResult> {
  const nativeUsb = session.getSnapshot().port?.nativeUsb ?? false;
  const fetchImpl = opts.fetchImpl ?? fetch;

  return session.lease('the firmware installer (esptool-js)', async (port) => {
    const transport = new Transport(port, false);
    const loader = new ESPLoader({ transport, baudrate: opts.baud ?? 921_600, romBaudrate: 115_200, terminal: loaderTerminal(opts.onLog) });
    let inBootloader = false;
    try {
      opts.onStage('connect', 'Putting the chip into download mode…');
      const description = await loader.main();
      inBootloader = true;
      const family = chipFamily(loader.chip.CHIP_NAME || description);
      const mac = await loader.chip.readMac(loader).catch(() => undefined);
      const flashSize = await loader.detectFlashSize().catch(() => undefined);

      // ---- choose the image -------------------------------------------------------
      let image: Uint8Array;
      let offset: number;
      let build: BuildInfo | undefined;
      if (opts.source.kind === 'manifest') {
        const { manifest, baseUrl } = opts.source;
        build = pickBuild(manifest, family, nativeUsb);
        if (!build) throw new Error(`firmware ${manifest.version} has no build for ${family}`);
        opts.onStage('select', `${build.name} - ${build.file}`);
        opts.onStage('download', `Downloading ${build.file}…`);
        image = await downloadImage(new URL(build.file, baseUrl).href, build.size, opts.onProgress, fetchImpl);
        opts.onStage('verify', 'Checking SHA-256…');
        const sha = await sha256Hex(image);
        if (sha !== build.sha256) throw new Error(`${build.file} failed its SHA-256 check (corrupt download or wrong manifest) - nothing was written`);
        offset = build.offset;
      } else {
        image = opts.source.data;
        offset = opts.source.offset;
        opts.onStage('select', `${opts.source.name} (${image.byteLength} bytes at 0x${offset.toString(16)})`);
      }
      const bad = checkImageForChip(image, offset, family, loader.chip.BOOTLOADER_FLASH_OFFSET);
      if (bad) throw new Error(`${bad} - nothing was written`);

      // ---- write ----------------------------------------------------------------
      if (opts.eraseAll) {
        opts.onStage('erase', 'Erasing the whole flash (settings included)…');
        await loader.eraseFlash();
      }
      const keep = opts.keepSettings && !opts.eraseAll;
      const fileArray: FlashChunk[] = keep ? chunksKeepingSettings(image, offset) : [{ data: image, address: offset }];
      const total = fileArray.reduce((n, c) => n + c.data.byteLength, 0);
      const before = fileArray.map((_, i) => fileArray.slice(0, i).reduce((n, c) => n + c.data.byteLength, 0));
      opts.onStage(
        'write',
        fileArray.length > 1
          ? `Writing ${fileArray.map((c) => '0x' + c.address.toString(16)).join(' + ')} (settings partition kept)…`
          : `Writing ${total} bytes at 0x${offset.toString(16)}…`,
      );
      await loader.writeFlash({
        fileArray: fileArray.map((c) => ({ data: c.data, address: c.address })),
        flashMode: 'keep',
        flashFreq: 'keep',
        flashSize: 'keep',
        eraseAll: false,
        compress: true,
        reportProgress: (i, written) => opts.onProgress(Math.min(100, Math.round((((before[i] ?? 0) + written) / total) * 100))),
      });

      opts.onStage('reset', 'Restarting into the new firmware…');
      await loader.after('hard_reset');
      inBootloader = false;
      return {
        chip: description,
        family,
        ...(mac ? { mac } : {}),
        ...(flashSize ? { flashSize } : {}),
        ...(build ? { build } : {}),
        bytesWritten: total,
        keptSettings: keep,
      };
    } catch (e) {
      if (inBootloader) await loader.after('hard_reset').catch(() => undefined); // do not leave it in download mode
      throw e;
    } finally {
      await transport.disconnect().catch(() => undefined);
    }
  });
}
