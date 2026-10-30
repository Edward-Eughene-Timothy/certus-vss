import json
import sqlite3
import time

from certus_vss.ingestion.ring_buffer import SIGNAL_PATHS, TelemetryRingBuffer
from certus_vss.storage.event_capture import capture_event_window, record_safety_event


def _frame(timestamp, sequence, speed, battery_temp=None):
    signals = {path: None for path in SIGNAL_PATHS}
    signals["Vehicle.Speed"] = speed
    signals["Vehicle.Powertrain.TractionBattery.Temperature"] = battery_temp
    return {"timestamp": timestamp, "sequence": sequence, "signals": signals}


def test_record_event_saves_available_pre_and_post_samples(tmp_path):
    db = tmp_path / "events.sqlite"
    buffer = TelemetryRingBuffer(maxlen=20, retention_seconds=3600)
    for seq, ts in enumerate((880, 940, 1000, 1060, 1120)):
        assert buffer.append(_frame(ts, seq, 20 + seq, 30 + seq))

    event_id = record_safety_event(buffer, {
        "timestamp": 1000.0,
        "event_type": "collision",
        "severity": "warning",
        "intensity": 12.5,
        "source": "carla",
        "other_actor_id": 42,
    }, db)

    with sqlite3.connect(db) as conn:
        event = conn.execute(
            "SELECT timestamp_utc_s, event_category, severity, trigger_signal, trigger_value, context_json "
            "FROM vehicle_events WHERE event_id=?", (event_id,)
        ).fetchone()
        rows = conn.execute(
            "SELECT offset_ms, vss_path, raw_value FROM fault_freeze_frames "
            "WHERE event_id=? ORDER BY offset_ms, vss_path", (event_id,)
        ).fetchall()
    assert event[:5] == (1000, "collision", "warning", "Vehicle.Collision", 12.5)
    context = json.loads(event[5])
    assert context["source"] == "carla"
    assert context["other_actor_id"] == 42
    assert len(rows) == 10  # two non-null signals across five frames
    assert {row[0] for row in rows} == {-120000, -60000, 0, 60000, 120000}


def test_capture_post_event_samples_later_and_is_idempotent(tmp_path):
    db = tmp_path / "events.sqlite"
    buffer = TelemetryRingBuffer(maxlen=20, retention_seconds=3600)
    assert buffer.append(_frame(1000, 0, 10))
    event_id = record_safety_event(buffer, {"timestamp": 1000, "event_type": "collision"}, db,
                                   pre_seconds=120, post_seconds=120)
    assert capture_event_window(event_id, buffer, 1000, db, pre_seconds=120, post_seconds=120) == 0

    assert buffer.append(_frame(1060, 1, 20))
    assert buffer.append(_frame(1120, 2, 30))
    assert capture_event_window(event_id, buffer, 1000, db, pre_seconds=120, post_seconds=120) == 2
    assert capture_event_window(event_id, buffer, 1000, db, pre_seconds=120, post_seconds=120) == 0
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT offset_ms, raw_value FROM fault_freeze_frames WHERE event_id=? AND vss_path='Vehicle.Speed' ORDER BY offset_ms",
            (event_id,),
        ).fetchall()
    assert rows == [(0, 10.0), (60000, 20.0), (120000, 30.0)]


def test_event_rejects_invalid_timestamp(tmp_path):
    buffer = TelemetryRingBuffer(maxlen=2)
    try:
        record_safety_event(buffer, {"timestamp": float("nan")}, tmp_path / "events.sqlite")
    except ValueError as exc:
        assert "timestamp" in str(exc)
    else:
        raise AssertionError("non-finite event timestamp should be rejected")

def test_scheduler_captures_later_telemetry_automatically(tmp_path):
    db = tmp_path / "scheduled_events.sqlite"
    buffer = TelemetryRingBuffer(maxlen=20, retention_seconds=3600)

    assert buffer.append(_frame(1000, 0, 10))

    event_id = record_safety_event(
        buffer,
        {"timestamp": 1000, "event_type": "collision"},
        db,
        pre_seconds=120,
        post_seconds=0.2,
    )

    time.sleep(0.05)
    assert buffer.append(_frame(1000.1, 1, 20))

    deadline = time.monotonic() + 3
    row = None

    while time.monotonic() < deadline:
        with sqlite3.connect(db) as conn:
            row = conn.execute(
                """SELECT raw_value FROM fault_freeze_frames
                   WHERE event_id=? AND vss_path='Vehicle.Speed'
                   AND offset_ms=100""",
                (event_id,),
            ).fetchone()

        if row is not None:
            break

        time.sleep(0.05)

    assert row == (20.0,)

def test_carla_collision_callback_persists_safety_event(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from certus_vss.ingestion import carla_adapter
    from certus_vss.storage.event_capture import record_safety_event

    db = tmp_path / "carla_collision.sqlite"
    buffer = TelemetryRingBuffer(maxlen=20, retention_seconds=3600)
    assert buffer.append(_frame(1000.0, 0, 25.0, 32.0))

    # Fake CARLA blueprint, world, and sensor so no CARLA server is needed.
    class FakeSensor:
        def listen(self, callback):
            self.callback = callback

    sensor = FakeSensor()
    world = SimpleNamespace(
        get_blueprint_library=lambda: SimpleNamespace(
            find=lambda name: name
        ),
        spawn_actor=lambda *args, **kwargs: sensor,
    )

    monkeypatch.setattr(
        carla_adapter,
        "_identity_transform",
        lambda world: object(),
    )

    captured_event_ids = []

    def handle_collision(event):
        event_id = record_safety_event(buffer, event, db)
        captured_event_ids.append(event_id)

    monkeypatch.setattr(carla_adapter.time, "time", lambda: 1000.0)

    carla_adapter.attach_collision_sensor(
        world,
        object(),
        handle_collision,
    )

    collision = SimpleNamespace(
        normal_impulse=SimpleNamespace(x=3.0, y=4.0, z=0.0),
        other_actor=SimpleNamespace(id=42),
    )
    sensor.callback(collision)

    assert len(captured_event_ids) == 1

    with sqlite3.connect(db) as conn:
        event = conn.execute(
            """SELECT event_category, trigger_value, context_json
               FROM vehicle_events WHERE event_id=?""",
            (captured_event_ids[0],),
        ).fetchone()
        freeze_frames = conn.execute(
            """SELECT COUNT(*) FROM fault_freeze_frames
               WHERE event_id=?""",
            (captured_event_ids[0],),
        ).fetchone()[0]

    assert event[0] == "collision"
    assert event[1] == 5.0
    assert json.loads(event[2])["other_actor_id"] == 42
    assert freeze_frames == 2
