/*
 * AEDI - IONITY GLOBAL | @ionity/ewp - Edge Wire Protocol v1 | telemetry.ts
 * Doc ID: DOC-2026-10-ESP32MCP-EWP1 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * TELEMETRY frame body (topic `tm`, QoS 0) - also the read_telemetry result.
 *
 * Every field maps to one ESP-IDF call so the firmware has nothing to invent:
 *   heap.internalFree     heap_caps_get_free_size(MALLOC_CAP_INTERNAL)
 *   heap.internalMinFree  heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL)
 *   heap.internalLargest  heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL)
 *   heap.psram*           same with MALLOC_CAP_SPIRAM (absent without PSRAM, e.g. ESP32-C6)
 *   heap.dmaFree          heap_caps_get_free_size(MALLOC_CAP_DMA)  - Wi-Fi/I2S/SPI live here
 *   net.rssiDbm           esp_wifi_sta_get_ap_info().rssi
 *   cpu.loadPct           FreeRTOS run-time stats of IDLE0/IDLE1 over the interval
 *   tasks[].stackFreeMin  uxTaskGetStackHighWaterMark() - BYTES in ESP-IDF (not words)
 *
 * Fragmentation is derived by the gateway: 1 - internalLargest / internalFree.
 * A board whose largest block falls below EWP_LIMITS.downlinkFrame can no
 * longer receive a full frame even with "plenty" of free heap - Datadog
 * monitors alert on that ratio, not on free heap alone.
 */
import { defineKeys } from './keys.ts';
import type { StateMode } from './ids.ts';
import type { F32, I8, U16, U32, U8, Unit } from './scalars.ts';

export interface HeapStats {
  readonly internalFree: U32;
  readonly internalMinFree: U32;
  readonly internalLargest: U32;
  readonly psramFree?: U32;
  readonly psramMinFree?: U32;
  readonly psramLargest?: U32;
  readonly dmaFree?: U32;
}
export const HeapStatsKey = defineKeys<HeapStats>()({
  internalFree: 1,
  internalMinFree: 2,
  internalLargest: 3,
  psramFree: 4,
  psramMinFree: 5,
  psramLargest: 6,
  dmaFree: 7,
});

export interface NetStats {
  /** absent while not associated */
  readonly rssiDbm?: I8;
  readonly channel?: U8;
  /** publishes that failed since boot */
  readonly txFail: U32;
  readonly reconnects: U16;
  /** frames waiting in the device's uplink queue right now */
  readonly uplinkQueued: U8;
}
export const NetStatsKey = defineKeys<NetStats>()({
  rssiDbm: 1,
  channel: 2,
  txFail: 3,
  reconnects: 4,
  uplinkQueued: 5,
});

/** Per-core load 0..100 %. One element on single-core chips (ESP32-C3/C6). */
export type CpuLoad = readonly [core0: U8] | readonly [core0: U8, core1: U8];

/** [task id (HELLO.tasks), min free stack bytes, worst loop/handler time us] */
export type TaskStat = readonly [id: U8, stackFreeMin: U16, runUsMax: U32];

export interface SensorStats {
  /** on-die temperature sensor (driver/temperature_sensor.h) - not ambient */
  readonly dieTempC?: F32;
  /** calibrated ADC channels in mV (esp_adc/adc_cali) */
  readonly adcMv?: readonly U16[];
}
export const SensorStatsKey = defineKeys<SensorStats>()({ dieTempC: 1, adcMv: 2 });

export interface LastInference {
  readonly model: U8;
  readonly positive: boolean;
  readonly p: Unit;
  readonly ageMs: U32;
}
export const LastInferenceKey = defineKeys<LastInference>()({ model: 1, positive: 2, p: 3, ageMs: 4 });

export interface TelemetryBody {
  /** per-boot sequence; a gap means QoS 0 loss, a reset to 0 means reboot */
  readonly seq: U32;
  readonly uptimeS: U32;
  /** unix seconds, only once SNTP has synced (no RTC on these boards) */
  readonly unixS?: U32;
  /** with FrameFlag.BUFFERED: how old this sample was when published (fw 2.0.1 lesson) */
  readonly ageMs?: U32;
  readonly mode: StateMode;
  readonly heap?: HeapStats;
  readonly net?: NetStats;
  readonly cpu?: CpuLoad;
  readonly tasks?: readonly TaskStat[];
  readonly sensors?: SensorStats;
  /** current value per actuator channel, index = HELLO.actuators[].ch */
  readonly actuators?: readonly Unit[];
  readonly inference?: LastInference;
  /** open metrics (fw 2.x `metrics{}`), keyed by HELLO.metrics[].id - names sent once, not per sample */
  readonly ext?: ReadonlyMap<U8, F32>;
}
export const TelemetryBodyKey = defineKeys<TelemetryBody>()({
  seq: 1,
  uptimeS: 2,
  unixS: 3,
  ageMs: 4,
  mode: 5,
  heap: 6,
  net: 7,
  cpu: 8,
  tasks: 9,
  sensors: 10,
  actuators: 11,
  inference: 12,
  ext: 13,
});
