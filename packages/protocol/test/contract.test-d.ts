/*
 * AEDI - IONITY GLOBAL | @ionity/ewp | compile-time contract tests | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Type-level tests: `tsc -p tsconfig.test.json` FAILS if any line marked
 * @ts-expect-error stops being an error, i.e. if the contract gets looser.
 */
import {
  Channel,
  CHANNEL_POLICY,
  defineKeys,
  EWP_MAGIC,
  EWP_VERSION,
  fixedBytes,
  InputSource,
  MsgType,
  Status,
  StateMode,
  ToolId,
  topic,
  hwid,
  siteId,
  u16,
  u32,
  u8,
  unit,
  type ContractCoverage,
  type DownlinkFrame,
  type HeaderLayoutComplete,
  type RequestFrame,
  type RequestOf,
  type ResultOf,
  type RunInferenceArgs,
  type SetStateModeArgs,
  type TelemetryFrame,
} from '../src/index.ts';

// --- registry & layout completeness ----------------------------------------
export const coverage: ContractCoverage = true;
export const layout: HeaderLayoutComplete = true;

// --- a well-formed run_inference REQ ------------------------------------------
const header = {
  magic: EWP_MAGIC,
  version: EWP_VERSION,
  type: MsgType.REQ,
  flags: u8(0b0000_0001), // ACK_REQUESTED
  rid: u32(0x0100_002a),
  tool: ToolId.RUN_INFERENCE,
  status: Status.OK,
  deadlineMs: u16(3000),
  bodyLen: u16(10),
  reserved: 0,
} as const;

export const req: RequestOf<typeof ToolId.RUN_INFERENCE> = {
  header,
  body: { model: u8(3), input: { source: InputSource.LIVE, windowMs: u16(1000) }, topK: u8(3) },
};
export const asDownlink: DownlinkFrame = req;

// --- the wire is branded: raw numbers are rejected ----------------------------
export const badRid: RequestFrame = {
  // @ts-expect-error  rid must be U32 (range-checked), not a bare number
  header: { ...header, rid: 42 },
  body: { model: u8(3), input: { source: InputSource.LIVE, windowMs: u16(1000) } },
};

// --- tool id and body are correlated -------------------------------------------
// @ts-expect-error  a set_actuator body under tool=RUN_INFERENCE
export const wrongBody: RequestFrame = {
  header,
  body: { channel: u8(0), value: unit(0.5) },
};

// --- `input_frame: any` is gone: each source carries exactly its fields ------
export const missingWindow: RunInferenceArgs = {
  model: u8(1),
  // @ts-expect-error  LIVE needs windowMs
  input: { source: InputSource.LIVE },
};
export const inlineNeedsShape: RunInferenceArgs = {
  model: u8(1),
  // @ts-expect-error  INLINE needs dtype + shape + data
  input: { source: InputSource.INLINE, data: fixedBytes(new Uint8Array(4), 4) },
};

// --- exactOptionalPropertyTypes: "absent" and "undefined" differ on the wire --
// @ts-expect-error  optional means omit the CBOR key, never encode undefined
export const explicitUndefined: RunInferenceArgs = {
  model: u8(1),
  input: { source: InputSource.LIVE, windowMs: u16(500) },
  topK: undefined,
};

// --- each mode carries exactly its parameters --------------------------------
// @ts-expect-error  LOW_POWER_SLEEP without durationS
export const sleepNoDuration: SetStateModeArgs = { mode: StateMode.LOW_POWER_SLEEP };
export const sleepOk: SetStateModeArgs = { mode: StateMode.LOW_POWER_SLEEP, durationS: u32(300) };

// --- direction: the gateway cannot publish device frames ----------------------
declare const tm: TelemetryFrame;
// @ts-expect-error  TELEMETRY is uplink-only
export const gatewaySendsTelemetry: DownlinkFrame = tm;

// --- RES narrowing by tool -----------------------------------------------------
declare const res: ResultOf<typeof ToolId.RUN_INFERENCE>;
export const topClassP: number | undefined = res.body.result.classes[0]?.[1];

// --- topics are typed per channel --------------------------------------------
export const dn: `ionity/ewp1/${string}/${string}/dn` = topic(siteId('office'), hwid('esp32-98a316e5d18c'), Channel.DOWN);
export const dnQos: 1 = CHANNEL_POLICY.dn.qos;

// --- defineKeys: completeness, uniqueness, no extras, 1-byte keys -------------
interface Demo {
  readonly a: number;
  readonly b?: number;
}
export const okKeys = defineKeys<Demo>()({ a: 1, b: 2 });
// @ts-expect-error  missing optional field b
export const missingKey = defineKeys<Demo>()({ a: 1 });
// @ts-expect-error  duplicate key 1
export const dupKey = defineKeys<Demo>()({ a: 1, b: 1 });
// @ts-expect-error  field c does not exist on Demo
export const extraKey = defineKeys<Demo>()({ a: 1, b: 2, c: 3 });
// @ts-expect-error  key 24 would need two bytes in CBOR
export const wideKey = defineKeys<Demo>()({ a: 1, b: 24 });
