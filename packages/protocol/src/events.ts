/*
 * AEDI - IONITY GLOBAL | @ionity/ewp - Edge Wire Protocol v1 | events.ts
 * Doc ID: DOC-2026-10-ESP32MCP-EWP1 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * EVENT, LOG and CRASH bodies (topic `ev`, QoS 1).
 *
 * `rid` links a frame to the tool request that caused it. The device never
 * sees Datadog trace ids: the gateway keeps rid -> span context (LRU, 60 s)
 * and stamps dd.trace_id / dd.span_id onto the log record it forwards, so a
 * trace in APM pivots straight to the on-device log lines - zero bytes of
 * trace context on the radio.
 */
import { defineKeys } from './keys.ts';
import type { EventKind, LogLevel, ResetReason, StateMode } from './ids.ts';
import type { F32, FixedBytes, U32, U8, Unit, Utf8 } from './scalars.ts';

export type EventBody =
  | {
      readonly kind: typeof EventKind.INFERENCE_POSITIVE;
      readonly rid?: U32;
      readonly model: U8;
      readonly cls?: U8;
      readonly p: Unit;
    }
  | {
      readonly kind: typeof EventKind.MODE_CHANGED;
      readonly rid?: U32;
      readonly from: StateMode;
      readonly to: StateMode;
      /** 0 = tool call, 1 = local rule, 2 = brown-out / watchdog recovery */
      readonly cause: U8;
    }
  | {
      readonly kind: typeof EventKind.LEASE_EXPIRED;
      readonly rid?: U32;
      readonly channel: U8;
      readonly revertedTo: Unit;
    }
  | {
      readonly kind: typeof EventKind.FAILSAFE_TRIPPED;
      readonly rid?: U32;
      readonly reason: Utf8<48>;
    }
  | {
      readonly kind: typeof EventKind.THRESHOLD;
      readonly rid?: U32;
      /** HELLO.metrics[].id */
      readonly metric: U8;
      readonly value: F32;
    };
export const EventBodyKey = defineKeys<EventBody>()({
  kind: 1,
  rid: 2,
  model: 3,
  cls: 4,
  p: 5,
  from: 6,
  to: 7,
  cause: 8,
  channel: 9,
  revertedTo: 10,
  reason: 11,
  metric: 12,
  value: 13,
});

export interface LogBody {
  readonly level: LogLevel;
  /** ESP_LOG tag */
  readonly tag: Utf8<16>;
  readonly msg: Utf8<192>;
  readonly rid?: U32;
  /** lines dropped by the device rate limiter since the previous LOG frame */
  readonly dropped?: U32;
}
export const LogBodyKey = defineKeys<LogBody>()({ level: 1, tag: 2, msg: 3, rid: 4, dropped: 5 });

/**
 * Sent once on the boot AFTER a panic, from esp_core_dump_get_summary()
 * (core dump in its flash partition - the network was dead when it crashed).
 * The gateway symbolises `backtrace` with addr2line against the ELF whose
 * SHA-256 prefix is `elfSha` (archived by CI) and forwards a Datadog log with
 * error.kind = "GuruMeditation" so Error Tracking groups crashes by stack.
 */
export interface CrashBody {
  readonly reason: ResetReason;
  /** pcTaskGetName of the faulting task */
  readonly task: Utf8<16>;
  readonly pc: U32;
  /** return addresses, innermost first, <= EWP_LIMITS.backtraceDepth */
  readonly backtrace: readonly U32[];
  readonly btCorrupted: boolean;
  readonly elfSha: FixedBytes<8>;
  /** Xtensa EXCCAUSE / RISC-V mcause */
  readonly cause?: U32;
  /** Xtensa EXCVADDR / RISC-V mtval - the address that faulted */
  readonly vaddr?: U32;
  /** panic reason string if the panic handler recorded one */
  readonly msg?: Utf8<96>;
}
export const CrashBodyKey = defineKeys<CrashBody>()({
  reason: 1,
  task: 2,
  pc: 3,
  backtrace: 4,
  btCorrupted: 5,
  elfSha: 6,
  cause: 7,
  vaddr: 8,
  msg: 9,
});
