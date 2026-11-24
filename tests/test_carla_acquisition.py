from types import SimpleNamespace as NS

import pytest
import json
import sqlite3

from certus_vss.storage.acquisition_store import (
    initialize_acquisition_storage,
    save_acquisition_sample,
)
from certus_vss.ingestion.carla_acquisition import (
    collect_carla_observations,
)
from certus_vss.ingestion.ring_buffer import SIGNAL_PATHS
from certus_vss.ingestion.acquisition_catalog import ACQUISITION_CATALOG


class FakeVehicle:
    id = 42

    def __init__(self):
        self.control_reads = 0
        self.physics_reads = 0
        self.forbidden_calls = []

    def get_velocity(self):
        return NS(x=3.0, y=4.0, z=0.0)

    def get_acceleration(self):
        return NS(x=0.1, y=0.2, z=0.0)

    def get_angular_velocity(self):
        return NS(x=0.0, y=0.0, z=1.5)

    def get_transform(self):
        return NS(
            location=NS(x=10.0, y=20.0, z=1.0),
            rotation=NS(pitch=0.0, yaw=90.0, roll=0.0),
        )

    def get_control(self):
        self.control_reads += 1
        return NS(
            throttle=0.5,
            brake=0.0,
            steer=-0.1,
            gear=2,
            hand_brake=False,
            reverse=False,
        )

    def get_physics_control(self):
        self.physics_reads += 1
        return NS(
            mass=1500.0,
            drag_coefficient=0.3,
            use_sweep_wheel_collision=True,
        )

    def apply_control(self, *_args, **_kwargs):
        self.forbidden_calls.append("apply_control")

    def set_transform(self, *_args, **_kwargs):
        self.forbidden_calls.append("set_transform")

    def set_physics_control(self, *_args, **_kwargs):
        self.forbidden_calls.append("set_physics_control")


class FakeWorld:
    def get_snapshot(self):
        return NS(timestamp=NS(elapsed_seconds=12.5))

    def get_map(self):
        return NS(name="Town03")

    def get_weather(self):
        return NS(cloudiness=20.0, precipitation=0.0, wetness=5.0)


def test_collects_expected_observations():
    records, metadata = collect_carla_observations(
        FakeWorld(),
        FakeVehicle(),
        timestamp_utc_s=1790000000.0,
    )

    by_signal = {record.signal: record for record in records}

    assert len(records) == 15
    assert by_signal["vehicle.speed"].value == 18.0
    assert by_signal["vehicle.control.throttle"].value == 0.5
    assert by_signal["environment.map"].value == "Town03"
    assert metadata["actor_id"] == 42
    assert metadata["simulation_time_s"] == 12.5


def test_reads_control_and_physics_without_commanding_vehicle():
    vehicle = FakeVehicle()

    collect_carla_observations(
        FakeWorld(),
        vehicle,
        timestamp_utc_s=1790000000.0,
    )

    assert vehicle.control_reads == 1
    assert vehicle.physics_reads == 1
    assert vehicle.forbidden_calls == []


def test_rejects_invalid_timestamps():
    for timestamp in (0, -1, float("nan"), float("inf"), True):
        with pytest.raises(ValueError):
            collect_carla_observations(
                FakeWorld(),
                FakeVehicle(),
                timestamp_utc_s=timestamp,
            )


def test_preserves_separate_utc_and_simulation_timestamps():
    _, metadata = collect_carla_observations(
        FakeWorld(),
        FakeVehicle(),
        timestamp_utc_s=1790000000.0,
    )

    assert metadata["timestamp_utc_s"] == 1790000000.0
    assert metadata["simulation_time_s"] == 12.5

def test_diagnostic_catalog_remains_limited_to_six_signals():
    expected_paths = {
        "Vehicle.Speed",
        "Vehicle.Powertrain.TractionBattery.Temperature",
        "Vehicle.Powertrain.TractionBattery.Current",
        "Vehicle.Powertrain.TractionBattery.StateOfCharge",
        "Vehicle.Cabin.HVAC.PowerDraw",
        "Vehicle.Powertrain.ElectricMotor.CoolantTemperature",
    }

    assert set(SIGNAL_PATHS) == expected_paths
    assert len(SIGNAL_PATHS) == 6

def test_missing_optional_carla_fields_are_skipped():
    class MinimalVehicle:
        id = 7

        def get_velocity(self):
            return NS(x=1.0, y=0.0, z=0.0)

    class MinimalWorld:
        def get_snapshot(self):
            return NS(timestamp=NS(elapsed_seconds=3.0))

    records, metadata = collect_carla_observations(
        MinimalWorld(),
        MinimalVehicle(),
        timestamp_utc_s=1790000000.0,
    )

    by_signal = {record.signal: record for record in records}

    assert "vehicle.speed" in by_signal
    assert by_signal["vehicle.speed"].value == 3.6
    assert "vehicle.control.throttle" not in by_signal
    assert "vehicle.physics" not in by_signal
    assert "environment.map" not in by_signal
    assert metadata["actor_id"] == 7


def test_non_finite_velocity_is_not_recorded():
    class InvalidVelocityVehicle:
        id = 8

        def get_velocity(self):
            return NS(x=float("nan"), y=0.0, z=0.0)

    class MinimalWorld:
        def get_snapshot(self):
            return NS(timestamp=NS(elapsed_seconds=4.0))

    records, _ = collect_carla_observations(
        MinimalWorld(),
        InvalidVelocityVehicle(),
        timestamp_utc_s=1790000000.0,
    )

    signals = {record.signal for record in records}

    assert "vehicle.velocity" not in signals
    assert "vehicle.speed" not in signals

def test_carla_observations_persist_with_metadata(tmp_path):
    db_path = tmp_path / "carla_acquisition_test.db"
    initialize_acquisition_storage(db_path)

    records, metadata = collect_carla_observations(
        FakeWorld(),
        FakeVehicle(),
        timestamp_utc_s=1790000000.0,
    )

    for record in records:
        save_acquisition_sample(
            record,
            db_path,
            simulation_time_s=metadata["simulation_time_s"],
            actor_id=metadata["actor_id"],
        )

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT timestamp_utc_s, simulation_time_s, actor_id,
                   value_json, unit, source, provenance, availability
            FROM acquisition_samples
            WHERE signal = ?
            """,
            ("vehicle.speed",),
        ).fetchone()

        count = conn.execute(
            "SELECT COUNT(*) FROM acquisition_samples"
        ).fetchone()[0]

    assert count == len(records)
    assert row is not None
    assert row[0] == 1790000000.0
    assert row[1] == 12.5
    assert row[2] == 42
    assert json.loads(row[3]) == 18.0
    assert row[4] == "km/h"
    assert row[5] == "carla"
    assert row[6] == "CALCULATED_FROM_CARLA"
    assert row[7] == "AVAILABLE"
