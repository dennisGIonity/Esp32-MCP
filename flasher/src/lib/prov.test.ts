// AEDI - IONITY GLOBAL | ionity-prov/1 + variant selection tests | Policy 986 AED
import { describe, expect, it } from "vitest";
import { buildRequest, compactFields, parseProvLine, provisionWarnings, redact, validateFields } from "./prov";
import { chipFamily, pickBuild, type Manifest } from "./variants";

describe("ionity-prov/1", () => {
  it("builds one JSON line the firmware accepts", () => {
    const line = buildRequest("set", { ssid: "IONITY-LAB-IOT", pass: "12345678" });
    expect(line.endsWith("\n")).toBe(true);
    expect(JSON.parse(line)).toEqual({ ionity: "prov", op: "set", ssid: "IONITY-LAB-IOT", pass: "12345678" });
  });

  it("parses only prefixed replies, tolerating leading log noise", () => {
    expect(parseProvLine('[IONITY-NODE] WiFi OK')).toBeNull();
    expect(parseProvLine('IONITY-PROV {"ok":true,"op":"hello","device_id":"esp32-98a316e5d18c"}')?.device_id).toBe("esp32-98a316e5d18c");
    expect(parseProvLine('\u0000garbageIONITY-PROV {"ok":false,"op":"set","error":"x"}')?.error).toBe("x");
    expect(parseProvLine("IONITY-PROV {broken")).toBeNull();
    expect(parseProvLine('IONITY-PROV {"ok":true}')).toBeNull();          // no op
  });

  it("mirrors the firmware's validation", () => {
    expect(validateFields({ ssid: "" })).toHaveLength(1);
    expect(validateFields({ ssid: "a".repeat(33) })).toHaveLength(1);
    expect(validateFields({ ssid: "lab", pass: "short" })).toHaveLength(1);
    expect(validateFields({ ssid: "lab", pass: "" })).toHaveLength(0);          // open network
    expect(validateFields({ ssid: "lab", pass: "longenough", mqtt_port: 70000 })).toHaveLength(1);
  });

  it("keeps empty pass/server but drops other empty strings", () => {
    const c = compactFields({ ssid: "lab", pass: "", server: "", mcp_token: "", label: "" });
    expect(c).toEqual({ ssid: "lab", pass: "", server: "" });
  });

  it("warns about mDNS fallback and read-only MCP", () => {
    const w = provisionWarnings({ ssid: "lab", pass: "x".repeat(8), role: "node", server: "" });
    expect(w.join(" ")).toMatch(/mDNS/);
    expect(w.join(" ")).toMatch(/read-only/);
  });

  it("never logs secrets", () => {
    const r = redact(buildRequest("set", { pass: "hunter22", fleet_token: "t0k", mcp_token: "abc", ssid: "lab" }));
    expect(r).not.toMatch(/hunter22|t0k|"abc"/);
    expect(r).toMatch(/"ssid":"lab"/);
  });
});

describe("image selection", () => {
  const m: Manifest = {
    product: "ionity-esp32-mcp-node", version: "2.0.0",
    builds: [
      { variant: "esp32s3_uart", name: "S3 uart", chip_family: "ESP32-S3", native_usb: false, file: "esp32s3_uart.bin", offset: 0, size: 1, sha256: "" },
      { variant: "esp32s3_usb", name: "S3 usb", chip_family: "ESP32-S3", native_usb: true, file: "esp32s3_usb.bin", offset: 0, size: 1, sha256: "" },
      { variant: "esp32_classic", name: "ESP32", chip_family: "ESP32", native_usb: false, file: "esp32_classic.bin", offset: 0, size: 1, sha256: "" },
    ],
  };
  it("normalises esptool chip strings", () => {
    expect(chipFamily("ESP32-S3 (QFN56) (revision v0.2)")).toBe("ESP32-S3");
    expect(chipFamily("ESP32-D0WD-V3 (revision v3.1)")).toBe("ESP32");
    expect(chipFamily("ESP32-C3 (QFN32) (revision v0.4)")).toBe("ESP32-C3");
  });
  it("picks the native-USB image only for Espressif's own VID", () => {
    expect(pickBuild(m, "ESP32-S3", 0x1a86)?.variant).toBe("esp32s3_uart");   // CH340
    expect(pickBuild(m, "ESP32-S3", 0x303a)?.variant).toBe("esp32s3_usb");
    expect(pickBuild(m, "ESP32", 0x10c4)?.variant).toBe("esp32_classic");
    expect(pickBuild(m, "ESP32-C6", 0x303a)).toBeUndefined();
  });
});

import { chunksKeepingNvs, parsePartitions } from "./partitions";

function fakeImage(): Uint8Array {
  const img = new Uint8Array(0x20000).fill(0xff);
  img[0] = 0xe9;
  const rows: [string, number, number, number, number][] = [
    ["nvs", 1, 2, 0x9000, 0x5000], ["otadata", 1, 0, 0xe000, 0x2000], ["app0", 0, 0x10, 0x10000, 0x1e0000],
  ];
  const dv = new DataView(img.buffer);
  rows.forEach(([label, type, sub, off, size], i) => {
    const o = 0x8000 + i * 32;
    dv.setUint16(o, 0x50aa, true); img[o + 2] = type; img[o + 3] = sub;
    dv.setUint32(o + 4, off, true); dv.setUint32(o + 8, size, true);
    img.set(new TextEncoder().encode(label), o + 12);
    for (let k = o + 12 + label.length; k < o + 28; k++) img[k] = 0;
  });
  return img;
}

describe("partition table", () => {
  it("reads the min_spiffs layout", () => {
    const p = parsePartitions(fakeImage());
    expect(p.map((x) => x.label)).toEqual(["nvs", "otadata", "app0"]);
    expect(p[0]).toMatchObject({ type: 1, subtype: 2, offset: 0x9000, size: 0x5000 });
  });
  it("keep-settings skips exactly the NVS partition", () => {
    const c = chunksKeepingNvs(fakeImage());
    expect(c.map((x) => [x.address, x.data.length])).toEqual([[0, 0x9000], [0xe000, 0x20000 - 0xe000]]);
    expect(c[0].data[0]).toBe(0xe9);
  });
});
