// AEDI - IONITY GLOBAL | ESP partition table reader | Policy 986 AED
// The merged image carries its partition table at 0x8000. The flasher reads
// it so "Keep board settings" can skip the NVS partition (WiFi, MCP host,
// tokens) while still writing bootloader, table, otadata and app.

export interface Partition { label: string; type: number; subtype: number; offset: number; size: number }

export const PT_OFFSET = 0x8000;

export function parsePartitions(img: Uint8Array, at = PT_OFFSET): Partition[] {
  const out: Partition[] = [];
  const dv = new DataView(img.buffer, img.byteOffset, img.byteLength);
  for (let o = at; o + 32 <= img.length && o < at + 0xc00; o += 32) {
    const magic = dv.getUint16(o, true);
    if (magic !== 0x50aa) break;                       // 0xEBEB = MD5 row, 0xFFFF = end
    const label = new TextDecoder().decode(img.subarray(o + 12, o + 28)).replace(/\0.*$/s, "");
    out.push({ type: img[o + 2], subtype: img[o + 3], offset: dv.getUint32(o + 4, true), size: dv.getUint32(o + 8, true), label });
  }
  return out;
}

/** Split an image into chunks that skip the NVS partition(s). */
export function chunksKeepingNvs(img: Uint8Array): { data: Uint8Array; address: number }[] {
  const nvs = parsePartitions(img).filter((p) => p.type === 1 && p.subtype === 2);
  if (!nvs.length) return [{ data: img, address: 0 }];
  const chunks: { data: Uint8Array; address: number }[] = [];
  let cur = 0;
  for (const p of nvs.sort((a, b) => a.offset - b.offset)) {
    if (p.offset > cur) chunks.push({ data: img.subarray(cur, Math.min(p.offset, img.length)), address: cur });
    cur = p.offset + p.size;
  }
  if (cur < img.length) chunks.push({ data: img.subarray(cur), address: cur });
  // esptool pads each chunk to 4 byte words itself; copy so every chunk owns its buffer
  return chunks.filter((c) => c.data.length).map((c) => ({ data: c.data.slice(), address: c.address }));
}
