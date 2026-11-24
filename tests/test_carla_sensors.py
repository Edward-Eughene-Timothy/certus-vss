import sqlite3

import pytest

from certus_vss.ingestion.carla_sensors import (
    SENSOR_SIGNAL_MAP,
    make_sensor_callback,
)
from certus_vss.storage.acquisition_store import initialize_acquisition_storage


@pytest.mark.parametrize("sensor_type", list(SENSOR_SIGNAL_MAP))
def test_sensor_callback_persists_external_reference(sensor_type, tmp_path):
    db_path = tmp_path / "sensor_test.db"
    initialize_acquisition_storage(db_path)

    measurement = object()
    received = []

    def payload_sink(value):
        received.append(value)
        return "file:///sensor-data/frame-001.bin"

    callback = make_sensor_callback(
        sensor_type,
        payload_sink=payload_sink,
        timestamp_provider=lambda _: 1760000000.0,
        simulation_time_provider=lambda _: 12.5,
        actor_id=42,
        metadata_provider=lambda _: {"frame": 100, "width": 800},
        db_path=db_path,
    )

    callback(measurement)

    assert received == [measurement]

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT timestamp_utc_s, simulation_time_s, actor_id,
                   sensor_type, payload_reference, metadata_json,
                   source, provenance
            FROM acquisition_sensor_frames
            """
        ).fetchone()

    assert row is not None
    assert row[0] == 1760000000.0
    assert row[1] == 12.5
    assert row[2] == 42
    assert row[3] == sensor_type
    assert row[4] == "file:///sensor-data/frame-001.bin"
    assert '"frame":100' in row[5]
    assert row[6] == "carla"
    assert row[7] == "CARLA_PROVIDED"


def test_sensor_callback_rejects_unsupported_sensor():
    with pytest.raises(ValueError, match="Unsupported"):
        make_sensor_callback(
            "sensor.unknown",
            payload_sink=lambda _: "ref",
            timestamp_provider=lambda _: 1760000000.0,
        )


@pytest.mark.parametrize("timestamp", [0, -1, float("nan"), float("inf"), True])
def test_sensor_callback_rejects_invalid_timestamp(timestamp, tmp_path):
    db_path = tmp_path / "invalid_timestamp.db"
    initialize_acquisition_storage(db_path)

    callback = make_sensor_callback(
        "sensor.camera.rgb",
        payload_sink=lambda _: "file:///frame.bin",
        timestamp_provider=lambda _: timestamp,
        db_path=db_path,
    )

    with pytest.raises(ValueError):
        callback(object())


def test_sensor_callback_rejects_empty_payload_reference(tmp_path):
    db_path = tmp_path / "empty_reference.db"
    initialize_acquisition_storage(db_path)

    callback = make_sensor_callback(
        "sensor.lidar.ray_cast",
        payload_sink=lambda _: " ",
        timestamp_provider=lambda _: 1760000000.0,
        db_path=db_path,
    )

    with pytest.raises(ValueError, match="non-empty reference"):
        callback(object())


def test_sensor_callback_does_not_save_payload_in_database(tmp_path):
    db_path = tmp_path / "reference_only.db"
    initialize_acquisition_storage(db_path)

    payload = b"large sensor payload"

    callback = make_sensor_callback(
        "sensor.camera.rgb",
        payload_sink=lambda _: "object-store://frames/001",
        timestamp_provider=lambda _: 1760000000.0,
        db_path=db_path,
    )
    callback(payload)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT payload_reference, metadata_json
            FROM acquisition_sensor_frames
            """
        ).fetchone()

    assert row[0] == "object-store://frames/001"
    assert payload.decode() not in str(row)
