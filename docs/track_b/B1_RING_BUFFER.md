# Track B — B1 Ring Buffer

## Status

**Implemented; targeted tests pass.** This is a local working-copy result. It has not yet been committed or merged into the shared GitHub repository. Integration with B2/B3 and full-repository tests remain pending.

## Scope

`src/certus_vss/ingestion/ring_buffer.py` provides a thread-safe, volatile Tier-1 buffer for normalized telemetry. The default is 36,000 frames (60 minutes at 10 Hz) for the six VSS signals currently listed in `configs/vss_signals.json`.

## Compact fixed-record design

Each frame is encoded into a 65-byte fixed-width record:

- timestamp: 64-bit float, epoch seconds
- sequence: unsigned 64-bit integer
- six signal values: 64-bit floats
- one validity bitmask byte, allowing missing signal values without storing NaN

The backing store is a preallocated `bytearray`, not a `deque` of per-frame Python dictionaries. At the default capacity it allocates exactly `36,000 * 65 = 2,340,000` bytes (about 2.23 MiB), below the 24 MiB Track B ceiling. Retrieved snapshots are converted to dictionaries on demand; callers should avoid retaining unnecessary snapshots in the 2 GB target process.

## Signal contract

The buffer accepts a mapping with `timestamp` and a `signals` mapping. Each configured signal value may be a numeric scalar, `null`, or a mapping containing a numeric `value`. Unknown signal paths are rejected so the fixed record layout remains unambiguous.

Configured paths are read from the repository's current `configs/vss_signals.json`:

- `Vehicle.Speed`
- `Vehicle.Powertrain.TractionBattery.Temperature`
- `Vehicle.Powertrain.TractionBattery.Current`
- `Vehicle.Powertrain.TractionBattery.StateOfCharge`
- `Vehicle.Cabin.HVAC.PowerDraw`
- `Vehicle.Powertrain.ElectricMotor.CoolantTemperature`

**Integration note:** the earlier CertusVSS plan text lists traction-battery voltage among its six core paths, while the current repository catalog lists electric-motor coolant temperature instead. Track A should confirm which signal set is authoritative before B2 integration.

## Retention and ordering

- Capacity is bounded to 36,000 records by default.
- Stream timestamps must be nondecreasing; out-of-order frames are rejected and counted.
- As stream time advances, frames strictly older than `latest_timestamp - retention_seconds` are expired.
- `expire(now)` permits a caller to expire records while ingestion is paused. `now` must use the same timestamp domain as the stream.
- If no new frames arrive and no caller invokes `expire`, records remain until the next append or explicit expiry call. B2 should schedule expiry if wall-clock expiry during a pause is required.

## API

- `TelemetryRingBuffer(maxlen=36000, max_bytes=24 * 1024 * 1024, retention_seconds=3600)`
- `append(frame) -> bool`: accepts valid configured frames, rejects invalid/out-of-order frames, and evicts oldest frames as needed.
- `snapshot()`: chronological detached frames.
- `get_since(timestamp)`: frames at or after a timestamp.
- `get_window(start, end)`: frames in the inclusive time interval.
- `expire(now)`: expire frames older than the configured retention window relative to `now`.
- `clear()`, `len(buffer)`, `payload_bytes`, `utilization`, `record_bytes`, and `latest_timestamp`.

## Verification

Command used:

```bash
PYTHONPATH=src pytest -c /dev/null -q tests/test_ring_buffer.py
```

Result: **11 passed**.

A local 36,000-frame smoke test using six numeric signals reported:

- retained frames: 36,000
- fixed-record allocation: 2,340,000 bytes (about 2.23 MiB)
- process RSS increase during the test: about 2.25 MiB in this run

RSS is environment-dependent and the reported delta is a single local measurement, not a hard real-time guarantee. The fixed backing allocation is deterministic; the 24 MiB configuration limit applies to that allocation. The full application must still be measured inside the project's 2 GB / 2-core cgroup.

## Known limitations / next steps

- The buffer is a single ordered stream; it rejects out-of-order timestamps instead of reordering samples.
- This buffer only stores the six configured numeric VSS paths. Large camera/LiDAR payloads and diagnostic event metadata belong in separate source/event handling paths.
- Explicit expiry must be scheduled if retention should advance while ingestion is paused.
- This component does not implement freeze-frame capture or hardware data-diode isolation; those remain B4 responsibilities.
- Targeted tests pass with project pytest configuration bypassed. The repository's existing `pyproject.toml` parsing issue must be addressed with the team before the full test suite is considered verified.
