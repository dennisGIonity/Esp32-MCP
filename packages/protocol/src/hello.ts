/*
 * AEDI - IONITY GLOBAL | @ionity/ewp - Edge Wire Protocol v1 | hello.ts
 * Doc ID: DOC-2026-10-ESP32MCP-EWP1 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * HELLO (topic `hello`, retained) and STATUS (topic `st`, retained + Last Will).
 *
 * HELLO is the device's self-advertisement: everything the gateway needs to
 * build MCP tool schemas for this board (which tools, which models with which
 * classes, which actuator channels, which custom metrics) is sent ONCE per
 * connection, so per-call frames can carry 1-byte handles instead of names.
 * Kept on its own retained topic so a Last Will on `st` never erases it.
 */
import { defineKeys } from './keys.ts';
import type { ActuatorKind, ChipModel, DeviceState, ModelKind, ResetReason, StateMode, TensorDType, ToolId } from './ids.ts';
import type { TensorShape } from './tools.ts';
import type { FixedBytes, U16, U32, U8, Unit, Utf8 } from './scalars.ts';

export interface FirmwareInfo {
  /** semver, e.g. "3.0.0" */
  readonly version: Utf8<16>;
  /** esp_app_get_elf_sha256() prefix - selects the ELF used to symbolise CRASH backtraces */
  readonly elfSha: FixedBytes<8>;
  /** esp_get_idf_version(), e.g. "v5.5.1" */
  readonly idf: Utf8<16>;
}
export const FirmwareInfoKey = defineKeys<FirmwareInfo>()({ version: 1, elfSha: 2, idf: 3 });

export interface ChipInfo {
  readonly model: ChipModel;
  /** esp_chip_info().revision, e.g. 2 for v0.2 */
  readonly rev: U16;
  readonly cores: U8;
  readonly flashMb: U8;
  /** 0 when no PSRAM - gateway disables models whose arena does not fit */
  readonly psramMb: U8;
}
export const ChipInfoKey = defineKeys<ChipInfo>()({ model: 1, rev: 2, cores: 3, flashMb: 4, psramMb: 5 });

/** What this firmware build actually allocated (may be below EWP_LIMITS). */
export interface LinkLimits {
  readonly maxDownlink: U16;
  readonly maxUplink: U16;
  readonly maxInflight: U8;
}
export const LinkLimitsKey = defineKeys<LinkLimits>()({ maxDownlink: 1, maxUplink: 2, maxInflight: 3 });

/** [tool id, contract version, ToolFlag bitset] */
export type ToolDescriptor = readonly [id: ToolId, ver: U8, flags: U8];

export interface ModelDescriptor {
  readonly id: U8;
  /** stable LLM-facing name, e.g. "vibration_anomaly" */
  readonly name: Utf8<24>;
  readonly hash: FixedBytes<8>;
  readonly kind: ModelKind;
  readonly dtype: TensorDType;
  readonly shape: TensorShape;
  readonly classes: U8;
  /** optional on-device labels; the gateway's model registry (by hash) wins when both exist */
  readonly labels?: readonly Utf8<24>[];
  /** tensor arena the model needs; 0 for STATISTICAL models */
  readonly arenaBytes: U32;
}
export const ModelDescriptorKey = defineKeys<ModelDescriptor>()({
  id: 1,
  name: 2,
  hash: 3,
  kind: 4,
  dtype: 5,
  shape: 6,
  classes: 7,
  labels: 8,
  arenaBytes: 9,
});

export interface ActuatorDescriptor {
  readonly ch: U8;
  /** fw 2.x names kept: "led", "alert_led", "pwm0", "pwm1", "relay0" */
  readonly name: Utf8<16>;
  readonly kind: ActuatorKind;
  readonly gpio: U8;
  /** value applied at boot, on FAILSAFE and when a lease expires */
  readonly safe: Unit;
  /** LEDC/SDM resolution in bits (PWM/SDM only) */
  readonly resBits?: U8;
  /** relay channels marked as switching mains/high-energy loads - gateway always confirms */
  readonly hazardous?: boolean;
}
export const ActuatorDescriptorKey = defineKeys<ActuatorDescriptor>()({
  ch: 1,
  name: 2,
  kind: 3,
  gpio: 4,
  safe: 5,
  resBits: 6,
  hazardous: 7,
});

/** [metric id, metric name (Datadog suffix), unit] - dictionary for TelemetryBody.ext */
export type MetricDescriptor = readonly [id: U8, name: Utf8<32>, unit: Utf8<8>];

/** [task id, FreeRTOS task name, core (0, 1, 0xFF = unpinned), stack bytes] */
export type TaskDescriptor = readonly [id: U8, name: Utf8<16>, core: U8, stackBytes: U16];

export interface HelloBody {
  /** EWP versions this firmware speaks (inclusive) */
  readonly proto: readonly [min: U8, max: U8];
  /** "esp32-" + base MAC; equals the MQTT client id and the topic level */
  readonly hwid: Utf8<24>;
  /** provisioned logical identity (RFC 9562 UUID, 16 raw bytes) - survives a board swap */
  readonly uuid: FixedBytes<16>;
  readonly fw: FirmwareInfo;
  readonly chip: ChipInfo;
  readonly limits: LinkLimits;
  readonly tools: readonly ToolDescriptor[];
  readonly models: readonly ModelDescriptor[];
  readonly actuators: readonly ActuatorDescriptor[];
  readonly metrics: readonly MetricDescriptor[];
  readonly tasks: readonly TaskDescriptor[];
  readonly mode: StateMode;
  readonly site?: Utf8<32>;
  readonly group?: Utf8<32>;
  readonly label?: Utf8<48>;
  readonly resetReason: ResetReason;
  readonly bootCount: U32;
}
export const HelloBodyKey = defineKeys<HelloBody>()({
  proto: 1,
  hwid: 2,
  uuid: 3,
  fw: 4,
  chip: 5,
  limits: 6,
  tools: 7,
  models: 8,
  actuators: 9,
  metrics: 10,
  tasks: 11,
  mode: 12,
  site: 13,
  group: 14,
  label: 15,
  resetReason: 16,
  bootCount: 17,
});

export interface StatusBody {
  readonly state: DeviceState;
  readonly mode?: StateMode;
  /** SLEEPING: unix seconds of the planned wake-up, so the host does not raise "offline" */
  readonly wakeAtS?: U32;
  /** IPv4 (4 B) or IPv6 (16 B) */
  readonly ip?: FixedBytes<4> | FixedBytes<16>;
}
export const StatusBodyKey = defineKeys<StatusBody>()({ state: 1, mode: 2, wakeAtS: 3, ip: 4 });
