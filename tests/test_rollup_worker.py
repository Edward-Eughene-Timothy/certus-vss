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
    buffer = TelemetryRingBuffer(maxlen=100)
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
    buffer = TelemetryRingBuffer(maxlen=100)
    assert buffer.append(_frame(6000, 0, 10))
    assert buffer.append(_frame(6061, 1, 99))
    assert flush_buffer_to_rollup(buffer, db_path) == 1
    assert flush_buffer_to_rollup(buffer, db_path) == 0
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM telemetry_rollup_1m").fetchone()[0] == 1


def test_no_write_until_a_minute_is_complete(tmp_path):
    buffer = TelemetryRingBuffer(maxlen=100)
    assert buffer.append(_frame(6000, 0, 10))
    assert buffer.append(_frame(6059, 1, 20))
    assert flush_buffer_to_rollup(buffer, tmp_path / "rollups.sqlite") == 0


def test_missing_signals_are_skipped_not_counted_as_zero(tmp_path):
    buffer = TelemetryRingBuffer(maxlen=100)
    assert buffer.append(_frame(6000, 0, 10, None))
    assert buffer.append(_frame(6020, 1, 20, None))
    assert buffer.append(_frame(6061, 2, 30, None))
    assert flush_buffer_to_rollup(buffer, tmp_path / "rollups.sqlite") == 1
    with sqlite3.connect(tmp_path / "rollups.sqlite") as conn:
        assert conn.execute("SELECT vss_path, sample_count FROM telemetry_rollup_1m").fetchall() == [
            ("Vehicle.Speed", 2)
        ]

def test_stationary_vehicle_is_classified_as_idle(tmp_path):
    db_path = tmp_path / "idle.sqlite"
    buffer = TelemetryRingBuffer(maxlen=100)

    def make_frame(timestamp, sequence, speed):
        signals = {path: None for path in SIGNAL_PATHS}
        signals["Vehicle.Speed"] = speed
        signals["Vehicle.Powertrain.TractionBattery.Temperature"] = 30
        signals["Vehicle.Powertrain.TractionBattery.Current"] = 0

        return {
            "timestamp": timestamp,
            "sequence": sequence,
            "signals": signals,
        }

    assert buffer.append(make_frame(6000, 0, 0))
    assert buffer.append(make_frame(6020, 1, 0))
    assert buffer.append(make_frame(6061, 2, 0))

    assert flush_buffer_to_rollup(buffer, db_path) == 3

    with sqlite3.connect(db_path) as conn:
        contexts = conn.execute(
            "SELECT DISTINCT drive_context FROM telemetry_rollup_1m"
        ).fetchall()

    assert contexts == [("IDLE",)]

def test_stationary_vehicle_charging_is_classified_as_charging(tmp_path):
    db_path = tmp_path / "charging.sqlite"
    buffer = TelemetryRingBuffer(maxlen=100)

    def make_frame(timestamp, sequence):
        signals = {path: None for path in SIGNAL_PATHS}
        signals["Vehicle.Speed"] = 0
        signals["Vehicle.Powertrain.TractionBattery.Temperature"] = 30
        signals["Vehicle.Powertrain.TractionBattery.Current"] = -10

        return {
            "timestamp": timestamp,
            "sequence": sequence,
            "signals": signals,
        }

    assert buffer.append(make_frame(6000, 0))
    assert buffer.append(make_frame(6020, 1))
    assert buffer.append(make_frame(6061, 2))

    assert flush_buffer_to_rollup(buffer, db_path) == 3

    with sqlite3.connect(db_path) as conn:
        contexts = conn.execute(
            "SELECT DISTINCT drive_context FROM telemetry_rollup_1m"
        ).fetchall()

    assert contexts == [("CHARGING",)]

def test_steady_high_speed_is_classified_as_highway_cruise(tmp_path):
    db_path = tmp_path / "highway.sqlite"
    buffer = TelemetryRingBuffer(maxlen=100)

    def make_frame(timestamp, sequence):
        signals = {path: None for path in SIGNAL_PATHS}
        signals["Vehicle.Speed"] = 80
        signals["Vehicle.Powertrain.TractionBattery.Temperature"] = 30
        signals["Vehicle.Powertrain.TractionBattery.Current"] = 0

        return {
            "timestamp": timestamp,
            "sequence": sequence,
            "signals": signals,
        }

    assert buffer.append(make_frame(6000, 0))
    assert buffer.append(make_frame(6020, 1))
    assert buffer.append(make_frame(6061, 2))

    assert flush_buffer_to_rollup(buffer, db_path) == 3

    with sqlite3.connect(db_path) as conn:
        contexts = conn.execute(
            "SELECT DISTINCT drive_context FROM telemetry_rollup_1m"
        ).fetchall()

    assert contexts == [("HIGHWAY_CRUISE",)]

def test_stop_and_go_driving_is_classified_as_urban(tmp_path):
    db_path = tmp_path / "urban.sqlite"
    buffer = TelemetryRingBuffer(maxlen=100)

    # Keep repeated stop-and-go transitions inside minute 100.
    speeds = [30, 0, 25, 0, 20, 0]

    for sequence, speed in enumerate(speeds):
        signals = {path: None for path in SIGNAL_PATHS}
        signals["Vehicle.Speed"] = speed
        signals["Vehicle.Powertrain.TractionBattery.Temperature"] = 30
        signals["Vehicle.Powertrain.TractionBattery.Current"] = 0

        frame = {
            "timestamp": 6000 + sequence * 5,
            "sequence": sequence,
            "signals": signals,
        }
        assert buffer.append(frame)

    # This frame advances the latest timestamp enough to complete minute 100.
    signals = {path: None for path in SIGNAL_PATHS}
    signals["Vehicle.Speed"] = 10
    signals["Vehicle.Powertrain.TractionBattery.Temperature"] = 30
    signals["Vehicle.Powertrain.TractionBattery.Current"] = 0

    assert buffer.append({
        "timestamp": 6120,
        "sequence": 6,
        "signals": signals,
    })

    assert flush_buffer_to_rollup(buffer, db_path) > 0

    with sqlite3.connect(db_path) as conn:
        contexts = conn.execute(
            """SELECT DISTINCT drive_context
               FROM telemetry_rollup_1m
               WHERE bucket_epoch_m = 100"""
        ).fetchall()

    assert contexts == [("URBAN_STOP_GO",)]

def test_simulated_telemetry_reaches_persisted_rollups(tmp_path):
    import sqlite3

    from certus_vss.ingestion.sim_stream import iter_simulated_frames

    db_path = tmp_path / "simulated_rollups.sqlite"
    buffer = TelemetryRingBuffer(maxlen=100)

    # Generate deterministic frames spanning two minute buckets.
    frames = iter_simulated_frames(
        sample_hz=1,
        max_samples=62,
        start_epoch=6000,
    )
    for frame in frames:
        assert buffer.append(frame)

    inserted = flush_buffer_to_rollup(
        buffer,
        db_path,
        provenance="integration_test.simulator",
    )

    assert inserted > 0

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT bucket_epoch_m, vss_path, sample_count, provenance
            FROM telemetry_rollup_1m
            ORDER BY bucket_epoch_m, vss_path
            """
        ).fetchall()

    assert rows
    assert any(row[1] == "Vehicle.Speed" for row in rows)
    assert all(row[3] == "integration_test.simulator" for row in rows)
    assert {row[0] for row in rows} == {100}
