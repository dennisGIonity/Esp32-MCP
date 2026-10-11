/*
 * AEDI - IONITY GLOBAL | @ionity/ewp - Edge Wire Protocol v1 | frame.ts
 * Doc ID: DOC-2026-10-ESP32MCP-EWP1 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * The frame: a fixed 16-byte little-endian header + one CBOR body (RFC 8949).
 *
 *   0      1      2      3      4             8      9      10      12      14      16
 *   +------+------+------+------+-------------+------+------+-------+-------+-------+----------
 *   |magic | ver  | type |flags |  rid (u32)  | tool |status|deadln |bodyLen|reservd| CBOR body
 *   | 0xE5 | 0x01 |      |      |             |      |      | (u16) | (u16) | =0    | (bodyLen B)
 *   +------+------+------+------+-------------+------+------+-------+-------+-------+----------
 *
 * Why a binary header in front of CBOR:
 *  - The Core-0 network task routes a frame (validate, dedupe rid, check
 *    deadline, pick a pool slot, enqueue for Core 1) WITHOUT running a CBOR
 *    parser - constant time, no allocation, trivially bounds-checked.
 *  - Every field is naturally aligned (u32 at 4, u16 at even offsets), so the
 *    firmware may memcpy() the 16 bytes into `ewp_header_wire_t` - never cast
 *    the esp-mqtt buffer pointer directly (alignment is not guaranteed).
 *  - The same frame rides MQTT, a WebSocket binary message or (bench) a COBS
 *    serial link: transport-agnostic correlation (rid) and deadline.
 *
 * Validation order on the device (cheap -> expensive, reject early):
 *   len >= 16 -> magic -> version -> unknown flag bits -> reserved == 0 ->
 *   16 + bodyLen == payload length -> bodyLen <= limits.maxDownlink - 16 ->
 *   type allowed on this topic (CHANNEL_POLICY) -> tool registered ->
 *   rid not in dedupe window -> pool slot free (else ERR BUSY) -> enqueue.
 */
import type { EWP_MAGIC, EWP_VERSION } from './constants.ts';
import type { ErrorStatus, MsgType, Status, ToolId } from './ids.ts';
import type { NO_TOOL } from './ids.ts';
import type { U16, U32, U8 } from './scalars.ts';
import type { AckBody, CancelBody, ErrorBody, ResultBody, ToolArgs, ToolResult } from './tools.ts';
import type { TelemetryBody } from './telemetry.ts';
import type { HelloBody, StatusBody } from './hello.ts';
import type { CrashBody, EventBody, LogBody } from './events.ts';

export interface FrameHeader {
  readonly magic: typeof EWP_MAGIC;
  readonly version: typeof EWP_VERSION;
  readonly type: MsgType;
  /** FrameFlag bitset */
  readonly flags: U8;
  /**
   * Correlation id. Gateway-allocated for REQ/CANCEL as (instance:8 << 24) | counter:24,
   * so N horizontally-scaled gateways never collide on one device. Device-originated
   * frames (TELEMETRY, EVENT ...) carry their own per-boot sequence instead.
   */
  readonly rid: U32;
  readonly tool: ToolId | typeof NO_TOOL;
  readonly status: Status;
  /**
   * REQ only: relative budget in ms from receipt on the device (no clock sync
   * needed). 0 = EWP_LIMITS.defaultDeadlineMs. A job still queued when it runs
   * out is answered with DEADLINE_EXCEEDED and never executed.
   */
  readonly deadlineMs: U16;
  readonly bodyLen: U16;
  readonly reserved: 0;
}

export interface HeaderFieldSpec {
  readonly field: keyof FrameHeader;
  readonly offset: number;
  readonly size: 1 | 2 | 4;
}

/** Single source of the byte layout - consumed by the codec and by gen-c-header.ts. */
export const HEADER_LAYOUT = [
  { field: 'magic', offset: 0, size: 1 },
  { field: 'version', offset: 1, size: 1 },
  { field: 'type', offset: 2, size: 1 },
  { field: 'flags', offset: 3, size: 1 },
  { field: 'rid', offset: 4, size: 4 },
  { field: 'tool', offset: 8, size: 1 },
  { field: 'status', offset: 9, size: 1 },
  { field: 'deadlineMs', offset: 10, size: 2 },
  { field: 'bodyLen', offset: 12, size: 2 },
  { field: 'reserved', offset: 14, size: 2 },
] as const satisfies readonly HeaderFieldSpec[];

/** Compile-time: the layout names every header field exactly once. */
export type HeaderLayoutComplete =
  Exclude<keyof FrameHeader, (typeof HEADER_LAYOUT)[number]['field']> extends never ? true : never;

/* ------------------------------------------------------------------------- */
/* Typed frames                                                               */
/* ------------------------------------------------------------------------- */

type Header<T extends MsgType, Extra = unknown> = FrameHeader & { readonly type: T } & Extra;

/** One REQ variant per tool: header.tool and body are correlated, not independent. */
export type RequestFrame = {
  readonly [T in ToolId]: {
    readonly header: Header<typeof MsgType.REQ, { readonly tool: T; readonly status: typeof Status.OK }>;
    readonly body: ToolArgs<T>;
  };
}[ToolId];

export type ResultFrame = {
  readonly [T in ToolId]: {
    readonly header: Header<typeof MsgType.RES, { readonly tool: T; readonly status: typeof Status.OK }>;
    readonly body: ResultBody<ToolResult<T>>;
  };
}[ToolId];

export interface AckFrame {
  readonly header: Header<typeof MsgType.ACK, { readonly tool: ToolId; readonly status: typeof Status.ACCEPTED }>;
  readonly body: AckBody;
}

export interface ErrorFrame {
  /** tool = NO_TOOL when the frame was rejected before the tool id could be trusted */
  readonly header: Header<typeof MsgType.ERR, { readonly status: ErrorStatus }>;
  readonly body: ErrorBody;
}

export interface CancelFrame {
  readonly header: Header<typeof MsgType.CANCEL, { readonly status: typeof Status.OK }>;
  readonly body: CancelBody;
}

export interface TelemetryFrame {
  readonly header: Header<typeof MsgType.TELEMETRY, { readonly tool: typeof NO_TOOL }>;
  readonly body: TelemetryBody;
}
export interface EventFrame {
  readonly header: Header<typeof MsgType.EVENT, { readonly tool: typeof NO_TOOL }>;
  readonly body: EventBody;
}
export interface LogFrame {
  readonly header: Header<typeof MsgType.LOG, { readonly tool: typeof NO_TOOL }>;
  readonly body: LogBody;
}
export interface CrashFrame {
  readonly header: Header<typeof MsgType.CRASH, { readonly tool: typeof NO_TOOL }>;
  readonly body: CrashBody;
}
export interface HelloFrame {
  readonly header: Header<typeof MsgType.HELLO, { readonly tool: typeof NO_TOOL }>;
  readonly body: HelloBody;
}
export interface StatusFrame {
  readonly header: Header<typeof MsgType.STATUS, { readonly tool: typeof NO_TOOL }>;
  readonly body: StatusBody;
}

/** What the GATEWAY may publish. An encoder typed on this cannot emit device frames. */
export type DownlinkFrame = RequestFrame | CancelFrame;

/** What the DEVICE may publish. */
export type UplinkFrame =
  | AckFrame
  | ResultFrame
  | ErrorFrame
  | TelemetryFrame
  | EventFrame
  | LogFrame
  | CrashFrame
  | HelloFrame
  | StatusFrame;

export type Frame = DownlinkFrame | UplinkFrame;

/** Narrow a RES by tool id: `ResultOf<typeof ToolId.RUN_INFERENCE>` -> its frame. */
export type ResultOf<T extends ToolId> = Extract<ResultFrame, { readonly header: { readonly tool: T } }>;
export type RequestOf<T extends ToolId> = Extract<RequestFrame, { readonly header: { readonly tool: T } }>;
