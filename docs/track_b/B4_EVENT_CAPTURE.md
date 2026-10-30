# Track B4 — Safety events and freeze-frame capture

## Implemented

- `src/certus_vss/storage/event_capture.py` persists event records into Track A's existing `vehicle_events` table.
- Available samples in the configured window (default ±120 seconds) are stored in `fault_freeze_frames` with millisecond offsets relative to the event.
- Null/non-numeric signal samples are skipped because the current Track A schema declares `raw_value` as `NOT NULL`.
- `capture_event_window()` can be called again after more telemetry arrives to add post-event samples. It checks existing `(event_id, offset_ms, vss_path)` combinations so repeated calls do not duplicate samples.
- CARLA collision callback payloads can be passed to `record_safety_event()`; default category is `collision`, trigger signal is `Vehicle.Collision`, and missing intensity becomes zero because the schema requires a numeric trigger value.
- The implementation only writes local SQLite event/freeze-frame records. It does not send vehicle commands or alter vehicle controls.

## Integration notes and limits

- Call `record_safety_event()` promptly on the collision callback to persist the event and whatever pre/post data is already retained.
- Call `capture_event_window()` later (for example after 120 seconds) to add the remaining post-event samples. A scheduler/orchestrator still needs to invoke this follow-up.
- The ring buffer retains 60 minutes by default; if the ingestion stream pauses or the buffer is cleared/overwritten before final capture, missing samples cannot be recovered from this volatile tier.
- The existing schema stores event time as integer epoch seconds. Freeze-frame offsets retain millisecond precision relative to the supplied event timestamp.
- This module records the event but does not yet automatically attach the CARLA sensor or schedule finalization; those are integration tasks.

## Validation

Targeted unit tests cover event persistence, pre/post window offsets, delayed capture, idempotent repeated capture, and invalid timestamps. Live CARLA collision testing remains environment-dependent.
