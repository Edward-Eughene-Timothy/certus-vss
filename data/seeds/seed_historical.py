"""
seed_historical.py: Synthesizes 30 days of high-fidelity automotive telemetry.
Populates:
  - telemetry_rollup_1m: 43,200 minutes * 6 signals = 259,200 rows (~24 MB)
  - vehicle_events: Diagnostic events (e.g., P0A7F Battery Degradation / Thermal Warning)
  - fault_freeze_frames: 10 Hz raw pre/post trigger window around events
"""

import json
import math
import random
import time
from pathlib import Path
from certus_vss.storage.db_engine import get_writer_connection, DB_PATH

# 30 Days in minutes: 30 * 24 * 60 = 43,200
TOTAL_MINUTES = 43_200
CHUNK_SIZE = 10_000  # Batch size to keep memory flat under 20 MB

SIGNALS = [
    {"path": "Vehicle.Speed", "unit": "km/h", "base": 45.0, "noise": 15.0},
    {"path": "Vehicle.Powertrain.TractionBattery.StateOfCharge", "unit": "percent", "base": 72.0, "noise": 5.0},
    {"path": "Vehicle.Powertrain.TractionBattery.Temperature", "unit": "celsius", "base": 31.0, "noise": 3.0},
    {"path": "Vehicle.Powertrain.TractionBattery.Current", "unit": "amperes", "base": 65.0, "noise": 25.0},
    {"path": "Vehicle.Powertrain.TractionBattery.Voltage", "unit": "volts", "base": 395.0, "noise": 4.0},
    {"path": "Vehicle.Cabin.HVAC.PowerDraw", "unit": "watts", "base": 1200.0, "noise": 300.0},
]

DRIVE_CONTEXTS = ["URBAN_STOP_GO", "HIGHWAY_CRUISE", "IDLE", "CHARGING"]


def generate_and_seed() -> None:
    start_time = time.time()
    now_epoch_s = int(start_time)
    start_epoch_m = (now_epoch_s // 60) - TOTAL_MINUTES

    print(f"[Seed] Generating 30 days ({TOTAL_MINUTES:,} minutes) across {len(SIGNALS)} signals...")
    print(f"[Seed] Total expected rollup rows: {TOTAL_MINUTES * len(SIGNALS):,}")

    with get_writer_connection() as conn:
        cursor = conn.cursor()

        # Clear previous data to ensure idempotent seeding
        cursor.execute("DELETE FROM fault_freeze_frames;")
        cursor.execute("DELETE FROM vehicle_events;")
        cursor.execute("DELETE FROM telemetry_rollup_1m;")
        cursor.execute("DELETE FROM sqlite_sequence WHERE name='vehicle_events';")
        conn.commit()

        # ---------------------------------------------------------------------
        # 1. Seed Tier 2: 1-Minute Rollups (Batched chunks)
        # ---------------------------------------------------------------------
        rollup_batch = []
        total_inserted = 0

        for minute_offset in range(TOTAL_MINUTES):
            bucket_m = start_epoch_m + minute_offset
            
            # Diurnal & drive-cycle context simulation
            hour_of_day = (minute_offset // 60) % 24
            if 0 <= hour_of_day < 6:
                context = "CHARGING" if (minute_offset % 3 == 0) else "IDLE"
            elif 7 <= hour_of_day <= 9 or 17 <= hour_of_day <= 19:
                context = "URBAN_STOP_GO"
            else:
                context = "HIGHWAY_CRUISE" if (minute_offset % 2 == 0) else "IDLE"

            for sig in SIGNALS:
                # Add sinusoidal time-of-day wave + pseudo-random noise
                wave = math.sin((minute_offset / 1440.0) * 2 * math.pi) * (sig["noise"] * 0.5)
                jitter = (random.random() - 0.5) * sig["noise"]
                
                # Context-specific adjustments
                if context == "IDLE":
                    avg_val = 0.0 if "Speed" in sig["path"] else sig["base"] * 0.85
                elif context == "CHARGING":
                    avg_val = -32.0 if "Current" in sig["path"] else sig["base"] * 0.9
                else:
                    avg_val = max(0.0, sig["base"] + wave + jitter)

                spread = abs(jitter) * 0.4
                val_min = round(max(0.0, avg_val - spread), 2)
                val_max = round(avg_val + spread, 2)
                val_avg = round(avg_val, 2)
                val_delta = round(val_max - val_min, 2)

                rollup_batch.append((
                    bucket_m,
                    sig["path"],
                    val_avg,
                    val_min,
                    val_max,
                    val_delta,
                    600,             # sample_count (10 Hz * 60s)
                    sig["unit"],
                    context,
                    "SIMULATED"
                ))

            if len(rollup_batch) >= CHUNK_SIZE:
                cursor.executemany(
                    """
                    INSERT INTO telemetry_rollup_1m 
                    (bucket_epoch_m, vss_path, val_avg, val_min, val_max, val_delta, sample_count, unit, drive_context, provenance)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    rollup_batch
                )
                conn.commit()
                total_inserted += len(rollup_batch)
                rollup_batch.clear()

        if rollup_batch:
            cursor.executemany(
                """
                INSERT INTO telemetry_rollup_1m 
                (bucket_epoch_m, vss_path, val_avg, val_min, val_max, val_delta, sample_count, unit, drive_context, provenance)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                rollup_batch
            )
            conn.commit()
            total_inserted += len(rollup_batch)
            rollup_batch.clear()

        print(f"[Seed] Successfully populated {total_inserted:,} rollup rows.")

        # ---------------------------------------------------------------------
        # 2. Seed Tier 4: Discrete Diagnostic Events (DTCs)
        # ---------------------------------------------------------------------
        # Event 1: Battery High-Temperature Alarm (5 days ago)
        event_1_time = now_epoch_s - (5 * 86400) - 1800
        cursor.execute(
            """
            INSERT INTO vehicle_events 
            (timestamp_utc_s, event_category, dtc_code, severity, trigger_signal, trigger_value, context_json, provenance)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                event_1_time,
                "THERMAL_WARN",
                "P0A7F",
                "WARNING",
                "Vehicle.Powertrain.TractionBattery.Temperature",
                42.8,
                json.dumps({
                    "ambient_temp_c": 36.2,
                    "speed_kmh": 112.4,
                    "pack_current_a": 184.2,
                    "cooling_fan_status": "OFFLINE"
                }),
                "SIMULATED"
            )
        )
        event_1_id = cursor.lastrowid

        # Event 2: ADAS Disengagement due to camera occlusion (2 days ago)
        event_2_time = now_epoch_s - (2 * 86400) - 3400
        cursor.execute(
            """
            INSERT INTO vehicle_events 
            (timestamp_utc_s, event_category, dtc_code, severity, trigger_signal, trigger_value, context_json, provenance)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                event_2_time,
                "ADAS_DISENGAGE",
                "U0100",
                "INFO",
                "Vehicle.Speed",
                88.5,
                json.dumps({
                    "steering_angle_deg": -1.4,
                    "radar_snr_db": 18.2,
                    "camera_optical_flow_confidence": 0.12
                }),
                "SIMULATED"
            )
        )
        event_2_id = cursor.lastrowid
        conn.commit()
        print(f"[Seed] Populated diagnostic events: Event ID {event_1_id} (P0A7F), Event ID {event_2_id} (U0100).")

        # ---------------------------------------------------------------------
        # 3. Seed Tier 3: Fault Freeze-Frames (10 Hz pre/post trigger window)
        # ---------------------------------------------------------------------
        # ±30 second window around Event 1 @ 10 Hz (step of 100 ms) = 600 samples
        freeze_frames = []
        for offset in range(-120000, 120100, 100):
            # Sigmoidal thermal runaway ramp leading up to the fault
            ramp = 36.0 + (6.8 / (1.0 + math.exp(-offset / 15000.0)))
            freeze_frames.append((
                event_1_id,
                offset,
                "Vehicle.Powertrain.TractionBattery.Temperature",
                round(ramp + (random.random() * 0.2), 2)
            ))
            freeze_frames.append((
                event_1_id,
                offset,
                "Vehicle.Powertrain.TractionBattery.Current",
                round(175.0 + (random.random() * 15.0), 1)
            ))

        cursor.executemany(
            """
            INSERT INTO fault_freeze_frames (event_id, offset_ms, vss_path, raw_value)
            VALUES (?, ?, ?, ?);
            """,
            freeze_frames
        )
        conn.commit()
        print(f"[Seed] Populated {len(freeze_frames):,} freeze-frame points for Event ID {event_1_id}.")

    elapsed = time.time() - start_time
    db_size_mb = Path(DB_PATH).stat().st_size / (1024 * 1024)
    print(f"[Seed] Completed in {elapsed:.2f}s | SQLite Database size: {db_size_mb:.2f} MB")


if __name__ == "__main__":
    generate_and_seed()