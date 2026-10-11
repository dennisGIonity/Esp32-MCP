/*
 * AEDI - IONITY GLOBAL | ewp_protocol.h - Edge Wire Protocol v1 (EWP/1)
 * Doc ID: DOC-2026-10-ESP32MCP-EWP1 | Policy 986 AED | License AED 900
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 *
 * AUTO-GENERATED from packages/protocol (scripts/gen-c-header.ts). DO NOT EDIT.
 * Change the TypeScript contract and regenerate; CI rejects a stale copy.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
#define EWP_STATIC_ASSERT(c, m) static_assert((c), m)
extern "C" {
#else
#define EWP_STATIC_ASSERT(c, m) _Static_assert((c), m)
#endif

/* Xtensa (ESP32/S2/S3) and RISC-V (C3/C6/H2/P4) are little-endian, like the wire. */
EWP_STATIC_ASSERT(__BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__, "EWP wire format is little-endian");

/* ---- framing ----------------------------------------------------------- */
#define EWP_MAGIC                 0xE5u
#define EWP_VERSION               1u
#define EWP_HEADER_LEN            16u
#define EWP_FRAME_FLAGS_KNOWN     0x1Fu
#define EWP_NO_TOOL               0x00u

/* ---- hard limits (buffers are sized from these - never exceed) --------- */
#define EWP_LIMIT_DOWNLINK_FRAME       4096u
#define EWP_LIMIT_UPLINK_FRAME         8192u
#define EWP_LIMIT_MAX_INFLIGHT         4u
#define EWP_LIMIT_INLINE_TENSOR_BYTES  2048u
#define EWP_LIMIT_MAX_TENSOR_RANK      4u
#define EWP_LIMIT_TOP_K                8u
#define EWP_LIMIT_ACTUATOR_CHANNELS    16u
#define EWP_LIMIT_MODELS               8u
#define EWP_LIMIT_CUSTOM_METRICS       32u
#define EWP_LIMIT_TASK_STATS           12u
#define EWP_LIMIT_BACKTRACE_DEPTH      16u
#define EWP_LIMIT_DEFAULT_DEADLINE_MS  5000u
#define EWP_LIMIT_SLEEP_MIN_S          5u
#define EWP_LIMIT_SLEEP_MAX_S          86400u
#define EWP_LIMIT_LEASE_MAX_MS         86400000u

/* ---- topics: ionity/ewp1/<site>/<hwid>/<channel> ----------------------- */
#define EWP_TOPIC_ROOT            "ionity/ewp1"
#define EWP_TOPIC_BROADCAST       "ionity/ewp1/_all/dn"
#define EWP_CH_DOWN                "dn"   /* qos 1 */
#define EWP_CH_DOWN_QOS            1
#define EWP_CH_DOWN_RETAIN         0
#define EWP_CH_RESULT              "rs"   /* qos 1 */
#define EWP_CH_RESULT_QOS          1
#define EWP_CH_RESULT_RETAIN       0
#define EWP_CH_TELEMETRY           "tm"   /* qos 0 */
#define EWP_CH_TELEMETRY_QOS       0
#define EWP_CH_TELEMETRY_RETAIN    0
#define EWP_CH_EVENT               "ev"   /* qos 1 */
#define EWP_CH_EVENT_QOS           1
#define EWP_CH_EVENT_RETAIN        0
#define EWP_CH_HELLO               "hello"   /* qos 1, retained */
#define EWP_CH_HELLO_QOS           1
#define EWP_CH_HELLO_RETAIN        1
#define EWP_CH_STATUS              "st"   /* qos 1, retained */
#define EWP_CH_STATUS_QOS          1
#define EWP_CH_STATUS_RETAIN       1

/* ---- identifiers ------------------------------------------------------- */
typedef enum {
    EWP_MSG_REQ = 0x01,
    EWP_MSG_ACK = 0x02,
    EWP_MSG_RES = 0x03,
    EWP_MSG_ERR = 0x04,
    EWP_MSG_CANCEL = 0x05,
    EWP_MSG_TELEMETRY = 0x10,
    EWP_MSG_EVENT = 0x11,
    EWP_MSG_LOG = 0x12,
    EWP_MSG_CRASH = 0x13,
    EWP_MSG_HELLO = 0x20,
    EWP_MSG_STATUS = 0x21,
} ewp_msg_type_t;

typedef enum {
    EWP_FLAG_ACK_REQUESTED = 0x01,
    EWP_FLAG_BROADCAST = 0x02,
    EWP_FLAG_PRIORITY = 0x04,
    EWP_FLAG_BUFFERED = 0x08,
    EWP_FLAG_TRUNCATED = 0x10,
} ewp_frame_flag_t;

typedef enum {
    EWP_TOOL_GET_DEVICE_INFO = 0x01,
    EWP_TOOL_READ_TELEMETRY = 0x02,
    EWP_TOOL_RUN_INFERENCE = 0x03,
    EWP_TOOL_SET_ACTUATOR = 0x04,
    EWP_TOOL_SET_STATE_MODE = 0x05,
    EWP_TOOL_IDENTIFY = 0x06,
} ewp_tool_id_t;

typedef enum {
    EWP_TOOL_FLAG_READ_ONLY = 0x01,
    EWP_TOOL_FLAG_DESTRUCTIVE = 0x02,
    EWP_TOOL_FLAG_LONG_RUNNING = 0x04,
    EWP_TOOL_FLAG_IDEMPOTENT = 0x08,
} ewp_tool_flag_t;

typedef enum {
    EWP_ST_OK = 0x00,
    EWP_ST_ACCEPTED = 0x01,
    EWP_ST_BAD_FRAME = 0x10,
    EWP_ST_UNSUPPORTED_VERSION = 0x11,
    EWP_ST_UNKNOWN_TOOL = 0x12,
    EWP_ST_INVALID_ARGS = 0x13,
    EWP_ST_TOO_LARGE = 0x14,
    EWP_ST_DUPLICATE = 0x15,
    EWP_ST_BUSY = 0x20,
    EWP_ST_DEADLINE_EXCEEDED = 0x21,
    EWP_ST_CANCELLED = 0x22,
    EWP_ST_FAILSAFE_LOCKED = 0x23,
    EWP_ST_NOT_READY = 0x24,
    EWP_ST_UNAUTHORIZED = 0x25,
    EWP_ST_RATE_LIMITED = 0x26,
    EWP_ST_TOOL_FAULT = 0x30,
    EWP_ST_OUT_OF_MEMORY = 0x31,
    EWP_ST_HW_FAULT = 0x32,
    EWP_ST_MODEL_ERROR = 0x33,
    EWP_ST_INTERNAL = 0x3F,
} ewp_status_t;

typedef enum {
    EWP_MODE_ACTIVE = 0,
    EWP_MODE_STANDBY = 1,
    EWP_MODE_INFERENCE_ACTIVE = 2,
    EWP_MODE_LOW_POWER_SLEEP = 3,
    EWP_MODE_FAILSAFE = 4,
} ewp_state_mode_t;

typedef enum {
    EWP_ACT_PWM = 1,
    EWP_ACT_RELAY = 2,
    EWP_ACT_GPIO = 3,
    EWP_ACT_SDM = 4,
    EWP_ACT_DAC = 5,
} ewp_actuator_kind_t;

typedef enum {
    EWP_INPUT_LIVE = 0,
    EWP_INPUT_INLINE = 1,
    EWP_INPUT_BLOB = 2,
} ewp_input_source_t;

typedef enum {
    EWP_DTYPE_I8 = 0,
    EWP_DTYPE_U8 = 1,
    EWP_DTYPE_I16 = 2,
    EWP_DTYPE_F32 = 3,
} ewp_tensor_dtype_t;

typedef enum {
    EWP_MODEL_CLASSIFIER = 1,
    EWP_MODEL_ANOMALY = 2,
    EWP_MODEL_REGRESSOR = 3,
    EWP_MODEL_STATISTICAL = 4,
} ewp_model_kind_t;

typedef enum {
    EWP_LOG_ERROR = 1,
    EWP_LOG_WARN = 2,
    EWP_LOG_INFO = 3,
    EWP_LOG_DEBUG = 4,
    EWP_LOG_VERBOSE = 5,
} ewp_log_level_t;

typedef enum {
    EWP_RESET_UNKNOWN = 0,
    EWP_RESET_POWER_ON = 1,
    EWP_RESET_EXTERNAL = 2,
    EWP_RESET_SOFTWARE = 3,
    EWP_RESET_PANIC = 4,
    EWP_RESET_INT_WDT = 5,
    EWP_RESET_TASK_WDT = 6,
    EWP_RESET_OTHER_WDT = 7,
    EWP_RESET_DEEP_SLEEP = 8,
    EWP_RESET_BROWNOUT = 9,
    EWP_RESET_USB = 10,
    EWP_RESET_JTAG = 11,
    EWP_RESET_EFUSE = 12,
    EWP_RESET_POWER_GLITCH = 13,
    EWP_RESET_CPU_LOCKUP = 14,
    EWP_RESET_OTA_ROLLBACK = 15,
} ewp_reset_reason_t;

typedef enum {
    EWP_CHIP_ESP32 = 0x01,
    EWP_CHIP_ESP32_S2 = 0x02,
    EWP_CHIP_ESP32_S3 = 0x03,
    EWP_CHIP_ESP32_C3 = 0x04,
    EWP_CHIP_ESP32_C6 = 0x05,
    EWP_CHIP_ESP32_C5 = 0x06,
    EWP_CHIP_ESP32_H2 = 0x07,
    EWP_CHIP_ESP32_P4 = 0x08,
    EWP_CHIP_ESP32_C2 = 0x09,
    EWP_CHIP_ESP32_C61 = 0x0A,
    EWP_CHIP_OTHER = 0xFF,
} ewp_chip_model_t;

typedef enum {
    EWP_STATE_OFFLINE = 0,
    EWP_STATE_ONLINE = 1,
    EWP_STATE_SLEEPING = 2,
    EWP_STATE_UPDATING = 3,
    EWP_STATE_REBOOTING = 4,
} ewp_device_state_t;

typedef enum {
    EWP_EVENT_INFERENCE_POSITIVE = 1,
    EWP_EVENT_MODE_CHANGED = 2,
    EWP_EVENT_LEASE_EXPIRED = 3,
    EWP_EVENT_FAILSAFE_TRIPPED = 4,
    EWP_EVENT_THRESHOLD = 5,
} ewp_event_kind_t;

typedef enum {
    EWP_TM_HEAP = 0x01,
    EWP_TM_NET = 0x02,
    EWP_TM_CPU = 0x04,
    EWP_TM_TASKS = 0x08,
    EWP_TM_SENSORS = 0x10,
    EWP_TM_ACTUATORS = 0x20,
    EWP_TM_INFERENCE = 0x40,
    EWP_TM_EXT = 0x80,
} ewp_telemetry_section_t;

/* LLM-facing names, for the optional on-device HTTP MCP front-end. */
#define EWP_TOOL_NAME_GET_DEVICE_INFO      "get_device_info"
#define EWP_TOOL_NAME_READ_TELEMETRY       "read_telemetry"
#define EWP_TOOL_NAME_RUN_INFERENCE        "run_inference"
#define EWP_TOOL_NAME_SET_ACTUATOR         "set_actuator"
#define EWP_TOOL_NAME_SET_STATE_MODE       "set_state_mode"
#define EWP_TOOL_NAME_IDENTIFY             "identify"

/* ---- CBOR map keys (all 0..23 => one byte on the wire) ----------------- */
/* AckBody */
#define EWP_K_ACK_BODY_QUEUE_DEPTH                     1u
#define EWP_K_ACK_BODY_ETA_MS                          2u
/* ActuatorDescriptor */
#define EWP_K_ACTUATOR_DESCRIPTOR_CH                   1u
#define EWP_K_ACTUATOR_DESCRIPTOR_NAME                 2u
#define EWP_K_ACTUATOR_DESCRIPTOR_KIND                 3u
#define EWP_K_ACTUATOR_DESCRIPTOR_GPIO                 4u
#define EWP_K_ACTUATOR_DESCRIPTOR_SAFE                 5u
#define EWP_K_ACTUATOR_DESCRIPTOR_RES_BITS             6u
#define EWP_K_ACTUATOR_DESCRIPTOR_HAZARDOUS            7u
/* CancelBody */
#define EWP_K_CANCEL_BODY_REASON                       1u
/* ChipInfo */
#define EWP_K_CHIP_INFO_MODEL                          1u
#define EWP_K_CHIP_INFO_REV                            2u
#define EWP_K_CHIP_INFO_CORES                          3u
#define EWP_K_CHIP_INFO_FLASH_MB                       4u
#define EWP_K_CHIP_INFO_PSRAM_MB                       5u
/* CrashBody */
#define EWP_K_CRASH_BODY_REASON                        1u
#define EWP_K_CRASH_BODY_TASK                          2u
#define EWP_K_CRASH_BODY_PC                            3u
#define EWP_K_CRASH_BODY_BACKTRACE                     4u
#define EWP_K_CRASH_BODY_BT_CORRUPTED                  5u
#define EWP_K_CRASH_BODY_ELF_SHA                       6u
#define EWP_K_CRASH_BODY_CAUSE                         7u
#define EWP_K_CRASH_BODY_VADDR                         8u
#define EWP_K_CRASH_BODY_MSG                           9u
/* ErrorBody */
#define EWP_K_ERROR_BODY_DETAIL                        1u
#define EWP_K_ERROR_BODY_ARG_KEY                       2u
#define EWP_K_ERROR_BODY_RETRY_AFTER_MS                3u
#define EWP_K_ERROR_BODY_TIMING                        4u
/* EventBody */
#define EWP_K_EVENT_BODY_KIND                          1u
#define EWP_K_EVENT_BODY_RID                           2u
#define EWP_K_EVENT_BODY_MODEL                         3u
#define EWP_K_EVENT_BODY_CLS                           4u
#define EWP_K_EVENT_BODY_P                             5u
#define EWP_K_EVENT_BODY_FROM                          6u
#define EWP_K_EVENT_BODY_TO                            7u
#define EWP_K_EVENT_BODY_CAUSE                         8u
#define EWP_K_EVENT_BODY_CHANNEL                       9u
#define EWP_K_EVENT_BODY_REVERTED_TO                   10u
#define EWP_K_EVENT_BODY_REASON                        11u
#define EWP_K_EVENT_BODY_METRIC                        12u
#define EWP_K_EVENT_BODY_VALUE                         13u
/* ExecTiming */
#define EWP_K_EXEC_TIMING_QUEUE_US                     1u
#define EWP_K_EXEC_TIMING_EXEC_US                      2u
#define EWP_K_EXEC_TIMING_HEAP_DELTA                   3u
/* FirmwareInfo */
#define EWP_K_FIRMWARE_INFO_VERSION                    1u
#define EWP_K_FIRMWARE_INFO_ELF_SHA                    2u
#define EWP_K_FIRMWARE_INFO_IDF                        3u
/* HeapStats */
#define EWP_K_HEAP_STATS_INTERNAL_FREE                 1u
#define EWP_K_HEAP_STATS_INTERNAL_MIN_FREE             2u
#define EWP_K_HEAP_STATS_INTERNAL_LARGEST              3u
#define EWP_K_HEAP_STATS_PSRAM_FREE                    4u
#define EWP_K_HEAP_STATS_PSRAM_MIN_FREE                5u
#define EWP_K_HEAP_STATS_PSRAM_LARGEST                 6u
#define EWP_K_HEAP_STATS_DMA_FREE                      7u
/* HelloBody */
#define EWP_K_HELLO_BODY_PROTO                         1u
#define EWP_K_HELLO_BODY_HWID                          2u
#define EWP_K_HELLO_BODY_UUID                          3u
#define EWP_K_HELLO_BODY_FW                            4u
#define EWP_K_HELLO_BODY_CHIP                          5u
#define EWP_K_HELLO_BODY_LIMITS                        6u
#define EWP_K_HELLO_BODY_TOOLS                         7u
#define EWP_K_HELLO_BODY_MODELS                        8u
#define EWP_K_HELLO_BODY_ACTUATORS                     9u
#define EWP_K_HELLO_BODY_METRICS                       10u
#define EWP_K_HELLO_BODY_TASKS                         11u
#define EWP_K_HELLO_BODY_MODE                          12u
#define EWP_K_HELLO_BODY_SITE                          13u
#define EWP_K_HELLO_BODY_GROUP                         14u
#define EWP_K_HELLO_BODY_LABEL                         15u
#define EWP_K_HELLO_BODY_RESET_REASON                  16u
#define EWP_K_HELLO_BODY_BOOT_COUNT                    17u
/* IdentifyArgs */
#define EWP_K_IDENTIFY_ARGS_DURATION_MS                1u
/* InferenceInput */
#define EWP_K_INFERENCE_INPUT_SOURCE                   1u
#define EWP_K_INFERENCE_INPUT_WINDOW_MS                2u
#define EWP_K_INFERENCE_INPUT_DTYPE                    3u
#define EWP_K_INFERENCE_INPUT_SHAPE                    4u
#define EWP_K_INFERENCE_INPUT_DATA                     5u
#define EWP_K_INFERENCE_INPUT_BLOB_ID                  6u
#define EWP_K_INFERENCE_INPUT_SHA256                   7u
/* InferenceResult */
#define EWP_K_INFERENCE_RESULT_MODEL                   1u
#define EWP_K_INFERENCE_RESULT_MODEL_HASH              2u
#define EWP_K_INFERENCE_RESULT_CLASSES                 3u
#define EWP_K_INFERENCE_RESULT_SCORE                   4u
#define EWP_K_INFERENCE_RESULT_POSITIVE                5u
#define EWP_K_INFERENCE_RESULT_STAGES                  6u
#define EWP_K_INFERENCE_RESULT_ARENA_PEAK_BYTES        7u
/* LastInference */
#define EWP_K_LAST_INFERENCE_MODEL                     1u
#define EWP_K_LAST_INFERENCE_POSITIVE                  2u
#define EWP_K_LAST_INFERENCE_P                         3u
#define EWP_K_LAST_INFERENCE_AGE_MS                    4u
/* LinkLimits */
#define EWP_K_LINK_LIMITS_MAX_DOWNLINK                 1u
#define EWP_K_LINK_LIMITS_MAX_UPLINK                   2u
#define EWP_K_LINK_LIMITS_MAX_INFLIGHT                 3u
/* LogBody */
#define EWP_K_LOG_BODY_LEVEL                           1u
#define EWP_K_LOG_BODY_TAG                             2u
#define EWP_K_LOG_BODY_MSG                             3u
#define EWP_K_LOG_BODY_RID                             4u
#define EWP_K_LOG_BODY_DROPPED                         5u
/* ModelDescriptor */
#define EWP_K_MODEL_DESCRIPTOR_ID                      1u
#define EWP_K_MODEL_DESCRIPTOR_NAME                    2u
#define EWP_K_MODEL_DESCRIPTOR_HASH                    3u
#define EWP_K_MODEL_DESCRIPTOR_KIND                    4u
#define EWP_K_MODEL_DESCRIPTOR_DTYPE                   5u
#define EWP_K_MODEL_DESCRIPTOR_SHAPE                   6u
#define EWP_K_MODEL_DESCRIPTOR_CLASSES                 7u
#define EWP_K_MODEL_DESCRIPTOR_LABELS                  8u
#define EWP_K_MODEL_DESCRIPTOR_ARENA_BYTES             9u
/* NetStats */
#define EWP_K_NET_STATS_RSSI_DBM                       1u
#define EWP_K_NET_STATS_CHANNEL                        2u
#define EWP_K_NET_STATS_TX_FAIL                        3u
#define EWP_K_NET_STATS_RECONNECTS                     4u
#define EWP_K_NET_STATS_UPLINK_QUEUED                  5u
/* ReadTelemetryArgs */
#define EWP_K_READ_TELEMETRY_ARGS_SECTIONS             1u
/* ResultBody */
#define EWP_K_RESULT_BODY_TIMING                       1u
#define EWP_K_RESULT_BODY_RESULT                       2u
/* RunInferenceArgs */
#define EWP_K_RUN_INFERENCE_ARGS_MODEL                 1u
#define EWP_K_RUN_INFERENCE_ARGS_INPUT                 2u
#define EWP_K_RUN_INFERENCE_ARGS_TOP_K                 3u
#define EWP_K_RUN_INFERENCE_ARGS_THRESHOLD             4u
/* SensorStats */
#define EWP_K_SENSOR_STATS_DIE_TEMP_C                  1u
#define EWP_K_SENSOR_STATS_ADC_MV                      2u
/* SetActuatorArgs */
#define EWP_K_SET_ACTUATOR_ARGS_CHANNEL                1u
#define EWP_K_SET_ACTUATOR_ARGS_VALUE                  2u
#define EWP_K_SET_ACTUATOR_ARGS_RAMP_MS                3u
#define EWP_K_SET_ACTUATOR_ARGS_LEASE_MS               4u
/* SetActuatorResult */
#define EWP_K_SET_ACTUATOR_RESULT_CHANNEL              1u
#define EWP_K_SET_ACTUATOR_RESULT_APPLIED              2u
#define EWP_K_SET_ACTUATOR_RESULT_PREVIOUS             3u
#define EWP_K_SET_ACTUATOR_RESULT_LEASE_REMAINING_MS   4u
/* SetStateModeArgs */
#define EWP_K_SET_STATE_MODE_ARGS_MODE                 1u
#define EWP_K_SET_STATE_MODE_ARGS_MODEL                2u
#define EWP_K_SET_STATE_MODE_ARGS_PERIOD_MS            3u
#define EWP_K_SET_STATE_MODE_ARGS_DURATION_S           4u
#define EWP_K_SET_STATE_MODE_ARGS_REASON               5u
/* SetStateModeResult */
#define EWP_K_SET_STATE_MODE_RESULT_PREVIOUS           1u
#define EWP_K_SET_STATE_MODE_RESULT_CURRENT            2u
#define EWP_K_SET_STATE_MODE_RESULT_EFFECTIVE_IN_MS    3u
#define EWP_K_SET_STATE_MODE_RESULT_LATCHED            4u
/* StatusBody */
#define EWP_K_STATUS_BODY_STATE                        1u
#define EWP_K_STATUS_BODY_MODE                         2u
#define EWP_K_STATUS_BODY_WAKE_AT_S                    3u
#define EWP_K_STATUS_BODY_IP                           4u
/* TelemetryBody */
#define EWP_K_TELEMETRY_BODY_SEQ                       1u
#define EWP_K_TELEMETRY_BODY_UPTIME_S                  2u
#define EWP_K_TELEMETRY_BODY_UNIX_S                    3u
#define EWP_K_TELEMETRY_BODY_AGE_MS                    4u
#define EWP_K_TELEMETRY_BODY_MODE                      5u
#define EWP_K_TELEMETRY_BODY_HEAP                      6u
#define EWP_K_TELEMETRY_BODY_NET                       7u
#define EWP_K_TELEMETRY_BODY_CPU                       8u
#define EWP_K_TELEMETRY_BODY_TASKS                     9u
#define EWP_K_TELEMETRY_BODY_SENSORS                   10u
#define EWP_K_TELEMETRY_BODY_ACTUATORS                 11u
#define EWP_K_TELEMETRY_BODY_INFERENCE                 12u
#define EWP_K_TELEMETRY_BODY_EXT                       13u

/* ---- fixed header --------------------------------------------------------
 * memcpy() the first EWP_HEADER_LEN bytes of a payload into this struct;
 * never cast the transport buffer pointer (its alignment is not guaranteed). */
typedef struct __attribute__((packed)) {
    uint8_t  magic       ; /* @ 0 */
    uint8_t  version     ; /* @ 1 */
    uint8_t  type        ; /* @ 2 */
    uint8_t  flags       ; /* @ 3 */
    uint32_t rid         ; /* @ 4 */
    uint8_t  tool        ; /* @ 8 */
    uint8_t  status      ; /* @ 9 */
    uint16_t deadline_ms ; /* @10 */
    uint16_t body_len    ; /* @12 */
    uint16_t reserved    ; /* @14 */
} ewp_header_wire_t;

EWP_STATIC_ASSERT(sizeof(ewp_header_wire_t) == EWP_HEADER_LEN, "EWP header must be 16 bytes");
EWP_STATIC_ASSERT(offsetof(ewp_header_wire_t, magic) == 0, "layout: magic");
EWP_STATIC_ASSERT(offsetof(ewp_header_wire_t, version) == 1, "layout: version");
EWP_STATIC_ASSERT(offsetof(ewp_header_wire_t, type) == 2, "layout: type");
EWP_STATIC_ASSERT(offsetof(ewp_header_wire_t, flags) == 3, "layout: flags");
EWP_STATIC_ASSERT(offsetof(ewp_header_wire_t, rid) == 4, "layout: rid");
EWP_STATIC_ASSERT(offsetof(ewp_header_wire_t, tool) == 8, "layout: tool");
EWP_STATIC_ASSERT(offsetof(ewp_header_wire_t, status) == 9, "layout: status");
EWP_STATIC_ASSERT(offsetof(ewp_header_wire_t, deadline_ms) == 10, "layout: deadlineMs");
EWP_STATIC_ASSERT(offsetof(ewp_header_wire_t, body_len) == 12, "layout: bodyLen");
EWP_STATIC_ASSERT(offsetof(ewp_header_wire_t, reserved) == 14, "layout: reserved");

#ifdef __cplusplus
} /* extern "C" */
#endif
