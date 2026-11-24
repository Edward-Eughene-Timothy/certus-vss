import sqlite3
from types import SimpleNamespace as NS

import pytest

from certus_vss.ingestion.carla_events import (
    normalize_collision_event,
    normalize_lane_invasion_event,
    make_event_callback,
)
from certus_vss.storage.acquisition_store import (
    initialize_acquisition_storage,
)


def test_normalizes_collision_event():
    event = NS(
        normal_impulse=NS(x=3.0, y=4.0, z=0.0),
        other_actor=NS(id=99),
    )

    result = normalize_collision_event(
        event,
        timestamp_utc_s=1790000000.0,
        simulation_time_s=12.5,
        actor_id=42,
    )

    assert result["event_type"] == "collision"
    assert result["event"]["other_actor_id"] == 99
    assert result["event"]["normal_impulse"] == {
        "x": 3.0, "y": 4.0, "z": 0.0
    }
    assert result["event"]["impulse_magnitude"] == 5.0
    assert result["simulation_time_s"] == 12.5
    assert result["actor_id"] == 42


def test_normalizes_lane_invasion_event():
    event = NS(
        crossed_lane_markings=[
            NS(type="Solid"),
            NS(type="Broken"),
        ]
    )

    result = normalize_lane_invasion_event(
        event,
        timestamp_utc_s=1790000000.0,
        actor_id=42,
    )

    assert result["event_type"] == "lane_invasion"
    assert result["event"]["crossed_lane_markings"] == [
        "Solid", "Broken"
    ]


def test_collision_with_missing_impulse_keeps_null():
    event = NS(other_actor=None)

    result = normalize_collision_event(
        event,
        timestamp_utc_s=1790000000.0,
    )

    assert result["event"]["normal_impulse"] is None
    assert result["event"]["impulse_magnitude"] is None
    assert result["event"]["other_actor_id"] is None


def test_event_normalizers_reject_invalid_timestamps():
    event = NS(normal_impulse=None, other_actor=None)

    for timestamp in (0, -1, float("nan"), float("inf"), True):
        with pytest.raises(ValueError):
            normalize_collision_event(
                event,
                timestamp_utc_s=timestamp,
            )


def test_collision_callback_persists_event(tmp_path):
    db_path = tmp_path / "carla_events.db"
    initialize_acquisition_storage(db_path)

    captured = []
    callback = make_event_callback(
        "collision",
        actor_id=42,
        simulation_time_provider=lambda: 12.5,
        on_event=captured.append,
        db_path=db_path,
    )

    callback(
        NS(
            normal_impulse=NS(x=0.0, y=6.0, z=8.0),
            other_actor=NS(id=99),
        )
    )

    assert len(captured) == 1
    assert captured[0]["event_type"] == "collision"

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT timestamp_utc_s, simulation_time_s, actor_id,
                   event_type, event_json, source, provenance
            FROM acquisition_events
            """
        ).fetchone()

    assert row is not None
    assert row[1] == 12.5
    assert row[2] == 42
    assert row[3] == "collision"
    assert '"other_actor_id":99' in row[4]
    assert row[5] == "carla"
    assert row[6] == "CARLA_PROVIDED"


def test_unsupported_event_kind_is_rejected():
    with pytest.raises(ValueError):
        make_event_callback("vehicle_reset")

def test_lane_invasion_callback_persists_event(tmp_path):
    db_path = tmp_path / "lane_events.db"
    initialize_acquisition_storage(db_path)

    callback = make_event_callback(
        "lane_invasion",
        actor_id=42,
        simulation_time_provider=lambda: 15.0,
        db_path=db_path,
    )

    callback(
        NS(
            crossed_lane_markings=[
                NS(type="Solid"),
                NS(type="Broken"),
            ]
        )
    )

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT simulation_time_s, actor_id, event_type, event_json
            FROM acquisition_events
            """
        ).fetchone()

    assert row is not None
    assert row[0] == 15.0
    assert row[1] == 42
    assert row[2] == "lane_invasion"
    assert "Solid" in row[3]
    assert "Broken" in row[3]