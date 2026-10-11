/*
 * AEDI - IONITY GLOBAL | @ionity/ewp | runtime guards | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 * Run: node --experimental-strip-types --test test/scalars.test.ts
 */
import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import {
  CHANNEL_POLICY,
  f32,
  fixedBytes,
  hwid,
  i8,
  siteId,
  u16,
  u32,
  u8,
  unit,
  utf8,
  utf8ByteLength,
  HEADER_LAYOUT,
  EWP_HEADER_LEN,
  MsgType,
  FRAME_FLAGS_KNOWN_MASK,
  FrameFlag,
  InferenceInputKey,
  HelloBodyKey,
  TelemetryBodyKey,
} from '../src/index.ts';

test('integer brands enforce C ranges', () => {
  assert.equal(u8(255), 255);
  assert.throws(() => u8(256), RangeError);
  assert.throws(() => u8(-1), RangeError);
  assert.throws(() => u8(1.5), RangeError);
  assert.equal(u16(65535), 65535);
  assert.throws(() => u16(65536), RangeError);
  assert.equal(u32(0xffff_ffff), 0xffff_ffff);
  assert.throws(() => u32(2 ** 32), RangeError);
  assert.equal(i8(-128), -128);
  assert.throws(() => i8(128), RangeError);
});

test('floats must be finite binary32', () => {
  assert.throws(() => f32(Number.NaN), RangeError);
  assert.throws(() => f32(Number.POSITIVE_INFINITY), RangeError);
  assert.throws(() => f32(1e39), RangeError); // overflows binary32
  assert.equal(f32(0.1), Math.fround(0.1));
  assert.throws(() => unit(1.0001), RangeError);
  assert.equal(unit(1), 1);
});

test('strings are bounded in UTF-8 bytes and NUL-free', () => {
  assert.equal(utf8ByteLength('Ü'), 2);
  assert.equal(utf8ByteLength('€'), 3);
  assert.equal(utf8ByteLength('😀'), 4);
  assert.doesNotThrow(() => utf8('ÜÜ', 4));
  assert.throws(() => utf8('ÜÜÜ', 4), RangeError);
  assert.throws(() => utf8('a\u0000b', 8), RangeError);
  assert.throws(() => utf8('\ud800', 8), RangeError);
});

test('fixed-size bytes', () => {
  assert.throws(() => fixedBytes(new Uint8Array(7), 8), RangeError);
  assert.equal(fixedBytes(new Uint8Array(8), 8).byteLength, 8);
});

test('topic levels cannot inject MQTT wildcards', () => {
  assert.throws(() => siteId('office/#'), RangeError);
  assert.throws(() => siteId('+'), RangeError);
  assert.throws(() => hwid('esp32-98A316E5D18C'), RangeError); // must be lowercase
  assert.equal(hwid('esp32-98a316e5d18c'), 'esp32-98a316e5d18c');
});

test('header layout is contiguous, aligned and 16 bytes', () => {
  let next = 0;
  for (const f of HEADER_LAYOUT) {
    assert.equal(f.offset, next, `gap before ${f.field}`);
    assert.equal(f.offset % f.size, 0, `${f.field} is not naturally aligned`);
    next = f.offset + f.size;
  }
  assert.equal(next, EWP_HEADER_LEN);
});

test('flag mask covers exactly the defined flags', () => {
  const all = Object.values(FrameFlag).reduce((a, b) => a | b, 0);
  assert.equal(all, FRAME_FLAGS_KNOWN_MASK);
});

test('every message type is routable to exactly one channel', () => {
  const seen = new Map<number, string>();
  for (const [ch, pol] of Object.entries(CHANNEL_POLICY)) {
    for (const t of pol.msgTypes) {
      assert.ok(!seen.has(t), `type ${t} on ${seen.get(t)} and ${ch}`);
      seen.set(t, ch);
    }
  }
  assert.equal(seen.size, Object.keys(MsgType).length);
});

test('CBOR key tables are 1-byte and unique (runtime echo of the type check)', () => {
  for (const table of [InferenceInputKey, HelloBodyKey, TelemetryBodyKey]) {
    const vals = Object.values(table);
    assert.equal(new Set(vals).size, vals.length);
    assert.ok(vals.every((k) => Number.isInteger(k) && k >= 0 && k <= 23));
  }
});
