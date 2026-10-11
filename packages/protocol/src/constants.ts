/*
 * AEDI - IONITY GLOBAL | @ionity/ewp - Edge Wire Protocol v1 | constants.ts
 * Doc ID: DOC-2026-10-ESP32MCP-EWP1 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Framing constants, hard limits and the MQTT topic map.
 */
import { MsgType } from './ids.ts';
import type { Brand } from './scalars.ts';

/** Not '{' (0x7B, legacy JSON envelope), not '[' and not a CBOR map head (0xA0-0xBF). */
export const EWP_MAGIC = 0xe5;
export const EWP_VERSION = 1;
export const EWP_HEADER_LEN = 16;

/**
 * Hard limits. The firmware allocates its buffers from these numbers, so they
 * are part of the contract - raising one is a protocol change, not a tweak.
 *
 * MEMORY-SAFETY: `downlinkFrame` sizes the esp-mqtt RX buffer and every slot of
 * the job pool (maxInflight x downlinkFrame bytes, allocated once at boot).
 * The gateway MUST refuse to encode anything larger - the device would drop it
 * with TOO_LARGE after esp-mqtt already spent heap reassembling it.
 */
export const EWP_LIMITS = {
  /** gateway -> device, header + body */
  downlinkFrame: 4096,
  /** device -> gateway, header + body */
  uplinkFrame: 8192,
  /** jobs queued + executing on the device at once (pool slots) */
  maxInflight: 4,
  /** RUN_INFERENCE InputSource.INLINE payload */
  inlineTensorBytes: 2048,
  /** tensor rank */
  maxTensorRank: 4,
  topK: 8,
  actuatorChannels: 16,
  models: 8,
  customMetrics: 32,
  taskStats: 12,
  backtraceDepth: 16,
  /** default REQ budget when the gateway does not specify one */
  defaultDeadlineMs: 5000,
  /** sleep bounds (fw 2.x compatible) */
  sleepMinS: 5,
  sleepMaxS: 86_400,
  /** a lease longer than a day is a configuration, not a lease */
  leaseMaxMs: 86_400_000,
} as const;

/* ------------------------------------------------------------------------- */
/* Topics                                                                     */
/* ------------------------------------------------------------------------- */

/**
 * `ionity/ewp1/...` lives beside the fw 2.x JSON topics (`ionity/<site>/<id>/...`)
 * so both generations run on one broker during migration. The legacy
 * subscription `ionity/+/+/telemetry` cannot match a 5-level EWP topic.
 */
export const TOPIC_ROOT = 'ionity/ewp1';

export const Channel = {
  DOWN: 'dn',
  RESULT: 'rs',
  TELEMETRY: 'tm',
  EVENT: 'ev',
  HELLO: 'hello',
  STATUS: 'st',
} as const;
export type Channel = (typeof Channel)[keyof typeof Channel];

/** `esp32-` + 12 lowercase hex of the base MAC (fw 2.x device_id). Also the MQTT client id. */
export type HwId = Brand<string, 'hwid'>;
/** MQTT topic level: [a-z0-9-], 1..32 chars. */
export type SiteId = Brand<string, 'site'>;

const HWID_RE = /^esp32-[0-9a-f]{12}$/;
const SITE_RE = /^[a-z0-9][a-z0-9-]{0,31}$/;

export function hwid(s: string): HwId {
  if (!HWID_RE.test(s)) throw new RangeError(`hwid: '${s}' does not match ${HWID_RE.source}`);
  return s as HwId;
}

/** Topic levels must never carry MQTT wildcards or separators ('+', '#', '/'). */
export function siteId(s: string): SiteId {
  if (!SITE_RE.test(s)) throw new RangeError(`site: '${s}' does not match ${SITE_RE.source}`);
  return s as SiteId;
}

export type Topic<C extends Channel = Channel> = `${typeof TOPIC_ROOT}/${string}/${string}/${C}`;

export function topic<C extends Channel>(site: SiteId, id: HwId, ch: C): Topic<C> {
  return `${TOPIC_ROOT}/${site}/${id}/${ch}`;
}

/** Fleet-wide downlink. Only FrameFlag.BROADCAST requests may be published here. */
export const BROADCAST_TOPIC = `${TOPIC_ROOT}/_all/dn` as const;

export interface ChannelPolicy {
  readonly dir: 'down' | 'up';
  readonly qos: 0 | 1;
  readonly retain: boolean;
  readonly msgTypes: readonly MsgType[];
}

/**
 * One table drives the broker ACL generator, the gateway's ingress validation
 * (a TELEMETRY frame arriving on `rs` is dropped and counted) and the firmware
 * publisher. Devices connect with a CLEAN session (MQTT 5: clean start + session
 * expiry 0) so the broker never queues QoS 1 commands for an offline device and
 * replays a stale "relay ON" an hour later. MQTT 5 message-expiry on `dn` is
 * set from the REQ deadline as a second line of defence.
 */
export const CHANNEL_POLICY = {
  dn: { dir: 'down', qos: 1, retain: false, msgTypes: [MsgType.REQ, MsgType.CANCEL] },
  rs: { dir: 'up', qos: 1, retain: false, msgTypes: [MsgType.ACK, MsgType.RES, MsgType.ERR] },
  tm: { dir: 'up', qos: 0, retain: false, msgTypes: [MsgType.TELEMETRY] },
  ev: { dir: 'up', qos: 1, retain: false, msgTypes: [MsgType.EVENT, MsgType.LOG, MsgType.CRASH] },
  hello: { dir: 'up', qos: 1, retain: true, msgTypes: [MsgType.HELLO] },
  st: { dir: 'up', qos: 1, retain: true, msgTypes: [MsgType.STATUS] },
} as const satisfies Record<Channel, ChannelPolicy>;
