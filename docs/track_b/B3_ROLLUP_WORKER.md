# B3 — One-minute rollup worker

## Interface

`flush_buffer_to_rollup(buffer, db_path=DB_PATH, *, drive_context="unknown", provenance="certus_vss.ring_buffer", schema_path=SCHEMA_PATH) -> int`

The function reads a detached snapshot from `TelemetryRingBuffer`, aggregates by minute and configured VSS path, persists to Track A's existing `telemetry_rollup_1m` table, and returns the number of inserted rows.

## Rollup semantics

- `bucket_epoch_m`: Unix epoch minute (`floor(timestamp / 60)`).
- `val_avg`, `val_min`, `val_max`: computed from valid numeric samples only.
- `val_delta`: last valid sample minus first valid sample in timestamp order for that signal and minute.
- `sample_count`: number of valid numeric samples for that signal in the bucket.
- `unit`: configured unit for the VSS path.
- `drive_context`, `provenance`: caller-provided metadata.
- Null/missing signals are not interpreted as zero and do not increase sample count.
- A minute is eligible once a newer minute has begun; the current, potentially incomplete minute is not written.
- A row already present for `(vss_path, bucket_epoch_m)` is not overwritten, so repeated flushes do not double-count or revise stored buckets.

## Operational notes

Run the flush on a periodic worker cadence (for example, every few seconds). Ensure it runs while a minute boundary is crossed and while the 60-minute buffer still retains the data. The current default `drive_context="unknown"` is a safe placeholder; session/drive context wiring remains an integration task with Track A.

## Validation

`PYTHONPATH=src pytest -c /dev/null -q tests/test_ring_buffer.py tests/test_ingestion_stream.py tests/test_rollup_worker.py`
