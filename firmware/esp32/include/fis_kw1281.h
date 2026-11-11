#ifndef FIS_KW1281_H
#define FIS_KW1281_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#define FIS_MAX_DATA 252u
#define FIS_IDENTITY_CAP 512u
typedef enum { FIS_OK, FIS_INVALID, FIS_TIMEOUT, FIS_CANCELLED, FIS_IO, FIS_PROTOCOL, FIS_LIMIT, FIS_REJECTED, FIS_BUSY, FIS_WAIT } fis_result;
typedef struct {
 void *ctx;
 uint64_t (*now_ms)(void *);
 bool (*cancelled)(void *);
 fis_result (*slow_init)(void *, uint8_t, uint64_t);
 fis_result (*wait_ms)(void *, uint32_t, uint64_t);
 fis_result (*read_byte)(void *, uint8_t *, uint64_t);
 fis_result (*write_byte)(void *, uint8_t, uint64_t);
} fis_transport;
typedef struct { fis_transport io; uint8_t counter; bool counter_set, identified; } fis_session;
typedef struct { uint8_t bytes[FIS_IDENTITY_CAP]; size_t len; } fis_identity;
typedef struct { uint8_t bytes[FIS_MAX_DATA]; size_t len; } fis_group;
typedef enum {
 FIS_CATALOG_ACTIVE_ALLOWED,
 FIS_CATALOG_IMPLEMENTED_UNVERIFIED,
 FIS_CATALOG_METADATA_ONLY,
 FIS_CATALOG_UNKNOWN,
 FIS_CATALOG_UNSUPPORTED_CONFIRMED
} fis_catalog_status;
typedef struct {
 uint8_t group_id;
 fis_catalog_status status;
 bool physical_read_allowed;
 const char *name;
 const char *source;
 uint32_t metadata_candidate_count;
} fis_group_metadata;
#define FIS_MONITOR_MAX_GROUPS 3u
#define FIS_MONITOR_MIN_PERIOD_MS 500u
#define FIS_MONITOR_MAX_PERIOD_MS 60000u
typedef enum { FIS_MONITOR_STOPPED, FIS_MONITOR_RUNNING, FIS_MONITOR_SUSPENDED } fis_monitor_status;
typedef struct {
 uint8_t groups[FIS_MONITOR_MAX_GROUPS];
 uint64_t last_completed_ms[FIS_MONITOR_MAX_GROUPS];
 uint64_t request_started_ms;
 uint32_t min_period_ms;
 size_t group_count;
 size_t cursor;
 size_t in_flight_index;
 fis_monitor_status status;
 bool has_completed[FIS_MONITOR_MAX_GROUPS];
 bool in_flight;
 bool clock_set;
 uint64_t last_observed_ms;
} fis_monitor;
/* Call once before use (or zero-initialize); never reinitialize in-flight state. */
void fis_monitor_init(fis_monitor *);
fis_result fis_identify(fis_session *, fis_identity *, uint64_t deadline_ms);
fis_result fis_keep_alive(fis_session *, uint64_t deadline_ms);
fis_result fis_read_group(fis_session *, uint8_t group, fis_group *, uint64_t deadline_ms);
fis_result fis_catalog_lookup(uint8_t group, fis_group_metadata *out);
fis_result fis_monitor_start(fis_monitor *, const uint8_t *groups, size_t count, uint32_t min_period_ms);
fis_result fis_monitor_next(fis_monitor *, uint64_t now_ms, uint8_t *group);
fis_result fis_monitor_complete(fis_monitor *, uint8_t group, uint64_t completed_ms, fis_result request_result);
void fis_monitor_stop(fis_monitor *);
#endif
