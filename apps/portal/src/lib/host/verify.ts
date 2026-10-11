/*
 * AEDI - IONITY GLOBAL | @ionity/portal | verify.ts | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Last pairing step: does the fleet host (fleet-host 2.x today, the gateway's
 * REST API later) list the board? The host learns about a board from its
 * hello/presence message, so a 404 right after reboot is retried.
 *
 * The portal never sends secrets here - only the hwid in the path.
 */

export type HostCheck =
  | { readonly status: 'listed'; readonly health: string; readonly detail: string }
  | { readonly status: 'skipped'; readonly reason: string }
  | { readonly status: 'missing'; readonly reason: string }
  | { readonly status: 'error'; readonly reason: string };

/** The API base the portal is served from, when that is a fleet host or `vite dev`. */
export function defaultHostApi(loc: Pick<Location, 'origin' | 'pathname' | 'hostname'> | undefined = globalThis.location): string {
  if (!loc) return '';
  if (/\/(flasher|portal)(\/|$)/.test(loc.pathname) || loc.hostname === 'localhost' || loc.hostname === '127.0.0.1') return loc.origin;
  return '';
}

export function mixedContentBlocked(apiBase: string, pageProtocol: string = globalThis.location?.protocol ?? ''): boolean {
  return pageProtocol === 'https:' && apiBase.startsWith('http:');
}

export function normalizeApiBase(input: string): { ok: true; value: string } | { ok: false; error: string } {
  const s = input.trim();
  if (s === '') return { ok: true, value: '' };
  let u: URL;
  try {
    u = new URL(s);
  } catch {
    return { ok: false, error: 'enter a full URL, e.g. http://192.168.0.3:8099' };
  }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') return { ok: false, error: 'the host API is http:// or https://' };
  if (u.username || u.password) return { ok: false, error: 'do not put credentials in the URL' };
  return { ok: true, value: u.origin + u.pathname.replace(/\/+$/, '') };
}

interface Options {
  readonly attempts?: number;
  readonly intervalMs?: number;
  readonly timeoutMs?: number;
  readonly signal?: AbortSignal;
  readonly fetchImpl?: typeof fetch;
}

const wait = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    const t = setTimeout(resolve, ms);
    signal?.addEventListener(
      'abort',
      () => {
        clearTimeout(t);
        reject(signal.reason as Error);
      },
      { once: true },
    );
  });

export async function checkBoardOnHost(apiBase: string, hwid: string, opts: Options = {}): Promise<HostCheck> {
  if (apiBase === '') return { status: 'skipped', reason: 'no fleet host API configured' };
  if (mixedContentBlocked(apiBase)) {
    return { status: 'skipped', reason: `this page is https:// and the host is http:// - the browser blocks that (open the portal from the host itself)` };
  }
  const attempts = opts.attempts ?? 8;
  const fetchImpl = opts.fetchImpl ?? fetch;
  const url = `${apiBase}/api/v1/devices/${encodeURIComponent(hwid)}`;
  let last: HostCheck = { status: 'missing', reason: 'not listed yet' };
  for (let i = 0; i < attempts; i++) {
    if (i > 0) await wait(opts.intervalMs ?? 2500, opts.signal);
    const timeout = AbortSignal.timeout(opts.timeoutMs ?? 5000);
    const signal = opts.signal ? AbortSignal.any([opts.signal, timeout]) : timeout;
    try {
      const r = await fetchImpl(url, { signal, headers: { Accept: 'application/json' } });
      if (r.status === 404) {
        last = { status: 'missing', reason: `the host does not list ${hwid} yet` };
        continue;
      }
      if (!r.ok) return { status: 'error', reason: `host answered HTTP ${r.status}` };
      const type = r.headers.get('content-type') ?? '';
      if (!type.includes('json')) {
        // e.g. a static server answering every path with index.html: retrying will not help
        return { status: 'error', reason: `${apiBase} is not a fleet host API (it answered ${type.split(';')[0] || 'without a content type'})` };
      }
      const body = (await r.json()) as { device?: { health?: unknown; transport?: unknown; ip?: unknown } };
      const health = typeof body.device?.health === 'string' ? body.device.health : 'unknown';
      if (health === 'online' || health === 'alerting') {
        const via = typeof body.device?.transport === 'string' ? ` via ${body.device.transport}` : '';
        return { status: 'listed', health, detail: `${health}${via}` };
      }
      last = { status: 'missing', reason: `listed, but ${health}` };
    } catch (e) {
      if (opts.signal?.aborted) throw e;
      last = { status: 'error', reason: (e as Error).name === 'TimeoutError' ? 'host did not answer in time' : `cannot reach the host (${(e as Error).message})` };
    }
  }
  return last;
}
