import sqlite3
import pytest

from certus_vss.storage.acquisition_store import initialize_acquisition_storage
from certus_vss.ingestion.acquisition_catalog import Availability, Provenance

from certus_vss.ingestion.acquisition_records import (
    make_acquisition_record,
)
from certus_vss.storage.acquisition_store import (
    save_acquisition_event,
    save_acquisition_sample,
    save_sensor_frame_reference,
)


def test_sample_write_preserves_value_and_metadata(tmp_path):
    db = tmp_path / "acquisition.sqlite"
    initialize_acquisition_storage(db)
    record = make_acquisition_record(
        timestamp_utc_s=1700000000.0,
        signal="vehicle.speed",
        value=36.0,
        unit="km/h",
        source="carla",
        provenance=Provenance.CARLA_CALCULATED,
        availability=Availability.AVAILABLE,
    )
    sample_id = save_acquisition_sample(
        record, db, simulation_time_s=12.5, actor_id=42
    )

    with sqlite3.connect(db) as conn:
        row = conn.execute(
            """SELECT timestamp_utc_s, simulation_time_s, actor_id,
                      signal, value_json, unit, source, provenance, availability
               FROM acquisition_samples WHERE sample_id=?""",
            (sample_id,),
        ).fetchone()

    assert row == (
        1700000000.0, 12.5, 42, "vehicle.speed", "36.0",
        "km/h", "carla", "CALCULATED_FROM_CARLA", "AVAILABLE",
    )


def test_unavailable_sample_is_stored_as_sql_null(tmp_path):
    db = tmp_path / "acquisition.sqlite"
    initialize_acquisition_storage(db)
    record = make_acquisition_record(
        timestamp_utc_s=1700000000.0,
        signal="vehicle.speed",
        value=None,
        unit="km/h",
        source="carla",
        provenance=Provenance.NOT_PROVIDED,
        availability=Availability.UNAVAILABLE,
    )

    sample_id = save_acquisition_sample(record, db)

    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT value_json FROM acquisition_samples WHERE sample_id=?",
            (sample_id,),
        ).fetchone()

    assert row == (None,)


def test_event_can_be_stored_without_trigger_value(tmp_path):
    db = tmp_path / "acquisition.sqlite"
    initialize_acquisition_storage(db)
    event_id = save_acquisition_event(
        timestamp_utc_s=1700000000.25,
        event_type="collision",
        event={"other_actor_id": 42},
        source="CARLA",
        provenance="CARLA_MEASURED",
        db_path=db,
        simulation_time_s=12.5,
        actor_id=7,
    )

    with sqlite3.connect(db) as conn:
        row = conn.execute(
            """SELECT timestamp_utc_s, simulation_time_s, actor_id,
                      event_type, event_json, source, provenance
               FROM acquisition_events WHERE event_id=?""",
            (event_id,),
        ).fetchone()

    assert row == (
        1700000000.25, 12.5, 7, "collision",
        '{"other_actor_id":42}', "CARLA", "CARLA_MEASURED",
    )


def test_event_details_can_be_sql_null(tmp_path):
    db = tmp_path / "acquisition.sqlite"
    initialize_acquisition_storage(db)
    event_id = save_acquisition_event(
        timestamp_utc_s=1700000000.0,
        event_type="lane_invasion",
        event=None,
        source="CARLA",
        provenance="CARLA_MEASURED",
        db_path=db,
    )

    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT event_json FROM acquisition_events WHERE event_id=?",
            (event_id,),
        ).fetchone()

    assert row == (None,)


def test_sensor_frame_stores_reference_not_payload(tmp_path):
    db = tmp_path / "acquisition.sqlite"
    initialize_acquisition_storage(db)
    frame_id = save_sensor_frame_reference(
        timestamp_utc_s=1700000000.0,
        sensor_type="sensor.camera.rgb",
        payload_reference="sensors/rgb/frame_0001.bin",
        source="CARLA",
        provenance="CARLA_MEASURED",
        db_path=db,
        metadata={"width": 800, "height": 600},
    )

    with sqlite3.connect(db) as conn:
        row = conn.execute(
            """SELECT sensor_type, payload_reference, metadata_json
               FROM acquisition_sensor_frames WHERE frame_id=?""",
            (frame_id,),
        ).fetchone()

    assert row == (
        "sensor.camera.rgb",
        "sensors/rgb/frame_0001.bin",
        '{"height":600,"width":800}',
    )


def test_diagnostic_tables_remain_separate(tmp_path):
    db = tmp_path / "acquisition.sqlite"
    initialize_acquisition_storage(db)
    save_acquisition_event(
        timestamp_utc_s=1700000000.0,
        event_type="collision",
        event=None,
        source="CARLA",
        provenance="CARLA_MEASURED",
        db_path=db,
    )

    with sqlite3.connect(db) as conn:
        acquisition_count = conn.execute(
            "SELECT COUNT(*) FROM acquisition_events"
        ).fetchone()[0]
        diagnostic_count = conn.execute(
            "SELECT COUNT(*) FROM vehicle_events"
        ).fetchone()[0]

    assert acquisition_count == 1
    assert diagnostic_count == 0


from certus_vss.storage.acquisition_store import save_acquisition_samples


def test_batch_save_persists_all_records(tmp_path):
    from certus_vss.ingestion.acquisition_records import make_acquisition_record

    db_path = tmp_path / "batch.db"
    initialize_acquisition_storage(db_path)

    records = [
        make_acquisition_record(
            timestamp_utc_s=1760000000.0 + i,
            signal="vehicle.speed",
            value=30.0 + i,
            unit="km/h",
            source="carla",
            provenance=Provenance.CARLA_CALCULATED,
            availability=Availability.AVAILABLE,
        )
        for i in range(3)
    ]

    ids = save_acquisition_samples(
        records, db_path, simulation_time_s=12.5, actor_id=42
    )

    assert len(ids) == 3
    assert len(set(ids)) == 3

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT signal, simulation_time_s, actor_id FROM acquisition_samples"
        ).fetchall()

    assert len(rows) == 3
    assert all(row[0] == "vehicle.speed" for row in rows)
    assert all(row[1] == 12.5 and row[2] == 42 for row in rows)


def test_batch_save_empty_list_returns_empty(tmp_path):
    db_path = tmp_path / "empty_batch.db"
    initialize_acquisition_storage(db_path)

    assert save_acquisition_samples([], db_path) == []


def test_batch_save_rejects_invalid_record_before_writing(tmp_path):
    from certus_vss.ingestion.acquisition_records import make_acquisition_record

    db_path = tmp_path / "invalid_batch.db"
    initialize_acquisition_storage(db_path)

    valid_record = make_acquisition_record(
        timestamp_utc_s=1760000000.0,
        signal="vehicle.speed",
        value=30.0,
        unit="km/h",
        source="carla",
        provenance=Provenance.CARLA_CALCULATED,
        availability=Availability.AVAILABLE,
    )

    with pytest.raises(TypeError):
        save_acquisition_samples([valid_record, object()], db_path)

    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM acquisition_samples"
        ).fetchone()[0]

    assert count == 0


def test_batch_save_rolls_back_on_database_failure(tmp_path):
    from certus_vss.ingestion.acquisition_records import make_acquisition_record

    db_path = tmp_path / "rollback_batch.db"
    initialize_acquisition_storage(db_path)

    record = make_acquisition_record(
        timestamp_utc_s=1760000000.0,
        signal="vehicle.speed",
        value=30.0,
        unit="km/h",
        source="carla",
        provenance=Provenance.CARLA_CALCULATED,
        availability=Availability.AVAILABLE,
    )

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TRIGGER reject_batch_insert
            BEFORE INSERT ON acquisition_samples
            BEGIN
                SELECT RAISE(ABORT, 'test failure');
            END;
            """
        )

    with pytest.raises(sqlite3.IntegrityError):
        save_acquisition_samples([record], db_path)

    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM acquisition_samples"
        ).fetchone()[0]

    assert count == 0
