// ===========================================================================
// AEDI - IONITY GLOBAL | Fleet host (MCP host) API client | Policy 986 AED
// ---------------------------------------------------------------------------
// The flasher is served by the fleet host at /flasher, so by default the
// host is this page's own origin. On GitHub Pages (https) a LAN host on
// http:// is blocked as mixed content - the UI explains that and falls back
// to the images bundled with the page.
// ===========================================================================
import type { Manifest } from "./variants";

export interface ProvDefaults {
  server: string;
  mdns_host: string;
  mqtt_port: number;
  http_port: number;
  site: string;
  group: string;
  fleet_name: string;
  require_token: boolean;
  admin: boolean;
  fleet_token?: string;
  mqtt_user?: string;
}

export interface DeviceView {
  device_id: string;
  site: string;
  group: string;
  label?: string;
  fw?: string;
  ip?: string;
  transport: string;
  health: "online" | "stale" | "offline" | "alerting";
  last_seen_age_s?: number;
  metrics: Record<string, unknown>;
  mode?: string;
  mcp_url?: string;
}

export function defaultHostUrl(): string {
  const { origin, pathname, hostname } = window.location;
  // Served by the fleet server (…/flasher/), or `npm run dev` (vite proxies /api).
  if (pathname.includes("/flasher") || hostname === "localhost" || hostname === "127.0.0.1") return origin;
  return "";                                            // e.g. GitHub Pages: user enters it
}

export function mixedContentBlocked(hostUrl: string): boolean {
  return window.location.protocol === "https:" && hostUrl.startsWith("http:");
}

export class HostClient {
  constructor(public base: string, public adminToken = "") {}

  private url(p: string) {
    return this.base.replace(/\/+$/, "") + p;
  }

  private headers(): HeadersInit {
    return this.adminToken ? { Authorization: `Bearer ${this.adminToken}` } : {};
  }

  private async json<T>(p: string, init?: RequestInit, timeoutMs = 6000): Promise<T> {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), timeoutMs);
    try {
      const r = await fetch(this.url(p), { ...init, headers: { ...this.headers(), ...(init?.headers ?? {}) }, signal: ctl.signal });
      if (!r.ok) {
        let detail = r.statusText;
        try { detail = (await r.json()).detail ?? detail; } catch { /* not json */ }
        throw new Error(`${r.status} ${detail}`);
      }
      return (await r.json()) as T;
    } finally {
      clearTimeout(t);
    }
  }

  health() { return this.json<{ ok: boolean; fleet: string; mqtt: { connected: boolean }; mcp: { server: string } }>("/api/v1/health"); }
  defaults() { return this.json<ProvDefaults>("/api/v1/provisioning/defaults"); }
  manifest() { return this.json<Manifest>("/api/v1/firmware/manifest"); }
  imageUrl(file: string) { return this.url(`/api/v1/firmware/${encodeURIComponent(file)}`); }
  device(id: string) { return this.json<{ device: DeviceView }>(`/api/v1/devices/${encodeURIComponent(id)}?history=1`); }
  deviceMcp(id: string, rpc: object) {
    return this.json<{ result?: { tools?: { name: string; description?: string }[]; structuredContent?: unknown }; error?: { message: string } }>(
      `/api/v1/devices/${encodeURIComponent(id)}/mcp`,
      { method: "POST", body: JSON.stringify(rpc), headers: { "Content-Type": "application/json" } },
      15000,
    );
  }
}

export async function fetchBytes(url: string, onProgress?: (pct: number) => void): Promise<Uint8Array> {
  const r = await fetch(url, { cache: "no-cache" });
  if (!r.ok) throw new Error(`download ${url}: ${r.status}`);
  const total = Number(r.headers.get("content-length") || 0);
  if (!r.body || !total || !onProgress) return new Uint8Array(await r.arrayBuffer());
  const reader = r.body.getReader();
  const out = new Uint8Array(total);
  let got = 0;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    out.set(value, got);
    got += value.length;
    onProgress(Math.round((got / total) * 100));
  }
  return out.subarray(0, got);
}

export const store = {
  get(k: string): string | null {
    try { return localStorage.getItem(`ionity-flasher:${k}`); } catch { return null; }
  },
  set(k: string, v: string) {
    try { localStorage.setItem(`ionity-flasher:${k}`, v); } catch { /* private mode */ }
  },
};
