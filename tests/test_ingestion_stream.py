from certus_vss.ingestion.ring_buffer import SIGNAL_PATHS, TelemetryRingBuffer
from certus_vss.ingestion.sim_stream import iter_simulated_frames, start_simulation
from certus_vss.ingestion.vss_normalizer import normalize_vss


def test_simulator_yields_changing_normalized_frames():
    frames = list(iter_simulated_frames(sample_hz=10, max_samples=3, start_epoch=1000))
    assert len(frames) == 3
    assert [f["sequence"] for f in frames] == [0, 1, 2]
    assert all(set(f["signals"]) == set(SIGNAL_PATHS) for f in frames)
    assert frames[0]["timestamp"] < frames[1]["timestamp"] < frames[2]["timestamp"]
    assert frames[0]["signals"]["Vehicle.Speed"] != frames[1]["signals"]["Vehicle.Speed"]


def test_start_simulation_pushes_frames_into_buffer():
    ring = TelemetryRingBuffer(maxlen=20, max_bytes=20 * 65)
    accepted = start_simulation(ring, sample_hz=1000, max_samples=10)
    assert accepted == 10
    assert len(ring) == 10


def test_vss_normalizer_maps_alias_and_converts_speed():
    result = normalize_vss("speed", 10, "m/s")
    assert result == {"path": "Vehicle.Speed", "value": 36.0, "unit": "km/h"}


def test_vss_normalizer_rejects_unknown_and_nonfinite_values():
    import pytest
    with pytest.raises(ValueError):
        normalize_vss("unknown_signal", 1)
    with pytest.raises(ValueError):
        normalize_vss("speed", float("nan"))


def test_carla_adapter_converts_velocity_and_keeps_unavailable_signals_null():
    from types import SimpleNamespace
    from certus_vss.ingestion.carla_adapter import make_carla_frame

    vehicle = SimpleNamespace(get_velocity=lambda: SimpleNamespace(x=3, y=4, z=0))
    frame = make_carla_frame(vehicle, timestamp=123.0, sequence=7)
    assert frame["source"] == "carla"
    assert frame["timestamp"] == 123.0
    assert frame["sequence"] == 7
    assert frame["signals"]["Vehicle.Speed"] == 18.0
    assert all(value is None for path, value in frame["signals"].items() if path != "Vehicle.Speed")


def test_carla_adapter_can_fill_project_specific_signals():
    from types import SimpleNamespace
    from certus_vss.ingestion.carla_adapter import make_carla_frame

    vehicle = SimpleNamespace(get_velocity=lambda: SimpleNamespace(x=0, y=0, z=0))
    frame = make_carla_frame(vehicle, extra_signals={"Vehicle.Powertrain.TractionBattery.StateOfCharge": 71.5})
    assert frame["signals"]["Vehicle.Powertrain.TractionBattery.StateOfCharge"] == 71.5


def test_carla_adapter_rejects_unknown_extra_signal():
    import pytest
    from types import SimpleNamespace
    from certus_vss.ingestion.carla_adapter import make_carla_frame

    vehicle = SimpleNamespace(get_velocity=lambda: SimpleNamespace(x=0, y=0, z=0))
    with pytest.raises(ValueError):
        make_carla_frame(vehicle, extra_signals={"Vehicle.Unknown": 1})


def test_carla_adapter_stream_integrates_with_ring_buffer():
    from types import SimpleNamespace
    from certus_vss.ingestion.carla_adapter import listen_carla

    vehicle = SimpleNamespace(get_velocity=lambda: SimpleNamespace(x=10, y=0, z=0))
    ring = TelemetryRingBuffer(maxlen=10, max_bytes=10 * 65)
    accepted = listen_carla(vehicle, ring, sample_hz=1000, max_samples=4)
    assert accepted == 4
    assert len(ring) == 4
    assert ring.snapshot()[-1]["signals"]["Vehicle.Speed"] == 36.0
