# Track B Status — Telemetry Ingestion & Safety

## Executive summary
Track B has core components for bounded in-memory telemetry, synthetic and adapter-based ingestion, one-minute SQLite rollups, and safety-event/freeze-frame persistence. The reported test suite passes 62 tests. The implementation is suitable for a **provisional interface-level handoff**, while final completion remains pending.

## Component status

| Area | Current reported state | Evidence / limitation |
|---|---|---|
| B1 — Ring buffer | Implemented | Fixed-width records in bounded bytearray; append, snapshot, window queries, retention expiry, rejection counters. |
| B2 — Ingestion | Implemented in part | Synthetic stream, VSS normalizer, optional CAN adapter, and CARLA adapter exist. Live CARLA and real DBC/CAN validation are not confirmed. |
| B3 — Rollups | Implemented and tested | `flush_buffer_to_rollup()` aggregates completed minute buckets and persists to `telemetry_rollup_1m`; repeat flushes skip existing keys. |
| B4 — Event capture | Implemented and tested | Event persistence, freeze-frame capture, and delayed post-event scheduler exist. Live event-source validation is not confirmed. |
| Application lifecycle | Not yet verified as integrated | Prior source search found component definitions but no production call sites for simulator startup, rollup flush, safety-event recording, or CARLA collision sensor attachment. |
| Track A compatibility | Pending confirmation | Confirm canonical signal set, schema/interface compatibility, and AI-hook integration. |
| Memory target | Partially evidenced | Ring-buffer payload size is predictable; full application/cgroup memory measurement remains outstanding. |
| Documentation | Handoff in progress | B1–B4 documents exist, but `PROGRESS.md` was reported stale. |

## Storage tiers
- **Tier 1:** volatile raw telemetry in RAM.
- **Tier 2:** completed one-minute statistical rollups in SQLite.
- **Tier 3:** high-resolution freeze-frames around safety events.
- **Tier 4:** discrete event records in SQLite.

The project README describes the principle as “SQL Computes, SLM Explains,” with deterministic, privacy-first vehicle telemetry.

## Ring-buffer sizing facts
The record format is `<dQ6dB`, reported as 65 bytes per frame. At 36,000 frames, payload allocation is 2,340,000 bytes (approximately 2.23 MiB), below the configured 24 MiB payload ceiling. This is backing payload size, not process RSS or total application memory.

## Rollup behavior and caveats
- Buckets use `floor(timestamp / 60)`.
- Only completed minute buckets are persisted; the newest incomplete bucket is withheld.
- Missing/non-numeric values are skipped per signal.
- `val_delta` is the last valid sample minus the first valid sample in timestamp order.
- Existing `(bucket_epoch_m, vss_path)` rows are skipped on repeat flushes.
- Drive-context classification uses heuristic thresholds and is not vehicle-calibrated.
- The worker snapshots retained buffer data. Frames evicted/expired before flushing are no longer available to that rollup; verify flush cadence against retention.

## Reported test evidence
Latest reported full-suite result: `python -m pytest -q` → **62 passed in 2.92s**. Reported added coverage includes simulator-to-rollup persistence and CARLA collision callback wiring to event persistence. This is reported evidence, not a fresh rerun for this package.

## Completion statement
**Track B is not yet confirmed 100% complete.** It can be handed off provisionally for interface-level integration, with open items tracked explicitly.
