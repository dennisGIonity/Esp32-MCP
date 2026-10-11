/*
 * AEDI - IONITY GLOBAL | @ionity/prov/testing | fake-board.ts
 * Doc ID: DOC-2026-10-ESP32MCP-PROV2 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * A simulated ESP32 console: ESP-IDF style boot logs plus the provisioning
 * protocol, as fw 2.x (prov/1, modelled on Provision.ino) or fw 3 (prov/2,
 * the Phase 3 contract). Used by unit tests, the portal's ?simulate mode and
 * the Playwright end-to-end run. It is a test double, not firmware: it only
 * models what the host can observe over the serial line.
 */
import { crc32Hex } from '../crc32.ts';
import { PROV_PREFIX, PROV_V1_LINE_MAX, PROV_V2_LINE_MAX } from '../framing.ts';
import { V1_SET_FIELDS, type SetFieldName } from '../fields.ts';

const timers = globalThis as unknown as { setTimeout(fn: () => void, ms: number): unknown };

export interface FakeNetwork {
  readonly ssid: string;
  readonly rssi: number;
  readonly ch: number;
  /** undefined = open network */
  readonly pass?: string;
}

export interface FakeBoardOptions {
  readonly prov: 1 | 2;
  readonly hwid?: string;
  readonly fw?: string;
  readonly chip?: string;
  readonly networks?: readonly FakeNetwork[];
  /** the broker/host accepts the board once Wi-Fi is up */
  readonly brokerReachable?: boolean;
  /** native USB: reboot makes the port vanish and come back (re-enumeration) */
  readonly nativeUsb?: boolean;
  /** milliseconds between boot log lines (0 in unit tests) */
  readonly pace?: number;
}

export interface FakeBoardIo {
  /** bytes from the board to the host */
  emit(text: string): void;
  /** native USB only: the port disappears during reset ... */
  detach?(): void;
  /** ... and enumerates again */
  attach?(): void;
}

type Json = Record<string, unknown>;

const C = { info: '\x1b[0;32m', warn: '\x1b[0;33m', err: '\x1b[0;31m', reset: '\x1b[0m' } as const;

const SECRET_KEYS = new Set(['pass', 'mqtt_pass', 'fleet_token', 'mcp_token', 'ota_pass']);
const ENVELOPE: ReadonlySet<string> = new Set(['ionity', 'op', 'v', 'id']);
const V2_FIELDS: readonly SetFieldName[] = [
  'ssid', 'pass', 'host_url', 'device_uuid', 'ca_pem', 'mqtt_user', 'mqtt_pass',
  'fleet_token', 'mcp_token', 'ota_pass', 'role', 'site', 'group', 'label',
];

export class FakeBoard {
  readonly opts: Required<Omit<FakeBoardOptions, 'networks'>> & { networks: readonly FakeNetwork[] };
  readonly #io: FakeBoardIo;
  readonly nvs = new Map<string, string | number>();
  #rx = '';
  #uptimeMs = 0;
  #wifiUp = false;
  #ip = '';
  #rssi = 0;
  #mqtt = false;
  #booted = false;
  rebootCount = 0;
  /** test hooks */
  corruptNextReply = false;
  dropNextReply = false;
  readonly received: Json[] = [];

  constructor(options: FakeBoardOptions, io: FakeBoardIo) {
    this.opts = {
      prov: options.prov,
      hwid: options.hwid ?? 'esp32-98a316e5d18c',
      fw: options.fw ?? (options.prov === 2 ? '3.0.0' : '2.1.1'),
      chip: options.chip ?? 'ESP32-S3',
      networks: options.networks ?? [
        { ssid: 'Afrihost Fibre DTM', rssi: -44, ch: 11, pass: 'correct-horse' },
        { ssid: 'IONITY-LAB-IOT', rssi: -61, ch: 6, pass: 'lab-pass-123' },
        { ssid: 'Guest', rssi: -78, ch: 1 },
      ],
      brokerReachable: options.brokerReachable ?? true,
      nativeUsb: options.nativeUsb ?? false,
      pace: options.pace ?? 0,
    };
    this.#io = io;
  }

  // ---------------------------------------------------------------- console output
  #log(level: 'I' | 'W' | 'E', tag: string, msg: string): void {
    const color = level === 'I' ? C.info : level === 'W' ? C.warn : C.err;
    this.#io.emit(`${color}${level} (${this.#uptimeMs}) ${tag}: ${msg}${C.reset}\n`);
  }

  #reply(obj: Json): void {
    if (this.dropNextReply) {
      this.dropNextReply = false;
      return;
    }
    const text = JSON.stringify(obj);
    if (this.opts.prov === 2) {
      let crc = crc32Hex(text);
      if (this.corruptNextReply) {
        this.corruptNextReply = false;
        crc = crc.replace(/^./, (c) => (c === '0' ? '1' : '0'));
      }
      this.#io.emit(`${PROV_PREFIX}${text} *${crc}\n`);
    } else {
      this.#io.emit(`${PROV_PREFIX}${text}\n`);
    }
  }

  // ---------------------------------------------------------------- lifecycle
  /** Power-on: boot log, then the hello announcement. */
  boot(): void {
    const steps: (() => void)[] = [
      () => this.#io.emit(`ESP-ROM:esp32s3-20210327\r\nrst:0x1 (POWERON),boot:0x8 (SPI_FAST_FLASH_BOOT)\r\n`),
      () => this.#log('I', 'cpu_start', `Pro cpu start user code, fw ${this.opts.fw}`),
      () => this.#log('I', 'ionity', `config: wifi="${String(this.nvs.get('wifi_ssid') ?? '')}" host=${this.#hostDisplay()}`),
      () => {
        this.#booted = true;
        this.#announce();
        if (this.nvs.get('wifi_ssid')) this.#autoJoin();
      },
    ];
    this.#run(steps);
  }

  #run(steps: readonly (() => void)[]): void {
    let i = 0;
    const next = (): void => {
      const step = steps[i++];
      if (!step) return;
      this.#uptimeMs += 37;
      step();
      if (this.opts.pace > 0) timers.setTimeout(next, this.opts.pace);
      else next();
    };
    next();
  }

  #hostDisplay(): string {
    if (this.opts.prov === 2) return String(this.nvs.get('host_url') ?? '') || 'mdns:ionity-fleet.local';
    return String(this.nvs.get('server') ?? '') || 'mdns:ionity-fleet.local';
  }

  #announce(): void {
    this.#reply({ ok: true, op: 'hello', ...this.#info() });
  }

  #autoJoin(): void {
    const r = this.#join();
    if (r.ok) {
      this.#log('I', 'wifi', `connected to "${String(this.nvs.get('wifi_ssid'))}" ip=${this.#ip} rssi=${this.#rssi}`);
      if (this.#brokerOk()) {
        this.#mqtt = true;
        this.#log('I', 'mqtt', `connected to ${this.#hostDisplay()}`);
      } else {
        this.#log('W', 'mqtt', `cannot reach ${this.#hostDisplay()}`);
      }
    } else {
      this.#log('W', 'wifi', r.error);
    }
  }

  // ---------------------------------------------------------------- input
  /** Host -> board bytes. */
  receive(text: string): void {
    this.#rx += text;
    let nl: number;
    while ((nl = this.#rx.indexOf('\n')) >= 0) {
      const line = this.#rx.slice(0, nl).replace(/\r$/, '');
      this.#rx = this.#rx.slice(nl + 1);
      this.#handleLine(line);
    }
    const max = this.opts.prov === 2 ? PROV_V2_LINE_MAX : PROV_V1_LINE_MAX;
    if (this.#rx.length > max) {
      this.#rx = ''; // fw 2.x drops an overlong line silently (Provision.ino); fw 3 says so
      if (this.opts.prov === 2) this.#reply({ ok: false, op: 'error', error: 'line too long' });
    }
  }

  #handleLine(raw: string): void {
    if (!this.#booted || !raw.startsWith('{')) return;
    const max = this.opts.prov === 2 ? PROV_V2_LINE_MAX : PROV_V1_LINE_MAX;
    if (raw.length > max) {
      if (this.opts.prov === 2) this.#reply({ ok: false, op: 'error', error: 'line too long' });
      return;
    }
    let text = raw;
    const m = / \*([0-9a-f]{8})$/.exec(raw);
    if (m) {
      if (this.opts.prov === 1) return; // fw 2.x cannot parse the suffix: the line is ignored
      text = raw.slice(0, m.index);
      if (crc32Hex(text) !== m[1]) {
        this.#reply({ ok: false, op: 'error', error: 'crc mismatch' });
        return;
      }
    }
    let req: Json;
    try {
      req = JSON.parse(text) as Json;
    } catch {
      return;
    }
    if (req['ionity'] !== 'prov') return;
    this.received.push(req);
    const op = typeof req['op'] === 'string' ? req['op'] : '';
    const id = this.opts.prov === 2 && typeof req['id'] === 'number' ? { id: req['id'] } : {};
    switch (op) {
      case 'hello':
      case 'get':
        this.#reply({ ok: true, op, ...id, ...this.#info() });
        return;
      case 'set':
        this.#set(req, id);
        return;
      case 'scan':
        this.#reply({ ok: true, op, ...id, networks: this.opts.networks.map((n) => ({ ssid: n.ssid, rssi: n.rssi, ch: n.ch, open: n.pass === undefined })) });
        return;
      case 'test':
        this.#test(id);
        return;
      case 'reboot':
        this.#reply({ ok: true, op, ...id });
        this.#restart();
        return;
      case 'factory_reset':
        this.nvs.clear();
        this.#reply({ ok: true, op, ...id });
        this.#restart();
        return;
      default:
        this.#reply({ ok: false, op, ...id, error: 'unknown op' });
    }
  }

  // ---------------------------------------------------------------- ops
  #set(req: Json, id: Json): void {
    const v2 = this.opts.prov === 2;
    const allowed: ReadonlySet<string> = v2 ? new Set<string>(V2_FIELDS) : new Set<string>(V1_SET_FIELDS);
    const fail = (field: string, error: string) => this.#reply({ ok: false, op: 'set', ...id, error, ...(v2 ? { field } : {}) });
    // fw 3 contract: validate everything first, then write; fw 2.x wrote as it went.
    const ssid = req['ssid'];
    if (typeof ssid === 'string' && (ssid.length === 0 || ssid.length > 32)) return fail('ssid', 'ssid must be 1-32 chars');
    const pass = req['pass'];
    if (typeof pass === 'string' && pass.length > 0 && (pass.length < 8 || pass.length > (v2 ? 64 : 63))) return fail('pass', 'WPA2 password must be 8-63 chars (or empty for open)');
    if (v2) {
      // fw 3 contract: an unknown field is an error (fw 2.x ignored it), and nothing is written.
      for (const k of Object.keys(req)) {
        if (!ENVELOPE.has(k) && !allowed.has(k)) return fail(k, 'unknown field');
      }
    }
    const changed: string[] = [];
    let netChanged = false;
    for (const [k, v] of Object.entries(req)) {
      if (!allowed.has(k) || (typeof v !== 'string' && typeof v !== 'number')) continue;
      const key = k === 'ssid' ? 'wifi_ssid' : k === 'pass' ? 'wifi_pass' : k === 'device_uuid' ? 'dev_uuid' : k;
      this.nvs.set(key, v);
      changed.push(k);
      if (k === 'ssid' || k === 'pass') netChanged = true;
    }
    if (netChanged) {
      this.#wifiUp = false;
      this.#mqtt = false;
    }
    this.#reply({ ok: true, op: 'set', ...id, changed, reboot_required: changed.length > 0, ...this.#info() });
  }

  #join(): { ok: true } | { ok: false; error: string } {
    const ssid = String(this.nvs.get('wifi_ssid') ?? '');
    const pass = String(this.nvs.get('wifi_pass') ?? '');
    const net = this.opts.networks.find((n) => n.ssid === ssid);
    if (!net) return { ok: false, error: 'ssid not found (2.4 GHz only)' };
    if ((net.pass ?? '') !== pass) return { ok: false, error: 'wrong password or rejected' };
    this.#wifiUp = true;
    this.#ip = '192.168.0.42';
    this.#rssi = net.rssi;
    return { ok: true };
  }

  #brokerOk(): boolean {
    return this.#wifiUp && this.opts.brokerReachable && String(this.nvs.get('role') ?? 'node') === 'node';
  }

  #test(id: Json): void {
    if (!this.nvs.get('wifi_ssid')) {
      this.#reply({ ok: false, op: 'test', ...id, error: 'no ssid provisioned' });
      return;
    }
    const r = this.#join();
    if (!r.ok) {
      this.#log('W', 'wifi', r.error);
      this.#reply(this.opts.prov === 2
        ? { ok: false, op: 'test', ...id, wifi: { ok: false, error: r.error }, broker: { ok: false, detail: 'no Wi-Fi' } }
        : { ok: false, op: 'test', error: r.error });
      return;
    }
    this.#mqtt = this.#brokerOk();
    this.#log('I', 'wifi', `test: joined, ip=${this.#ip}`);
    if (this.opts.prov === 2) {
      this.#reply({
        ok: this.#mqtt,
        op: 'test',
        ...id,
        wifi: { ok: true, ip: this.#ip, rssi: this.#rssi },
        broker: { ok: this.#mqtt, detail: this.#mqtt ? `connected to ${this.#hostDisplay()}` : `cannot reach ${this.#hostDisplay()}` },
        board: { ok: true, op: 'get', ...this.#info() },
      });
    } else {
      this.#reply({ op: 'test', ok: true, ...this.#info(), mqtt: this.#mqtt });
    }
  }

  /** Hardware reset: EN pulsed low (RTS from the host, or the RESET button). */
  reset(): void {
    this.#restart();
  }

  #restart(): void {
    this.rebootCount++;
    this.#booted = false;
    this.#wifiUp = false;
    this.#mqtt = false;
    this.#uptimeMs = 0;
    const later = (fn: () => void, ms: number) => (this.opts.pace > 0 ? timers.setTimeout(fn, ms) : fn());
    later(() => {
      if (this.opts.nativeUsb) this.#io.detach?.();
      later(() => {
        if (this.opts.nativeUsb) this.#io.attach?.();
        this.boot();
      }, this.opts.nativeUsb ? 600 : 200);
    }, 150);
  }

  // ---------------------------------------------------------------- identity
  #info(): Json {
    const v2 = this.opts.prov === 2;
    const ssid = String(this.nvs.get('wifi_ssid') ?? '');
    const base: Json = {
      ...(v2 ? { prov: 2, line_max: PROV_V2_LINE_MAX, set_fields: V2_FIELDS, hwid: this.opts.hwid } : { device_id: this.opts.hwid }),
      fw: this.opts.fw,
      product: 'ionity-esp32-mcp-node',
      chip: this.opts.chip,
      chip_rev: 2,
      flash_mb: 16,
      mac: this.opts.hwid.slice(6).replace(/(..)(?!$)/g, '$1:'),
      provisioned: ssid.length > 0,
      ssid,
      has_pass: String(this.nvs.get('wifi_pass') ?? '').length > 0,
      role: String(this.nvs.get('role') ?? 'node'),
      site: String(this.nvs.get('site') ?? 'lab'),
      group: String(this.nvs.get('group') ?? 'bench'),
      label: String(this.nvs.get('label') ?? ''),
      has_mcp_token: this.nvs.has('mcp_token'),
      has_ota_pass: this.nvs.has('ota_pass'),
      mode: 'ACTIVE',
      wifi: this.#wifiUp ? 'connected' : 'down',
      mqtt: this.#mqtt,
    };
    if (v2) {
      base['host_url'] = String(this.nvs.get('host_url') ?? '');
      base['device_uuid'] = String(this.nvs.get('dev_uuid') ?? '');
      base['has_ca'] = this.nvs.has('ca_pem');
      base['has_mqtt_pass'] = this.nvs.has('mqtt_pass');
    } else {
      base['server'] = String(this.nvs.get('server') ?? '');
      base['mqtt_port'] = Number(this.nvs.get('mqtt_port') ?? 1883);
      base['http_port'] = Number(this.nvs.get('http_port') ?? 8099);
    }
    if (this.#wifiUp) {
      base['ip'] = this.#ip;
      base['rssi'] = this.#rssi;
      base['mcp_url'] = `http://${this.#ip}/mcp`;
    }
    return base;
  }

  /** For assertions: never contains secrets in clear (mirrors "has_*" reporting). */
  get publicNvs(): Record<string, string | number> {
    const out: Record<string, string | number> = {};
    for (const [k, v] of this.nvs) out[k] = SECRET_KEYS.has(k) || k === 'wifi_pass' ? '•••' : v;
    return out;
  }
}
