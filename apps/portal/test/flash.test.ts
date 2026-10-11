/*
 * AEDI - IONITY GLOBAL | @ionity/portal | partition table + manifest | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { downloadImage } from '../src/lib/flash/installer.ts';
import { bootloaderOffset, checkImageForChip, chipFamily, ManifestError, parseManifest, pickBuild, sha256Hex } from '../src/lib/flash/manifest.ts';
import { chunksKeepingSettings, parsePartitions, PartitionTableError, PT_OFFSET } from '../src/lib/flash/partitions.ts';

interface Row {
  label: string;
  type: number;
  subtype: number;
  offset: number;
  size: number;
}

/** A merged image: bootloader magic at 0, partition table at 0x8000, app filler after. */
function mergedImage(rows: Row[], length = 0x20000): Uint8Array {
  const img = new Uint8Array(length).fill(0xff);
  img[0] = 0xe9;
  const dv = new DataView(img.buffer);
  rows.forEach((r, i) => {
    const o = PT_OFFSET + i * 32;
    dv.setUint16(o, 0x50aa, true);
    img[o + 2] = r.type;
    img[o + 3] = r.subtype;
    dv.setUint32(o + 4, r.offset, true);
    dv.setUint32(o + 8, r.size, true);
    img.fill(0, o + 12, o + 28); // gen_esp32part.py NUL-pads the 16-byte label
    img.set(new TextEncoder().encode(r.label), o + 12);
    dv.setUint32(o + 28, 0, true);
  });
  const md5 = PT_OFFSET + rows.length * 32;
  dv.setUint16(md5, 0xebeb, true);
  return img;
}

const DEFAULT_TABLE: Row[] = [
  { label: 'nvs', type: 1, subtype: 2, offset: 0x9000, size: 0x5000 },
  { label: 'otadata', type: 1, subtype: 0, offset: 0xe000, size: 0x2000 },
  { label: 'app0', type: 0, subtype: 0x10, offset: 0x10000, size: 0x10000 },
];

describe('partition table', () => {
  it('parses entries and stops at the MD5 row', () => {
    const parts = parsePartitions(mergedImage(DEFAULT_TABLE));
    expect(parts.map((p) => p.label)).toEqual(['nvs', 'otadata', 'app0']);
    expect(parts[0]).toMatchObject({ type: 1, subtype: 2, offset: 0x9000, size: 0x5000, encrypted: false });
  });

  it('keeps settings: writes around NVS and NVS keys, never into them', () => {
    const rows = [...DEFAULT_TABLE, { label: 'nvs_keys', type: 1, subtype: 4, offset: 0x19000, size: 0x1000 }];
    const img = mergedImage(rows, 0x20000);
    const chunks = chunksKeepingSettings(img);
    expect(chunks.map((c) => [c.address, c.data.byteLength])).toEqual([
      [0x0, 0x9000],
      [0xe000, 0x19000 - 0xe000],
      [0x1a000, 0x20000 - 0x1a000],
    ]);
    // every chunk owns its bytes (esptool may pad them in place)
    expect(chunks[0]?.data.buffer).not.toBe(img.buffer);
    for (const c of chunks) for (const r of rows.filter((x) => x.type === 1 && (x.subtype === 2 || x.subtype === 4))) {
      const end = c.address + c.data.byteLength;
      expect(end <= r.offset || c.address >= r.offset + r.size).toBe(true);
    }
  });

  it('reads labels padded with erased flash (0xFF) too', () => {
    const img = mergedImage(DEFAULT_TABLE);
    img.fill(0xff, PT_OFFSET + 12 + 3, PT_OFFSET + 28);
    expect(parsePartitions(img)[0]?.label).toBe('nvs');
  });

  it('refuses a table whose NVS is not sector-aligned (an erase would eat into it)', () => {
    const img = mergedImage([{ label: 'nvs', type: 1, subtype: 2, offset: 0x9800, size: 0x5000 }]);
    expect(() => chunksKeepingSettings(img)).toThrow(PartitionTableError);
  });

  it('writes the whole image when there is no table', () => {
    const img = new Uint8Array(0x1000).fill(0xab);
    expect(chunksKeepingSettings(img, 0x10000)).toEqual([{ data: img, address: 0x10000 }]);
  });
});

describe('manifest', () => {
  const fw2 = JSON.parse(readFileSync(new URL('./fixtures/manifest-fw2.json', import.meta.url), 'utf8')) as unknown;

  it('reads the fw 2.0 manifest unchanged', () => {
    const m = parseManifest(fw2);
    expect(m.version).toBe('2.0.0');
    expect(m.provisioning?.protocol).toBe('ionity-prov/1');
    expect(m.builds).toHaveLength(4);
  });

  it('picks the image whose console matches the USB port', () => {
    const m = parseManifest(fw2);
    expect(pickBuild(m, 'ESP32-S3', true)?.variant).toBe('esp32s3_usb');
    expect(pickBuild(m, 'ESP32-S3', false)?.variant).toBe('esp32s3_uart');
    expect(pickBuild(m, 'ESP32-C6', false)).toBeUndefined();
  });

  it('rejects path tricks and malformed hashes from an untrusted host', () => {
    const bad = (patch: Record<string, unknown>) => {
      const m = structuredClone(fw2) as { builds: Record<string, unknown>[] };
      Object.assign(m.builds[0] as object, patch);
      return () => parseManifest(m);
    };
    expect(bad({ file: '../../etc/passwd.bin' })).toThrow(ManifestError);
    expect(bad({ file: 'https://evil.example/x.bin' })).toThrow(ManifestError);
    expect(bad({ sha256: 'ABC' })).toThrow(ManifestError);
    expect(bad({ offset: 0x1234 })).toThrow(ManifestError);
  });

  it('names chip families like esptool-js reports them', () => {
    expect(chipFamily('ESP32-S3 (QFN56) (revision v0.2)')).toBe('ESP32-S3');
    expect(chipFamily('ESP32-C61 (revision v1.0)')).toBe('ESP32-C61');
    expect(chipFamily('ESP32-C6 (QFN40) (revision v0.1)')).toBe('ESP32-C6');
    expect(chipFamily('ESP32-D0WD-V3 (revision v3.1)')).toBe('ESP32');
    expect(bootloaderOffset('ESP32')).toBe(0x1000);
    expect(bootloaderOffset('ESP32-C6')).toBe(0);
  });

  it('checks the bootloader magic where the chip expects it', () => {
    const s3 = mergedImage(DEFAULT_TABLE);
    expect(checkImageForChip(s3, 0, 'ESP32-S3')).toBeNull();
    expect(checkImageForChip(s3, 0, 'ESP32')).toMatch(/no ESP32 bootloader at 0x1000/);
    expect(checkImageForChip(new Uint8Array(16), 0x10000, 'ESP32-S3')).toBeNull(); // app-only image
  });
});

describe('download', () => {
  const body = (bytes: Uint8Array<ArrayBuffer>): typeof fetch => async () => new Response(bytes);

  it('verifies size against the manifest and computes SHA-256', async () => {
    const data = new TextEncoder().encode('ionity');
    const pct: number[] = [];
    const got = await downloadImage('https://h/x.bin', data.byteLength, (p) => pct.push(p), body(data));
    expect(got).toEqual(data);
    expect(pct.at(-1)).toBe(100);
    expect(await sha256Hex(got)).toBe(createHash('sha256').update('ionity').digest('hex'));
  });

  it('never writes past the buffer the manifest sized (longer body = error)', async () => {
    await expect(downloadImage('https://h/x.bin', 4, () => undefined, body(new Uint8Array(10)))).rejects.toThrow(/larger than the manifest/);
    await expect(downloadImage('https://h/x.bin', 20, () => undefined, body(new Uint8Array(10)))).rejects.toThrow(/got 10 of 20/);
  });
});
