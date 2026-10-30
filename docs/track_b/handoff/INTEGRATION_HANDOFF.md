# Track B Integration Handoff

## Component map

| Concern | File / symbol | Role |
|---|---|---|
| RAM buffer | `src/certus_vss/ingestion/ring_buffer.py` — `TelemetryRingBuffer` | Validates normalized frames and stores bounded raw telemetry. |
| Synthetic source | `src/certus_vss/ingestion/sim_stream.py` — `iter_simulated_frames`, `start_simulation` | Generates six-signal frames; bounded or stop-event controlled. |
| VSS normalization | `src/certus_vss/ingestion/vss_normalizer.py` — `normalize_vss` | Maps supported aliases and converts units, including m/s to km/h for speed. |
| CAN adapter | `src/certus_vss/ingestion/can_adapter.py` | Optional CAN input; real DBC and target validation remain outstanding. |
| CARLA adapter | `src/certus_vss/ingestion/carla_adapter.py` | Converts actor velocity to speed and supports extra signals/collision callbacks. |
| Rollups | `src/certus_vss/storage/rollup_worker.py` — `flush_buffer_to_rollup` | Aggregates completed minutes and persists them to SQLite. |
| Schema/init | `src/certus_vss/storage/schema.sql`, `src/certus_vss/storage/db_engine.py` — `init_database` | Defines rollup, event, and freeze-frame tables; initializer accepts a custom DB path. |
| Event capture | `src/certus_vss/storage/event_capture.py` | Persists event and freeze-frame records. |
| Delayed capture | `src/certus_vss/storage/capture_scheduler.py` — `PostEventCaptureScheduler` | Schedules post-event capture in a background timer. |
| UI placeholder | `src/certus_vss/ui/textual_app.py` — `run_app` | Supplied contents only print an initialization message. |

## Normalized frame contract
The simulator emits dictionaries with `timestamp` (epoch seconds), `sequence` (non-negative integer), `source`, and `signals` (configured VSS path to numeric value). The ring buffer requires `timestamp` and `signals`, validates signal names/values, and maintains its own sequence.

Current six paths:
1. `Vehicle.Speed`
2. `Vehicle.Powertrain.TractionBattery.Temperature`
3. `Vehicle.Powertrain.TractionBattery.Current`
4. `Vehicle.Powertrain.TractionBattery.StateOfCharge`
5. `Vehicle.Cabin.HVAC.PowerDraw`
6. `Vehicle.Powertrain.ElectricMotor.CoolantTemperature`

**Team confirmation required:** an earlier architecture note reportedly referenced a battery voltage path. Agree on the canonical signal set before integration; do not assume this list is final.

## Rollup database contract
Table: `telemetry_rollup_1m`; primary key `(bucket_epoch_m, vss_path)`. Required fields: `bucket_epoch_m`, `vss_path`, `val_avg`, `val_min`, `val_max`, `val_delta`, `sample_count`, `unit`, `drive_context`, `provenance`.

`val_delta` means last valid sample minus first valid sample in timestamp order. The worker skips existing keys on repeated flushes. For temporary/custom databases, pass `db_path` explicitly to `flush_buffer_to_rollup()`. The `get_writer_connection()` and `get_readonly_connection()` helpers use a module-level database path; verify path consistency before mixing them with custom-path workflows.

## Event / freeze-frame contract
Tables: `vehicle_events`, `fault_freeze_frames`. Freeze-frame rows reference `event_id`, use `offset_ms`, and store `vss_path` and `raw_value`. The capture implementation has been described as supporting a configurable pre/post window (default ±120 seconds); unavailable/non-numeric values are skipped because `raw_value` is `NOT NULL`.

The scheduler starts a background timer for the post-event delay. `shutdown()` cancels pending timers; it does not guarantee unfinished post-event captures complete. Agree shutdown semantics with the application owner.

## Proposed lifecycle integration (not yet implemented/verified)
1. Initialize the schema at the configured DB path.
2. Construct the buffer with capacity/retention approved for the target.
3. Start the selected source (synthetic first; CARLA/CAN only after configuration and validation).
4. Periodically call `flush_buffer_to_rollup(buffer, db_path=...)`.
5. Coordinate stop signals and thread joins.
6. Perform a final flush without treating an incomplete minute as complete.
7. Apply an agreed policy to pending event-capture timers.
8. Log failures and expose health/status.
9. Add an executable entry point once CLI/UI behavior is agreed.

This is a proposal, not a claim that these steps are already wired together.
