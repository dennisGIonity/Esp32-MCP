/*
 * AEDI - IONITY GLOBAL | @ionity/ewp - Edge Wire Protocol v1 | ids.ts
 * Doc ID: DOC-2026-10-ESP32MCP-EWP1 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * Every numeric identifier that crosses the wire.
 *
 * Rules
 *  - `as const` objects + derived union types, never TS `enum`: erasable syntax
 *    (runs under Node type-stripping) and 1:1 mappable to C by gen-c-header.ts.
 *  - Values are part of the wire contract. NEVER renumber; only append.
 *  - Protocol enums never reuse ESP-IDF enum values (esp_reset_reason_t,
 *    esp_chip_model_t ...). IDF may reorder/extend those between releases; the
 *    firmware maps IDF -> EWP in one switch statement instead.
 */

type ValueOf<T> = T[keyof T];

/** Frame kind - byte 2 of the header. */
export const MsgType = {
  /** gateway -> device: invoke a tool */
  REQ: 0x01,
  /** device -> gateway: request accepted and queued (only if FrameFlag.ACK_REQUESTED) */
  ACK: 0x02,
  /** device -> gateway: tool finished, body = ResultBody<T> */
  RES: 0x03,
  /** device -> gateway: request rejected or tool failed, body = ErrorBody */
  ERR: 0x04,
  /** gateway -> device: cancel the request whose rid is in the header */
  CANCEL: 0x05,
  /** device -> gateway: periodic telemetry sample */
  TELEMETRY: 0x10,
  /** device -> gateway: asynchronous domain event (inference positive, lease expired ...) */
  EVENT: 0x11,
  /** device -> gateway: rate-limited log line (WARN and above by default) */
  LOG: 0x12,
  /** device -> gateway: core-dump summary of the previous boot's panic */
  CRASH: 0x13,
  /** device -> gateway: capabilities manifest, retained */
  HELLO: 0x20,
  /** device -> gateway: presence (also the MQTT Last Will), retained */
  STATUS: 0x21,
} as const;
export type MsgType = ValueOf<typeof MsgType>;

/** Header byte 3. Unknown bits MUST be zero; receivers reject frames that set them. */
export const FrameFlag = {
  /** REQ: device must publish ACK when the job is queued (long-running tools). */
  ACK_REQUESTED: 1 << 0,
  /** REQ: fleet broadcast - device delays its reply by a random 0..2 s to avoid a response storm. */
  BROADCAST: 1 << 1,
  /** REQ: jump the job queue (FAILSAFE / emergency stop path). */
  PRIORITY: 1 << 2,
  /** TELEMETRY/EVENT/LOG: replayed from the offline buffer - `ageMs` is meaningful. */
  BUFFERED: 1 << 3,
  /** RES/LOG: the body was truncated by the device to fit its uplink limit. */
  TRUNCATED: 1 << 4,
} as const;
export type FrameFlag = ValueOf<typeof FrameFlag>;
export const FRAME_FLAGS_KNOWN_MASK = 0b0001_1111;

/** Header byte 8. 0x00 = frame is not tool-related. */
export const ToolId = {
  GET_DEVICE_INFO: 0x01,
  READ_TELEMETRY: 0x02,
  RUN_INFERENCE: 0x03,
  SET_ACTUATOR: 0x04,
  SET_STATE_MODE: 0x05,
  IDENTIFY: 0x06,
} as const;
export type ToolId = ValueOf<typeof ToolId>;
export const NO_TOOL = 0x00;

/**
 * Allocation plan for tool ids (inclusive ranges). NetObserve is PLAN-v3
 * (get_dns_activity, query_dns_log, get_network_stats, get_local_devices,
 * set_flag_rules, get_flag_rules) - ids are reserved here, bodies are defined
 * in that phase so the v3 work never collides with the edge-AI tools.
 */
export const TOOL_ID_RANGE = {
  core: [0x01, 0x3f],
  netObserve: [0x40, 0x5f],
  edgeAi: [0x60, 0x7f],
  vendor: [0x80, 0xef],
  system: [0xf0, 0xfe],
} as const;

/** LLM-facing names. The gateway exposes these via MCP; the wire carries only the id. */
export const TOOL_NAME = {
  [ToolId.GET_DEVICE_INFO]: 'get_device_info',
  [ToolId.READ_TELEMETRY]: 'read_telemetry',
  [ToolId.RUN_INFERENCE]: 'run_inference',
  [ToolId.SET_ACTUATOR]: 'set_actuator',
  [ToolId.SET_STATE_MODE]: 'set_state_mode',
  [ToolId.IDENTIFY]: 'identify',
} as const satisfies Record<ToolId, string>;

/** Bitset carried per tool in HELLO.tools - drives MCP annotations and confirmation gates. */
export const ToolFlag = {
  READ_ONLY: 1 << 0,
  /** Changes the physical world (relays, PWM, sleep). Gateway requires confirmation (MRTR) by policy. */
  DESTRUCTIVE: 1 << 1,
  /** May exceed 1 s - gateway sets ACK_REQUESTED and may use the MCP tasks extension. */
  LONG_RUNNING: 1 << 2,
  /** Safe to retry with the same arguments. */
  IDEMPOTENT: 1 << 3,
} as const;
export type ToolFlag = ValueOf<typeof ToolFlag>;

/** Header byte 9. 0x00 OK / 0x01 ACCEPTED are the only non-error values. */
export const Status = {
  OK: 0x00,
  ACCEPTED: 0x01,
  // --- request problems (the gateway built a bad frame) ---
  BAD_FRAME: 0x10,
  UNSUPPORTED_VERSION: 0x11,
  UNKNOWN_TOOL: 0x12,
  INVALID_ARGS: 0x13,
  TOO_LARGE: 0x14,
  DUPLICATE: 0x15,
  // --- device state (try later / change state first) ---
  BUSY: 0x20,
  DEADLINE_EXCEEDED: 0x21,
  CANCELLED: 0x22,
  FAILSAFE_LOCKED: 0x23,
  NOT_READY: 0x24,
  UNAUTHORIZED: 0x25,
  RATE_LIMITED: 0x26,
  // --- execution faults ---
  TOOL_FAULT: 0x30,
  OUT_OF_MEMORY: 0x31,
  HW_FAULT: 0x32,
  MODEL_ERROR: 0x33,
  INTERNAL: 0x3f,
} as const;
export type Status = ValueOf<typeof Status>;
export type ErrorStatus = Exclude<Status, typeof Status.OK | typeof Status.ACCEPTED>;

/** The gateway may retry these automatically (bounded, with ErrorBody.retryAfterMs). */
export const RETRYABLE_STATUS: ReadonlySet<ErrorStatus> = new Set<ErrorStatus>([
  Status.BUSY,
  Status.DEADLINE_EXCEEDED,
  Status.NOT_READY,
  Status.RATE_LIMITED,
]);

/** Values identical to fw 2.x `enum StateMode` (NodeState.h) - NVS-latched values stay valid. */
export const StateMode = {
  ACTIVE: 0,
  STANDBY: 1,
  INFERENCE_ACTIVE: 2,
  LOW_POWER_SLEEP: 3,
  FAILSAFE: 4,
} as const;
export type StateMode = ValueOf<typeof StateMode>;

/** ESP32-S3 and ESP32-C6 have NO DAC peripheral: analog-ish outputs are SDM or LEDC + RC filter. */
export const ActuatorKind = {
  PWM: 1,
  RELAY: 2,
  GPIO: 3,
  /** sigma-delta modulator output (needs an external RC low-pass) */
  SDM: 4,
  /** true DAC - ESP32 classic / ESP32-S2 only */
  DAC: 5,
} as const;
export type ActuatorKind = ValueOf<typeof ActuatorKind>;

export const InputSource = {
  /** device captures its own sensor window (the normal case) */
  LIVE: 0,
  /** tensor bytes travel inside the REQ (<= EWP_LIMITS.inlineTensorBytes) */
  INLINE: 1,
  /** tensor previously staged on the device (chunked upload, later phase) */
  BLOB: 2,
} as const;
export type InputSource = ValueOf<typeof InputSource>;

export const TensorDType = {
  I8: 0,
  U8: 1,
  I16: 2,
  F32: 3,
} as const;
export type TensorDType = ValueOf<typeof TensorDType>;
export const TENSOR_DTYPE_BYTES = { 0: 1, 1: 1, 2: 2, 3: 4 } as const satisfies Record<TensorDType, number>;

export const ModelKind = {
  CLASSIFIER: 1,
  ANOMALY: 2,
  REGRESSOR: 3,
  /** classic DSP / statistics (zscore, rssi_motion, threshold) - no tensor arena */
  STATISTICAL: 4,
} as const;
export type ModelKind = ValueOf<typeof ModelKind>;

/** Mirrors esp_log_level_t ordering so the firmware maps 1:1. */
export const LogLevel = {
  ERROR: 1,
  WARN: 2,
  INFO: 3,
  DEBUG: 4,
  VERBOSE: 5,
} as const;
export type LogLevel = ValueOf<typeof LogLevel>;

/** Stable EWP numbering - the firmware maps esp_reset_reason() onto this. */
export const ResetReason = {
  UNKNOWN: 0,
  POWER_ON: 1,
  EXTERNAL: 2,
  SOFTWARE: 3,
  PANIC: 4,
  INT_WDT: 5,
  TASK_WDT: 6,
  OTHER_WDT: 7,
  DEEP_SLEEP: 8,
  BROWNOUT: 9,
  USB: 10,
  JTAG: 11,
  EFUSE: 12,
  POWER_GLITCH: 13,
  CPU_LOCKUP: 14,
  OTA_ROLLBACK: 15,
} as const;
export type ResetReason = ValueOf<typeof ResetReason>;

export const ChipModel = {
  ESP32: 1,
  ESP32_S2: 2,
  ESP32_S3: 3,
  ESP32_C3: 4,
  ESP32_C6: 5,
  ESP32_C5: 6,
  ESP32_H2: 7,
  ESP32_P4: 8,
  ESP32_C2: 9,
  ESP32_C61: 10,
  OTHER: 0xff,
} as const;
export type ChipModel = ValueOf<typeof ChipModel>;

export const DeviceState = {
  /** published by the broker as the MQTT Last Will - the device vanished */
  OFFLINE: 0,
  ONLINE: 1,
  /** announced before deep sleep; clean DISCONNECT suppresses the Last Will */
  SLEEPING: 2,
  UPDATING: 3,
  REBOOTING: 4,
} as const;
export type DeviceState = ValueOf<typeof DeviceState>;

export const EventKind = {
  INFERENCE_POSITIVE: 1,
  MODE_CHANGED: 2,
  LEASE_EXPIRED: 3,
  FAILSAFE_TRIPPED: 4,
  THRESHOLD: 5,
} as const;
export type EventKind = ValueOf<typeof EventKind>;

/** READ_TELEMETRY section mask - lets the caller bound the reply size. */
export const TelemetrySection = {
  HEAP: 1 << 0,
  NET: 1 << 1,
  CPU: 1 << 2,
  TASKS: 1 << 3,
  SENSORS: 1 << 4,
  ACTUATORS: 1 << 5,
  INFERENCE: 1 << 6,
  EXT: 1 << 7,
} as const;
export type TelemetrySection = ValueOf<typeof TelemetrySection>;
export const TELEMETRY_SECTIONS_ALL = 0xff;
