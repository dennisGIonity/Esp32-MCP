/*
 * AEDI - IONITY GLOBAL | @ionity/ewp - Edge Wire Protocol v1 | tools.ts
 * Doc ID: DOC-2026-10-ESP32MCP-EWP1 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Edge MCP tool registry - the request/result body of every tool, as it
 * travels between the gateway and the ESP32 (NOT the LLM-facing MCP schema:
 * the gateway owns names, units and labels; the wire carries handles).
 *
 *   LLM sees:  run_inference({ device: "riser-gf", model_id: "vibration_anomaly",
 *                              input_frame: { source: "live", window_ms: 1000 } })
 *   wire:      REQ tool=0x03 body {1: 3, 2: {1: 0, 2: 1000}}            (~10 B)
 */
import { defineKeys } from './keys.ts';
import type { InputSource, StateMode, TensorDType } from './ids.ts';
import { ToolId } from './ids.ts';
import type { Bytes, F32, FixedBytes, I32, U16, U32, U8, Unit, Utf8 } from './scalars.ts';
import type { HelloBody } from './hello.ts';
import type { TelemetryBody } from './telemetry.ts';

/** Object with no fields - encodes as an empty CBOR map (1 byte: 0xA0). */
export type EmptyBody = Readonly<Record<never, never>>;

/* ------------------------------------------------------------------------- */
/* Envelope shared by every RES                                               */
/* ------------------------------------------------------------------------- */

/**
 * Device-side timing, measured with esp_timer_get_time() (monotonic, 1 us).
 * The gateway turns these durations into child spans of the MCP request; it
 * never trusts the device wall clock (no RTC, SNTP may not have synced).
 */
export interface ExecTiming {
  /** time from frame receipt (net task) until a worker picked the job up */
  readonly queueUs: U32;
  /** tool handler execution time on the worker core */
  readonly execUs: U32;
  /** internal-RAM free after minus before the handler (negative = possible leak) */
  readonly heapDelta: I32;
}
export const ExecTimingKey = defineKeys<ExecTiming>()({ queueUs: 1, execUs: 2, heapDelta: 3 });

export interface ResultBody<R> {
  readonly timing: ExecTiming;
  readonly result: R;
}
export const ResultBodyKey = defineKeys<ResultBody<unknown>>()({ timing: 1, result: 2 });

/** ACK - the job is queued; lets the gateway tell "device offline" from "model still running". */
export interface AckBody {
  /** jobs ahead of this one, including the one executing */
  readonly queueDepth: U8;
  /** device's own estimate, from the tool's moving average; absent if unknown */
  readonly etaMs?: U32;
}
export const AckBodyKey = defineKeys<AckBody>()({ queueDepth: 1, etaMs: 2 });

export interface ErrorBody {
  /** short human-readable detail; surfaced to the LLM as the tool error text */
  readonly detail?: Utf8<96>;
  /** CBOR key of the offending argument (INVALID_ARGS) so the gateway can name it */
  readonly argKey?: U8;
  /** BUSY / RATE_LIMITED / NOT_READY: earliest sensible retry */
  readonly retryAfterMs?: U32;
  /** partial timing when the failure happened mid-execution */
  readonly timing?: ExecTiming;
}
export const ErrorBodyKey = defineKeys<ErrorBody>()({ detail: 1, argKey: 2, retryAfterMs: 3, timing: 4 });

/** CANCEL - the target is the header rid; the body only says why. */
export interface CancelBody {
  /** 1 = MCP client cancelled, 2 = gateway deadline, 3 = superseded */
  readonly reason?: U8;
}
export const CancelBodyKey = defineKeys<CancelBody>()({ reason: 1 });

/* ------------------------------------------------------------------------- */
/* 0x01 get_device_info                                                       */
/* ------------------------------------------------------------------------- */

/** Re-reads the capabilities manifest (same shape as the retained HELLO). */
export type DeviceInfoResult = HelloBody;

/* ------------------------------------------------------------------------- */
/* 0x02 read_telemetry                                                        */
/* ------------------------------------------------------------------------- */

export interface ReadTelemetryArgs {
  /** TelemetrySection bitmask (ids.ts); absent = HEAP|NET|CPU|ACTUATORS (bounded, < 300 B) */
  readonly sections?: U8;
}
export const ReadTelemetryArgsKey = defineKeys<ReadTelemetryArgs>()({ sections: 1 });

/** A live snapshot is the telemetry frame body: one decoder, one Datadog mapping. */
export type ReadTelemetryResult = TelemetryBody;

/* ------------------------------------------------------------------------- */
/* 0x03 run_inference                                                         */
/* ------------------------------------------------------------------------- */

/** Tensor shape, rank 1..4 (EWP_LIMITS.maxTensorRank). */
export type TensorShape =
  | readonly [d0: U16]
  | readonly [d0: U16, d1: U16]
  | readonly [d0: U16, d1: U16, d2: U16]
  | readonly [d0: U16, d1: U16, d2: U16, d3: U16];

/**
 * Replaces the spec's `input_frame: any`. An MCU cannot accept an unbounded,
 * untyped blob: every variant here has a hard upper bound the firmware can
 * pre-allocate for.
 *
 * INVARIANT (runtime-checked by gateway AND device, not expressible in TS):
 *   INLINE: product(shape) * TENSOR_DTYPE_BYTES[dtype] === data.byteLength
 */
export type InferenceInput =
  | {
      readonly source: typeof InputSource.LIVE;
      /** capture window from the model's bound sensor (I2S mic, IMU, CSI, ADC ring) */
      readonly windowMs: U16;
    }
  | {
      readonly source: typeof InputSource.INLINE;
      readonly dtype: TensorDType;
      readonly shape: TensorShape;
      /** raw little-endian tensor; <= EWP_LIMITS.inlineTensorBytes */
      readonly data: Bytes<2048>;
    }
  | {
      readonly source: typeof InputSource.BLOB;
      /** handle returned by the (later-phase) chunked blob upload */
      readonly blobId: U32;
      /** device verifies before use; mismatch -> INVALID_ARGS */
      readonly sha256: FixedBytes<32>;
    };
export const InferenceInputKey = defineKeys<InferenceInput>()({
  source: 1,
  windowMs: 2,
  dtype: 3,
  shape: 4,
  data: 5,
  blobId: 6,
  sha256: 7,
});

export interface RunInferenceArgs {
  /** model handle from HELLO.models[].id - the gateway resolves "vibration_anomaly" -> 3 */
  readonly model: U8;
  readonly input: InferenceInput;
  /** classes to return, 1..EWP_LIMITS.topK (default 3) */
  readonly topK?: U8;
  /** override the model's decision threshold for this call */
  readonly threshold?: F32;
}
export const RunInferenceArgsKey = defineKeys<RunInferenceArgs>()({ model: 1, input: 2, topK: 3, threshold: 4 });

/** [class index, probability 0..1] - a 2-tuple, not a map: ~7 B per class on the wire. */
export type ClassScore = readonly [cls: U8, p: Unit];
/** [preprocess us, invoke us, postprocess us] */
export type InferenceStages = readonly [preUs: U32, invokeUs: U32, postUs: U32];

export interface InferenceResult {
  readonly model: U8;
  /** first 8 bytes of the model file SHA-256; the gateway maps it to labels + version */
  readonly modelHash: FixedBytes<8>;
  /** sorted by p descending, length <= topK; empty for ANOMALY/REGRESSOR models */
  readonly classes: readonly ClassScore[];
  /** raw model score (z-score, reconstruction error, regression output) */
  readonly score?: F32;
  /** decision after threshold */
  readonly positive: boolean;
  readonly stages: InferenceStages;
  /** tensor-arena high-water mark in PSRAM - memory regression signal per model version */
  readonly arenaPeakBytes: U32;
}
export const InferenceResultKey = defineKeys<InferenceResult>()({
  model: 1,
  modelHash: 2,
  classes: 3,
  score: 4,
  positive: 5,
  stages: 6,
  arenaPeakBytes: 7,
});

/* ------------------------------------------------------------------------- */
/* 0x04 set_actuator                                                          */
/* ------------------------------------------------------------------------- */

export interface SetActuatorArgs {
  /** index into HELLO.actuators - the gateway resolves "pwm0" / "relay0" -> index */
  readonly channel: U8;
  /** normalised: PWM/SDM duty 0..1; RELAY/GPIO exactly 0 or 1 (device rejects 0.5 on a relay) */
  readonly value: Unit;
  /** linear ramp from the current value (PWM/SDM only) - soft start for motors/pumps */
  readonly rampMs?: U16;
  /**
   * Dead-man lease: unless renewed, the channel reverts to its HELLO `safe`
   * value after this many ms. An LLM that switches a heater on and then loses
   * its session must not leave the heater on forever. <= EWP_LIMITS.leaseMaxMs.
   */
  readonly leaseMs?: U32;
}
export const SetActuatorArgsKey = defineKeys<SetActuatorArgs>()({ channel: 1, value: 2, rampMs: 3, leaseMs: 4 });

export interface SetActuatorResult {
  readonly channel: U8;
  /** value actually applied after quantisation to the LEDC/SDM resolution */
  readonly applied: Unit;
  readonly previous: Unit;
  readonly leaseRemainingMs?: U32;
}
export const SetActuatorResultKey = defineKeys<SetActuatorResult>()({
  channel: 1,
  applied: 2,
  previous: 3,
  leaseRemainingMs: 4,
});

/* ------------------------------------------------------------------------- */
/* 0x05 set_state_mode                                                        */
/* ------------------------------------------------------------------------- */

/** Each mode carries exactly the parameters it needs - nothing optional-but-required. */
export type SetStateModeArgs =
  | { readonly mode: typeof StateMode.ACTIVE }
  | { readonly mode: typeof StateMode.STANDBY }
  | {
      readonly mode: typeof StateMode.INFERENCE_ACTIVE;
      readonly model: U8;
      /** run the model every periodMs (default 2000, fw 2.x behaviour) */
      readonly periodMs?: U16;
    }
  | {
      readonly mode: typeof StateMode.LOW_POWER_SLEEP;
      /** EWP_LIMITS.sleepMinS..sleepMaxS; device replies FIRST, then sleeps */
      readonly durationS: U32;
    }
  | {
      readonly mode: typeof StateMode.FAILSAFE;
      /** all actuators -> safe value and locked; latched in NVS across reboots */
      readonly reason?: Utf8<48>;
    };
export const SetStateModeArgsKey = defineKeys<SetStateModeArgs>()({
  mode: 1,
  model: 2,
  periodMs: 3,
  durationS: 4,
  reason: 5,
});

export interface SetStateModeResult {
  readonly previous: StateMode;
  readonly current: StateMode;
  /** LOW_POWER_SLEEP: the device flushes uplink, publishes STATUS=SLEEPING, then sleeps after this */
  readonly effectiveInMs: U16;
  /** true when the mode survives reboot (FAILSAFE) */
  readonly latched: boolean;
}
export const SetStateModeResultKey = defineKeys<SetStateModeResult>()({
  previous: 1,
  current: 2,
  effectiveInMs: 3,
  latched: 4,
});

/* ------------------------------------------------------------------------- */
/* 0x06 identify                                                              */
/* ------------------------------------------------------------------------- */

export interface IdentifyArgs {
  /** blink/flash duration, default 1200 ms */
  readonly durationMs?: U16;
}
export const IdentifyArgsKey = defineKeys<IdentifyArgs>()({ durationMs: 1 });

/* ------------------------------------------------------------------------- */
/* The registry: ToolId -> { args, result }                                   */
/* ------------------------------------------------------------------------- */

/**
 * Adding a tool = add an id in ids.ts + one entry here. Every encoder, the
 * frame unions and the C header generator are driven from this map, so a tool
 * without a contract cannot be sent and a contract without an id cannot exist.
 */
export interface ToolContract {
  readonly [ToolId.GET_DEVICE_INFO]: { readonly args: EmptyBody; readonly result: DeviceInfoResult };
  readonly [ToolId.READ_TELEMETRY]: { readonly args: ReadTelemetryArgs; readonly result: ReadTelemetryResult };
  readonly [ToolId.RUN_INFERENCE]: { readonly args: RunInferenceArgs; readonly result: InferenceResult };
  readonly [ToolId.SET_ACTUATOR]: { readonly args: SetActuatorArgs; readonly result: SetActuatorResult };
  readonly [ToolId.SET_STATE_MODE]: { readonly args: SetStateModeArgs; readonly result: SetStateModeResult };
  readonly [ToolId.IDENTIFY]: { readonly args: IdentifyArgs; readonly result: EmptyBody };
}

export type ToolArgs<T extends ToolId> = ToolContract[T]['args'];
export type ToolResult<T extends ToolId> = ToolContract[T]['result'];

/** Compile-time proof that ToolContract covers every ToolId and nothing else. */
type _ContractCoversIds = [Exclude<ToolId, keyof ToolContract>, Exclude<keyof ToolContract, ToolId>];
export type ContractCoverage = _ContractCoversIds extends [never, never] ? true : never;
