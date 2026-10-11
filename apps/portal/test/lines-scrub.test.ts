/*
 * AEDI - IONITY GLOBAL | @ionity/portal | line splitting + secret scrubbing | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
import { describe, expect, it } from 'vitest';
import { LineHistory, LineSplitter, stripAnsi } from '../src/lib/serial/lines.ts';
import { MASK, SecretScrubber } from '../src/lib/serial/scrub.ts';

const enc = new TextEncoder();

describe('LineSplitter', () => {
  it('reassembles lines split across USB chunks and strips CR', () => {
    const s = new LineSplitter();
    expect(s.push(enc.encode('I (12) boot: hel'))).toEqual([]);
    expect(s.push(enc.encode('lo\r\nIONITY-PROV {"ok":true}\nrest'))).toEqual(['I (12) boot: hello', 'IONITY-PROV {"ok":true}']);
    expect(s.pending).toBe('rest');
  });

  it('keeps a multi-byte UTF-8 character that straddles two chunks', () => {
    const bytes = enc.encode('Wi-Fi “Café” ✓\n');
    const s = new LineSplitter();
    const out: string[] = [];
    for (const b of bytes) out.push(...s.push(Uint8Array.of(b))); // one byte at a time
    expect(out).toEqual(['Wi-Fi “Café” ✓']);
  });

  it('flushes an endless line instead of growing without bound (baud mismatch noise)', () => {
    const s = new LineSplitter(64);
    const out = s.push(enc.encode('x'.repeat(100)));
    expect(out).toEqual(['x'.repeat(100)]);
    expect(s.pending).toBe('');
  });
});

describe('LineHistory', () => {
  it('is bounded and strips ANSI colours from the saved text', () => {
    const h = new LineHistory(3);
    for (const l of ['a', '\x1b[0;32mI (1) b\x1b[0m', 'c', 'd']) h.push(l);
    expect(h.size).toBe(3);
    expect(h.text()).toBe('I (1) b\nc\nd\n');
    expect(stripAnsi('\x1b[1;31mE\x1b[0m')).toBe('E');
  });
});

describe('SecretScrubber', () => {
  it('masks a secret inside a line, longest first', () => {
    const s = new SecretScrubber();
    s.set(['lab-pass-123', 'lab-pass-123-extended', 'short']);
    expect(s.line('psk=lab-pass-123-extended ok')).toBe(`psk=${MASK} ok`);
    expect(s.line('psk=lab-pass-123')).toBe(`psk=${MASK}`);
    expect(s.line('short is not masked (too short to be safe to mask)')).toContain('short');
  });

  it('masks a secret split across stream chunks and releases a harmless tail', () => {
    const s = new SecretScrubber();
    s.set(['correct-horse']);
    let shown = s.push('W (88) wifi: pass=corr');
    expect(shown).toBe('W (88) wifi: pass='); // "corr" held back: could be the secret
    shown += s.push('ect-horse rejected\n');
    expect(shown).toBe(`W (88) wifi: pass=${MASK} rejected\n`);
    expect(s.push('a cor')).toBe('a '); // held
    expect(s.flush()).toBe('cor'); // stream went quiet: release
  });

  it('is a pass-through without secrets', () => {
    const s = new SecretScrubber();
    expect(s.active).toBe(false);
    expect(s.push('abc')).toBe('abc');
    expect(s.flush()).toBe('');
  });
});
