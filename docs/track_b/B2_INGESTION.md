# Track B — B2 Streaming Ingestion

## Status

**Initial implementation and mocked tests complete; live CARLA and DBC integration remain pending.** The synthetic stream can exercise the ring buffer continuously. The CAN adapter is an optional listen-only scaffold that requires a vehicle-specific decoder.

## Implemented

- `sim_stream.py`: `iter_simulated_frames()` generates timestamped, sequenced frames at a configurable sample rate; `start_simulation()` sends frames to a supplied ring buffer at real-time cadence and can stop after a sample count or via a stop event.
- `vss_normalizer.py`: maps the initial aliases to the six VSS paths in `configs/vss_signals.json`, validates finite numeric values, and converts vehicle speed from m/s to km/h.
- `can_adapter.py`: optional `python-can` listener for SocketCAN/vcan. It receives messages only and requires an injected vehicle-specific decoder; no transmit API is used.
- `carla_adapter.py`: samples a CARLA vehicle actor velocity, maps speed from m/s to km/h, emits the configured frame shape, and appends frames to the ring. An optional collision sensor callback emits event records for a future freeze-frame/event store.
- `tests/test_ingestion_stream.py`: checks simulator frame shape/timestamps, buffer integration, unit conversion, invalid input rejection, and CARLA adapter behavior using a fake actor.

## Normalized frame shape

```json
{
  "timestamp": 1800000000.0,
  "sequence": 42,
  "source": "synthetic_simulator",
  "signals": {
    "Vehicle.Speed": 48.5,
    "Vehicle.Powertrain.TractionBattery.Temperature": 31.2,
    "Vehicle.Powertrain.TractionBattery.Current": -20.0,
    "Vehicle.Powertrain.TractionBattery.StateOfCharge": 77.9,
    "Vehicle.Cabin.HVAC.PowerDraw": 720.0,
    "Vehicle.Powertrain.ElectricMotor.CoolantTemperature": 35.0
  }
}
```

The example values are synthetic. Timestamps are epoch seconds. The ring buffer currently stores the timestamp, sequence and six numeric values in fixed-width records; source metadata is not retained in the fixed record.

## Test command and result

```bash
PYTHONPATH=src pytest -c /dev/null -q tests/test_ring_buffer.py tests/test_ingestion_stream.py
```

Latest result: **19 passed** (11 ring-buffer tests plus 8 ingestion tests).

## Limitations / next work

- The CARLA adapter is implemented but has only been tested with a fake actor. It has not been run against a live CARLA server. CARLA does not natively provide all battery/BMS/HVAC signals; those remain null unless an extra-signal provider is supplied. The collision sensor requires the CARLA Python API and a live world and has not been exercised here.
- The CAN listener needs a real DBC/decoder and a virtual-CAN integration test. `python-can` receive-only usage is not proof of a physical data diode or ASIL compliance.
- The normalizer currently supports only the six aliases and paths in the repository's current signal catalog.
- `start_simulation()` is a development source; it is not a real vehicle model.
- The full repository test suite is still unverified because the existing project configuration has a TOML parsing issue.
