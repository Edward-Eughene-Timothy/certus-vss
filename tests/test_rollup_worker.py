import sqlite3

from certus_vss.ingestion.ring_buffer import SIGNAL_PATHS, TelemetryRingBuffer
from certus_vss.storage.rollup_worker import flush_buffer_to_rollup


def _frame(timestamp, sequence, speed, battery_temp=None):
    signals = {path: None for path in SIGNAL_PATHS}
    signals["Vehicle.Speed"] = speed
    signals["Vehicle.Powertrain.TractionBattery.Temperature"] = battery_temp
    return {"timestamp": timestamp, "sequence": sequence, "signals": signals}


def test_rollup_writes_completed_minutes_and_metadata(tmp_path):
    db_path = tmp_path / "rollups.sqlite"
    buffer = TelemetryRingBuffer(maxlen=20)
    # Minute 100 is complete; minute 101 is still in progress at t=6101.
    assert buffer.append(_frame(6000, 0, 10, 30))
    assert buffer.append(_frame(6020, 1, 20, 32))
    assert buffer.append(_frame(6060, 2, 99, 40))
    assert buffer.append(_frame(6101, 3, 100, 42))

    inserted = flush_buffer_to_rollup(
        buffer, db_path, drive_context="test_drive", provenance="test_source"
    )
    assert inserted == 2
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT bucket_epoch_m, vss_path, val_avg, val_min, val_max, val_delta, "
            "sample_count, unit, drive_context, provenance "
            "FROM telemetry_rollup_1m ORDER BY vss_path"
        ).fetchall()
    assert len(rows) == 2
    speed = next(row for row in rows if row[1] == "Vehicle.Speed")
    assert speed == (100, "Vehicle.Speed", 15.0, 10.0, 20.0, 10.0, 2,
                     "km/h", "test_drive", "test_source")
    temp = next(row for row in rows if row[1].endswith("TractionBattery.Temperature"))
    assert temp[2:7] == (31.0, 30.0, 32.0, 2.0, 2)


def test_repeat_flush_is_idempotent(tmp_path):
    db_path = tmp_path / "rollups.sqlite"
    buffer = TelemetryRingBuffer(maxlen=20)
    assert buffer.append(_frame(6000, 0, 10))
    assert buffer.append(_frame(6061, 1, 99))
    assert flush_buffer_to_rollup(buffer, db_path) == 1
    assert flush_buffer_to_rollup(buffer, db_path) == 0
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM telemetry_rollup_1m").fetchone()[0] == 1


def test_no_write_until_a_minute_is_complete(tmp_path):
    buffer = TelemetryRingBuffer(maxlen=20)
    assert buffer.append(_frame(6000, 0, 10))
    assert buffer.append(_frame(6059, 1, 20))
    assert flush_buffer_to_rollup(buffer, tmp_path / "rollups.sqlite") == 0


def test_missing_signals_are_skipped_not_counted_as_zero(tmp_path):
    buffer = TelemetryRingBuffer(maxlen=20)
    assert buffer.append(_frame(6000, 0, 10, None))
    assert buffer.append(_frame(6020, 1, 20, None))
    assert buffer.append(_frame(6061, 2, 30, None))
    assert flush_buffer_to_rollup(buffer, tmp_path / "rollups.sqlite") == 1
    with sqlite3.connect(tmp_path / "rollups.sqlite") as conn:
        assert conn.execute("SELECT vss_path, sample_count FROM telemetry_rollup_1m").fetchall() == [
            ("Vehicle.Speed", 2)
        ]
