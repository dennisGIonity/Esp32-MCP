/*
 * AEDI - IONITY GLOBAL | @ionity/portal | partitions.ts | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * ESP-IDF partition table reader (ported from flasher 2.0, hardened).
 *
 * A merged firmware image carries its partition table at 0x8000. "Keep board
 * settings" writes bootloader, table, otadata and app but skips the NVS
 * partition (Wi-Fi, MCP host, device UUID, tokens) - and the NVS-keys partition,
 * because overwriting the keys of an encrypted NVS makes the settings unreadable.
 *
 * FLASH-SAFETY: esptool erases whole 4 KiB sectors before writing. A chunk that
 * ends inside a sector would erase the start of the NVS partition behind it, so
 * skipped partitions must be sector-aligned in offset and size. ESP-IDF's
 * gen_esp32part.py enforces that; a table that breaks it is rejected here rather
 * than trusted.
 */

export interface Partition {
  readonly label: string;
  readonly type: number;
  readonly subtype: number;
  readonly offset: number;
  readonly size: number;
  readonly encrypted: boolean;
}

export interface FlashChunk {
  readonly data: Uint8Array;
  readonly address: number;
}

export const PT_OFFSET = 0x8000;
/** CONFIG_PARTITION_TABLE size: 3 KiB = 95 entries + MD5 row + end marker. */
const PT_MAX_BYTES = 0xc00;
const ENTRY_BYTES = 32;
const ENTRY_MAGIC = 0x50aa;
const MD5_MAGIC = 0xebeb;
const SECTOR = 0x1000;

export const PART_TYPE_DATA = 0x01;
export const PART_SUBTYPE_NVS = 0x02;
export const PART_SUBTYPE_NVS_KEYS = 0x04;

export class PartitionTableError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'PartitionTableError';
  }
}

const labelDecoder = new TextDecoder('ascii');

/**
 * @param img        the image as it will be written
 * @param imageBase  flash address the image is written to (0 for merged images)
 */
export function parsePartitions(img: Uint8Array, imageBase = 0): Partition[] {
  const at = PT_OFFSET - imageBase;
  if (at < 0 || at + ENTRY_BYTES > img.byteLength) return [];
  const dv = new DataView(img.buffer, img.byteOffset, img.byteLength);
  const out: Partition[] = [];
  for (let o = at; o + ENTRY_BYTES <= img.byteLength && o < at + PT_MAX_BYTES; o += ENTRY_BYTES) {
    const magic = dv.getUint16(o, true);
    if (magic === MD5_MAGIC) break; // checksum row, then the end of the table
    if (magic !== ENTRY_MAGIC) break; // 0xFFFF = end (erased flash)
    const flags = dv.getUint32(o + 28, true);
    out.push({
      type: dv.getUint8(o + 2),
      subtype: dv.getUint8(o + 3),
      offset: dv.getUint32(o + 4, true),
      size: dv.getUint32(o + 8, true),
      label: readLabel(img.subarray(o + 12, o + 28)),
      encrypted: (flags & 0x1) === 1,
    });
  }
  return out;
}

/** 16-byte label field: NUL-padded by gen_esp32part.py; stop at 0x00 or erased 0xFF. */
function readLabel(field: Uint8Array): string {
  let n = 0;
  while (n < field.byteLength && field[n] !== 0x00 && field[n] !== 0xff) n++;
  return labelDecoder.decode(field.subarray(0, n));
}

export function isSettingsPartition(p: Partition): boolean {
  return p.type === PART_TYPE_DATA && (p.subtype === PART_SUBTYPE_NVS || p.subtype === PART_SUBTYPE_NVS_KEYS);
}

/**
 * Split an image into chunks that skip the settings partitions.
 * Returns one chunk (the whole image) when the image has no partition table or
 * no settings partition. Every chunk owns a copy of its bytes.
 */
export function chunksKeepingSettings(img: Uint8Array, imageBase = 0): FlashChunk[] {
  const skip = parsePartitions(img, imageBase)
    .filter(isSettingsPartition)
    .sort((a, b) => a.offset - b.offset);
  if (skip.length === 0) return [{ data: img.slice(), address: imageBase }];

  for (const p of skip) {
    if (p.offset % SECTOR !== 0 || p.size % SECTOR !== 0 || p.size === 0) {
      throw new PartitionTableError(
        `partition "${p.label}" (0x${p.offset.toString(16)}, ${p.size} bytes) is not aligned to 4 KiB flash sectors - keeping settings would erase part of it. Flash without "Keep board settings".`,
      );
    }
  }

  const chunks: FlashChunk[] = [];
  const end = imageBase + img.byteLength;
  let cur = imageBase;
  for (const p of skip) {
    if (p.offset > cur) chunks.push({ data: img.slice(cur - imageBase, Math.min(p.offset, end) - imageBase), address: cur });
    cur = Math.max(cur, p.offset + p.size);
  }
  if (cur < end) chunks.push({ data: img.slice(cur - imageBase), address: cur });
  return chunks.filter((c) => c.data.byteLength > 0);
}
