/*
 * AEDI - IONITY GLOBAL | @ionity/portal | manifest.ts | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Firmware manifest (written by firmware/tools/build.py) and image selection.
 * Reads the fw 2.x manifest unchanged; fw 3 adds `provisioning.protocol`
 * "ionity-prov/2" and builds for ESP32-C6.
 */

export interface BuildInfo {
  readonly variant: string;
  readonly name: string;
  /** "ESP32-S3", "ESP32-C6", ... as chipFamily() returns it */
  readonly chip_family: string;
  /** image prints its console on native USB (USB-Serial-JTAG / CDC) instead of UART0 */
  readonly native_usb: boolean;
  readonly file: string;
  /** flash address the file is written to (0 for merged images) */
  readonly offset: number;
  readonly size: number;
  readonly sha256: string;
  readonly app_pct?: number | null;
}

export interface Manifest {
  readonly product: string;
  readonly version: string;
  readonly built_at?: string;
  readonly provisioning?: { readonly protocol: string; readonly baud?: number };
  readonly builds: readonly BuildInfo[];
}

export class ManifestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ManifestError';
  }
}

type J = Record<string, unknown>;
const isObj = (v: unknown): v is J => typeof v === 'object' && v !== null && !Array.isArray(v);

/** Validate untrusted JSON (fetched from a host) before anything acts on it. */
export function parseManifest(json: unknown): Manifest {
  if (!isObj(json)) throw new ManifestError('manifest is not a JSON object');
  const { product, version, builds } = json;
  if (typeof product !== 'string' || typeof version !== 'string') throw new ManifestError('manifest needs "product" and "version"');
  if (!Array.isArray(builds) || builds.length === 0) throw new ManifestError('manifest has no builds');
  const out: BuildInfo[] = builds.map((b, i) => {
    if (!isObj(b)) throw new ManifestError(`builds[${i}] is not an object`);
    const need = (k: string, t: 'string' | 'number' | 'boolean') => {
      if (typeof b[k] !== t) throw new ManifestError(`builds[${i}].${k} must be a ${t}`);
      return b[k];
    };
    const file = need('file', 'string') as string;
    // The file name becomes part of a URL: no path tricks.
    if (!/^[A-Za-z0-9._-]+\.bin$/.test(file)) throw new ManifestError(`builds[${i}].file "${file}" is not a plain *.bin name`);
    const sha256 = need('sha256', 'string') as string;
    if (!/^[0-9a-f]{64}$/.test(sha256)) throw new ManifestError(`builds[${i}].sha256 is not 64 lowercase hex digits`);
    const offset = need('offset', 'number') as number;
    const size = need('size', 'number') as number;
    if (!Number.isInteger(offset) || offset < 0 || offset % 0x1000 !== 0) throw new ManifestError(`builds[${i}].offset must be a 4 KiB-aligned address`);
    if (!Number.isInteger(size) || size <= 0 || size > 32 * 1024 * 1024) throw new ManifestError(`builds[${i}].size is out of range`);
    const appPct = b['app_pct'];
    return {
      variant: need('variant', 'string') as string,
      name: need('name', 'string') as string,
      chip_family: need('chip_family', 'string') as string,
      native_usb: need('native_usb', 'boolean') as boolean,
      file,
      offset,
      size,
      sha256,
      ...(typeof appPct === 'number' ? { app_pct: appPct } : {}),
    };
  });
  const prov = json['provisioning'];
  const builtAt = json['built_at'];
  return {
    product,
    version,
    ...(typeof builtAt === 'string' ? { built_at: builtAt } : {}),
    ...(isObj(prov) && typeof prov['protocol'] === 'string'
      ? { provisioning: { protocol: prov['protocol'], ...(typeof prov['baud'] === 'number' ? { baud: prov['baud'] } : {}) } }
      : {}),
    builds: out,
  };
}

/** Longest names first: "ESP32-C61" must not be read as "ESP32-C6". */
const FAMILIES = [
  'ESP32-C61', 'ESP32-S31', 'ESP32-H21', 'ESP32-E22',
  'ESP32-S3', 'ESP32-S2', 'ESP32-C6', 'ESP32-C5', 'ESP32-C3', 'ESP32-C2', 'ESP32-H2', 'ESP32-H4', 'ESP32-P4',
] as const;

/** esptool-js reports e.g. "ESP32-S3 (QFN56) (revision v0.2)"; prov hello reports "ESP32-S3". */
export function chipFamily(description: string): string {
  const d = description.toUpperCase();
  for (const f of FAMILIES) if (d.includes(f)) return f;
  return d.includes('ESP32') ? 'ESP32' : d.trim();
}

/**
 * The image must match where the console is: a native-USB board needs the image
 * whose Serial is USB CDC, or the provisioning handshake goes out of UART0 pins
 * nobody is listening on.
 */
export function pickBuild(m: Manifest, family: string, nativeUsb: boolean): BuildInfo | undefined {
  const same = m.builds.filter((b) => b.chip_family === family);
  return same.find((b) => b.native_usb === nativeUsb) ?? same[0];
}

/**
 * Where the ROM expects the second-stage bootloader (first byte 0xE9).
 * The installer prefers esptool-js' own value (ROM.BOOTLOADER_FLASH_OFFSET);
 * this table is the fallback and matches esptool-js 0.7 targets.
 */
export function bootloaderOffset(family: string): number {
  switch (family) {
    case 'ESP32':
    case 'ESP32-S2':
      return 0x1000;
    case 'ESP32-C5':
    case 'ESP32-P4':
    case 'ESP32-H4':
    case 'ESP32-S31':
      return 0x2000;
    default:
      return 0x0; // S3, C2, C3, C6, C61, H2
  }
}

/**
 * Sanity check before writing: a merged image has the bootloader magic where
 * the ROM will look for it. Catches "wrong chip" and "app-only .bin at 0x0".
 * Returns a reason, or null when the image looks right.
 */
export function checkImageForChip(image: Uint8Array, imageBase: number, family: string, blOffset = bootloaderOffset(family)): string | null {
  if (imageBase > blOffset) return null; // an app-only image written higher up: nothing to check here
  if (image[blOffset - imageBase] === 0xe9) return null;
  return `this image has no ${family} bootloader at 0x${blOffset.toString(16)} (wrong chip, or not a merged image)`;
}

export async function sha256Hex(data: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', data as Uint8Array<ArrayBuffer>);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
}
