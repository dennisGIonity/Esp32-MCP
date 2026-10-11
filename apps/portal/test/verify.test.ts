/*
 * AEDI - IONITY GLOBAL | @ionity/portal | fleet host check | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
import { describe, expect, it } from 'vitest';
import { checkBoardOnHost, defaultHostApi, mixedContentBlocked, normalizeApiBase } from '../src/lib/host/verify.ts';

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

describe('fleet host check', () => {
  it('retries while the host has not seen the board, then reports it listed', async () => {
    const calls: string[] = [];
    const answers = [json(404, { detail: 'unknown' }), json(200, { device: { health: 'online', transport: 'mqtt' } })];
    const fetchImpl: typeof fetch = async (url) => {
      calls.push(String(url));
      return answers.shift() ?? json(500, {});
    };
    const r = await checkBoardOnHost('http://192.168.0.3:8099', 'esp32-98a316e5d18c', { fetchImpl, intervalMs: 5 });
    expect(r).toEqual({ status: 'listed', health: 'online', detail: 'online via mqtt' });
    expect(calls).toEqual(['http://192.168.0.3:8099/api/v1/devices/esp32-98a316e5d18c', 'http://192.168.0.3:8099/api/v1/devices/esp32-98a316e5d18c']);
  });

  it('stops at once when the address serves a web page instead of the API', async () => {
    let calls = 0;
    const fetchImpl: typeof fetch = async () => {
      calls++;
      return new Response('<!doctype html>', { status: 200, headers: { 'Content-Type': 'text/html' } });
    };
    const r = await checkBoardOnHost('http://localhost:4173', 'esp32-x', { fetchImpl, intervalMs: 5 });
    expect(r).toMatchObject({ status: 'error' });
    expect(r.status === 'error' && r.reason).toMatch(/not a fleet host API \(it answered text\/html\)/);
    expect(calls).toBe(1);
  });

  it('skips without a host, and on an https page talking to an http host', async () => {
    expect(await checkBoardOnHost('', 'x')).toMatchObject({ status: 'skipped' });
    expect(mixedContentBlocked('http://10.0.0.2:8099', 'https:')).toBe(true);
    expect(mixedContentBlocked('https://fleet.lan', 'https:')).toBe(false);
  });

  it('normalises the API base and refuses credentials in the URL', () => {
    expect(normalizeApiBase(' http://192.168.0.3:8099/ ')).toEqual({ ok: true, value: 'http://192.168.0.3:8099' });
    expect(normalizeApiBase('http://admin:pw@host')).toMatchObject({ ok: false });
    expect(normalizeApiBase('ftp://host')).toMatchObject({ ok: false });
  });

  it('defaults to the serving origin under /portal or on localhost', () => {
    expect(defaultHostApi({ origin: 'http://192.168.0.3:8099', pathname: '/portal/', hostname: '192.168.0.3' })).toBe('http://192.168.0.3:8099');
    expect(defaultHostApi({ origin: 'https://ionity-global.github.io', pathname: '/esp32/', hostname: 'ionity-global.github.io' })).toBe('');
  });
});
