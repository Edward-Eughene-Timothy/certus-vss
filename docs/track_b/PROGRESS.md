# Track B Engineering Progress

## Current status

Track B is being implemented in a local working copy of the team's base repository. No changes have been pushed to GitHub yet.

| Work item | Status | Evidence / next action |
|---|---|---|
| B1 — Ring buffer | Implemented; targeted tests pass | 11 targeted tests pass; fixed backing allocation is about 2.23 MiB at 36,000 frames. Integrate with B2/B3 and rerun under the target cgroup. |
| B2 — Streaming ingestion | Initial implementation; real CARLA runtime and DBC validation pending | Synthetic source, VSS normalizer, optional listen-only python-can scaffold, and CARLA actor/collision adapter. 8 ingestion tests pass. |
| B3 — Rollup worker | Not started | Confirm Track A database interface and implement one-minute batched rollups. |
| B4 — Freeze-frames and safety | Not started | Implement event-window capture and verify read-only transport boundaries. |

## B1 summary

- Replaced per-frame Python dictionary storage with a fixed-width 65-byte record in a preallocated bytearray.
- Added timestamp retention, explicit expiry for paused streams, sequence metadata, validity mask for missing signals, and rejection counters.
- Added tests for fixed allocation, count eviction, timestamp windows, expiry, out-of-order frames, invalid values, detached snapshots, unknown signals, and concurrent append attempts.
- Verification command: `PYTHONPATH=src pytest -c /dev/null -q tests/test_ring_buffer.py` — **11 passed**.
- Local full-capacity smoke test: 36,000 frames, 2,340,000 bytes fixed allocation (about 2.23 MiB), approximately 2.25 MiB process RSS increase in that run.

## Open integration question

The earlier architecture plan names `Vehicle.Powertrain.TractionBattery.Voltage` among six core signals, but the current repository's `configs/vss_signals.json` lists `Vehicle.Powertrain.ElectricMotor.CoolantTemperature` instead. Confirm the canonical signal set with Track A before implementing B2.

## Repository-level caveat

Targeted B1 tests were run with `-c /dev/null` because the existing project pytest/TOML configuration has a parsing issue. Do not interpret this as full-repository test verification. Fix or agree on that configuration separately with the team.


## B2 summary

- Added a synthetic frame iterator and real-time streaming function that append normalized six-signal frames into the B1 buffer.
- Added VSS alias validation and m/s-to-km/h conversion.
- Added an optional listen-only SocketCAN/vcan receiver that accepts an injected decoder.
- Added `carla_adapter.py` to sample CARLA actor velocity, normalize speed to km/h, append to the ring, and optionally attach a collision sensor callback.
- Combined targeted result: **19 passed** across B1 and ingestion tests.
- CARLA runtime execution, collision sensor callback in a live world, and real DBC decoding remain unverified. Battery/HVAC values remain null unless a project-specific provider supplies them.

## B3 summary (initial implementation)

- Implemented `storage/rollup_worker.py` against the existing Track A `telemetry_rollup_1m` table and its declared columns; no parallel schema was introduced.
- Aggregates completed minute buckets by VSS path: average, minimum, maximum, last-minus-first delta, valid sample count, unit, drive context, and provenance.
- Missing values are skipped rather than treated as zero. Only completed minutes are written, and existing `(vss_path, bucket_epoch_m)` rows are skipped to make periodic flushes idempotent.
- Added tests for aggregate values, metadata, incomplete-minute deferral, idempotent repeated flushes, and missing signals.
- Fixed non-breaking-but-invalid NBSP characters in `db_engine.py` and `schema.sql`; they previously caused Python parsing and SQLite schema execution failures.
- Verification: `PYTHONPATH=src pytest -c /dev/null -q tests/test_ring_buffer.py tests/test_ingestion_stream.py tests/test_rollup_worker.py` — **23 passed**.
- Integration caveat: `drive_context` defaults to `unknown`; a real drive/session context provider still needs to be agreed with Track A. The chosen `val_delta` meaning is last sample minus first sample within the minute.

## B4 — Safety events and freeze frames (initial implementation)

- Added `storage/event_capture.py` to persist safety events into Track A's `vehicle_events` table and numeric samples into `fault_freeze_frames`.
- Supports the planned default ±120-second window, relative millisecond offsets, skipping missing values, and repeatable delayed capture without duplicate samples.
- Added unit tests for event metadata, freeze-frame persistence, delayed post-event capture, idempotency, and invalid timestamps.
- Not yet wired to an automatic scheduler or a live CARLA collision sensor; caller/orchestrator must invoke event persistence and follow-up capture. Live CARLA validation remains outstanding.
