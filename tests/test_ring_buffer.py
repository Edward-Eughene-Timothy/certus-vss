from datetime import datetime, timezone
import threading

import pytest

from certus_vss.ingestion.ring_buffer import SIGNAL_PATHS, TelemetryRingBuffer


def frame(t, speed=10.0, **overrides):
    signals = {path: None for path in SIGNAL_PATHS}
    signals["Vehicle.Speed"] = speed
    signals.update(overrides)
    return {"timestamp": t, "signals": signals}


def test_fixed_storage_is_below_24_mib_for_default_capacity():
    ring = TelemetryRingBuffer()
    assert ring.payload_bytes <= 24 * 1024 * 1024
    assert ring.payload_bytes == 36_000 * ring.record_bytes
    assert ring.record_bytes <= 80


def test_retains_latest_frames_when_count_limit_reached():
    ring = TelemetryRingBuffer(maxlen=3, max_bytes=3 * 65)
    for t in range(5):
        assert ring.append(frame(t))
    assert len(ring) == 3
    assert [x["timestamp"] for x in ring.snapshot()] == [2.0, 3.0, 4.0]
    assert ring.evicted_frames == 2


def test_timestamp_window_includes_boundaries():
    ring = TelemetryRingBuffer()
    ring.append(frame("2026-10-22T10:00:00Z"))
    ring.append(frame("2026-10-22T10:00:01Z"))
    ring.append(frame("2026-10-22T10:00:02Z"))
    result = ring.get_window("2026-10-22T10:00:01Z", "2026-10-22T10:00:02Z")
    assert len(result) == 2


def test_naive_iso_timestamp_is_treated_as_utc():
    ring = TelemetryRingBuffer()
    ring.append(frame("2026-10-22T10:00:00"))
    cutoff = datetime(2026, 10, 22, 10, tzinfo=timezone.utc).timestamp()
    assert len(ring.get_since(cutoff)) == 1


def test_expiration_occurs_when_stream_time_advances():
    ring = TelemetryRingBuffer(maxlen=10, max_bytes=650, retention_seconds=3600)
    assert ring.append(frame(1000))
    assert ring.append(frame(4599))
    assert len(ring) == 2
    assert ring.append(frame(4601))
    assert [f["timestamp"] for f in ring.snapshot()] == [4599.0, 4601.0]
    assert ring.expired_frames == 1


def test_explicit_expire_works_when_stream_is_paused():
    ring = TelemetryRingBuffer(maxlen=10, max_bytes=650, retention_seconds=3600)
    ring.append(frame(1000))
    ring.append(frame(2000))
    assert ring.expire(5601) == 2
    assert len(ring) == 0


def test_out_of_order_frame_is_rejected_without_mutating_buffer():
    ring = TelemetryRingBuffer(maxlen=10, max_bytes=650)
    ring.append(frame(10))
    assert not ring.append(frame(9))
    assert [f["timestamp"] for f in ring.snapshot()] == [10.0]
    assert ring.rejected_out_of_order == 1


def test_missing_or_invalid_timestamp_and_signal_rejected():
    ring = TelemetryRingBuffer()
    assert not ring.append({"signals": {}})
    assert not ring.append({"timestamp": "not-a-time", "signals": {}})
    assert not ring.append(frame(1, **{"Vehicle.Speed": float("nan")}))
    assert ring.rejected_invalid == 3


def test_snapshots_are_detached_from_input_and_buffer():
    ring = TelemetryRingBuffer()
    original = frame(1)
    ring.append(original)
    original["signals"]["Vehicle.Speed"] = 999
    returned = ring.snapshot()
    assert returned[0]["signals"]["Vehicle.Speed"] == 10.0
    returned[0]["signals"]["Vehicle.Speed"] = 123
    assert ring.snapshot()[0]["signals"]["Vehicle.Speed"] == 10.0


def test_unknown_signals_rejected_to_keep_fixed_layout():
    ring = TelemetryRingBuffer()
    assert not ring.append({"timestamp": 1, "signals": {"Vehicle.Unknown": 4}})
    assert len(ring) == 0


def test_concurrent_appends_remain_bounded():
    ring = TelemetryRingBuffer(maxlen=500, max_bytes=500 * 65)
    def writer(start):
        for n in range(100):
            # Give each thread a disjoint but ordered range; this test exercises
            # concurrency safety rather than cross-thread timestamp ordering.
            ring.append(frame(start + n))
    threads = [threading.Thread(target=writer, args=(i * 100,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(ring) <= 500
    assert ring.payload_bytes <= 24 * 1024 * 1024
