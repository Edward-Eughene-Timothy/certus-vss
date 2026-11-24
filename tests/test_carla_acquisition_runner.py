from types import SimpleNamespace as NS

import pytest

from certus_vss.ingestion import carla_acquisition_runner as runner


class FakeVehicle:
    def __init__(self, actor_id, type_id="vehicle.audi.a2"):
        self.id = actor_id
        self.type_id = type_id
        self.control_commands = []

    def get_transform(self):
        return NS(location=NS(x=1.0, y=2.0, z=0.0))

    def apply_control(self, *args, **kwargs):
        self.control_commands.append("apply_control")

    def set_transform(self, *args, **kwargs):
        self.control_commands.append("set_transform")


class FakeActors:
    def __init__(self, vehicles):
        self.vehicles = vehicles

    def filter(self, pattern):
        assert pattern == "vehicle.*"
        return self.vehicles


class FakeWorld:
    def __init__(self, vehicles):
        self.vehicles = vehicles

    def get_actors(self):
        return FakeActors(self.vehicles)

    def get_actor(self, actor_id):
        return next(
            (v for v in self.vehicles if v.id == actor_id),
            None,
        )

    def get_snapshot(self):
        return NS(timestamp=NS(elapsed_seconds=1.0))


class FakeClient:
    def __init__(self, world):
        self.world = world

    def get_world(self):
        return self.world


def test_discovery_sorts_vehicles_by_actor_id():
    world = FakeWorld([
        FakeVehicle(30),
        FakeVehicle(10),
        FakeVehicle(20),
    ])

    vehicles = runner.discover_vehicles(world)

    assert [v.id for v in vehicles] == [10, 20, 30]


def test_discovery_returns_empty_list_when_no_vehicles():
    assert runner.discover_vehicles(FakeWorld([])) == []


def test_selection_fails_when_no_vehicles_exist():
    with pytest.raises(RuntimeError, match="No vehicles found"):
        runner.select_vehicle(FakeWorld([]))


def test_selection_uses_explicit_actor_id():
    first = FakeVehicle(10)
    second = FakeVehicle(20)

    selected = runner.select_vehicle(
        FakeWorld([first, second]),
        actor_id=20,
    )

    assert selected is second


def test_selection_rejects_unknown_actor_id():
    with pytest.raises(RuntimeError, match="was not found"):
        runner.select_vehicle(
            FakeWorld([FakeVehicle(10)]),
            actor_id=99,
        )

def test_multiple_vehicle_selection_uses_user_choice(monkeypatch):
    first = FakeVehicle(10)
    second = FakeVehicle(20)

    monkeypatch.setattr("builtins.input", lambda _: "2")

    selected = runner.select_vehicle(
        FakeWorld([first, second])
    )

    assert selected is second

def test_selection_stops_when_input_is_unavailable(monkeypatch):
    first = FakeVehicle(10)
    second = FakeVehicle(20)

    def unavailable_input(_):
        raise EOFError

    monkeypatch.setattr("builtins.input", unavailable_input)

    with pytest.raises(RuntimeError, match="selection cancelled"):
        runner.select_vehicle(FakeWorld([first, second]))

def test_runner_stops_when_vehicle_disappears(caplog):
    vehicle = FakeVehicle(42)
    world = FakeWorld([])

    runner.run_acquisition(
        FakeClient(world),
        vehicle,
        duration_s=1.0,
    )

    assert "no longer available" in caplog.text


def test_runner_persists_collected_records(monkeypatch):
    vehicle = FakeVehicle(42)
    world = FakeWorld([vehicle])
    calls = []

    fake_time = [0.0]

    def fake_monotonic():
        return fake_time[0]

    def fake_sleep(seconds):
        fake_time[0] += seconds

    monkeypatch.setattr(
        runner.time,
        "monotonic",
        fake_monotonic,
    )
    monkeypatch.setattr(
        runner.time,
        "sleep",
        fake_sleep,
    )

    monkeypatch.setattr(
        runner,
        "collect_carla_observations",
        lambda *args, **kwargs: (
            ["record-1", "record-2"],
            {
                "simulation_time_s": 1.0,
                "actor_id": 42,
            },
        ),
    )

    def fake_save(records, **kwargs):
        calls.append((records, kwargs))
        return [1, 2]

    monkeypatch.setattr(
        runner,
        "save_acquisition_samples",
        fake_save,
    )

    result = runner.run_acquisition(
        FakeClient(world),
        vehicle,
        sample_hz=5.0,
        duration_s=0.35,
    )

    assert result is True

    assert len(calls) == 2
    assert all(call[1]["actor_id"] == 42 for call in calls)
    assert vehicle.control_commands == []


def test_runner_stops_and_logs_database_failure(monkeypatch, caplog):
    vehicle = FakeVehicle(42)
    world = FakeWorld([vehicle])

    clock_values = iter([0.0, 0.1, 0.2])

    monkeypatch.setattr(
        runner.time,
        "monotonic",
        lambda: next(clock_values),
    )
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)

    monkeypatch.setattr(
        runner,
        "collect_carla_observations",
        lambda *args, **kwargs: (
            ["record"],
            {"simulation_time_s": 1.0, "actor_id": 42},
        ),
    )

    def fail_save(*args, **kwargs):
        raise OSError("simulated database failure")

    monkeypatch.setattr(
        runner,
        "save_acquisition_samples",
        fail_save,
    )

    result = runner.run_acquisition(
        FakeClient(world),
        vehicle,
        duration_s=1.0,
    )

    assert result is False

    assert "Acquisition cycle failed" in caplog.text
    assert vehicle.control_commands == []

def test_runner_returns_failure_when_world_connection_fails(monkeypatch):
    vehicle = FakeVehicle(42)

    class FailingClient:
        def get_world(self):
            raise OSError("simulated CARLA connection failure")

    monkeypatch.setattr(runner.time, "monotonic", lambda: 0.0)

    result = runner.run_acquisition(
        FailingClient(),
        vehicle,
    )

    assert result is False


def test_runner_returns_failure_when_snapshot_retrieval_fails(monkeypatch):
    vehicle = FakeVehicle(42)
    world = FakeWorld([vehicle])

    def fail_snapshot():
        raise RuntimeError("simulated snapshot failure")

    monkeypatch.setattr(world, "get_snapshot", fail_snapshot)
    monkeypatch.setattr(runner.time, "monotonic", lambda: 0.0)

    result = runner.run_acquisition(
        FakeClient(world),
        vehicle,
    )

    assert result is False