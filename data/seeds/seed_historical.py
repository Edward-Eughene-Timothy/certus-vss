"""
Historical 30-Day Telemetry Rollup & Event Seeder for CertusVSS.
Populates data/certus_telemetry.db with 30 days of 1-minute rollup records
(43,200 minutes x 6 signals = 259,200 rows) plus realistic vehicle events.
"""

import math
import random
import time
from pathlib import Path

from certus_vss.common.config import DB_PATH
from certus_vss.storage.db_engine import init_database, get_writer_connection

SIGNALS = [
    {
        "path": "Vehicle.Speed",
        "unit": "km/h",
        "base_min": 0.0,
        "base_max": 120.0,
        "noise": 5.0,
    },
    {
        "path": "Vehicle.Powertrain.TractionBattery.Temperature",
        "unit": "celsius",
        "base_min": 25.0,
        "base_max": 45.0,
        "noise": 0.8,
    },
    {
        "path": "Vehicle.Powertrain.TractionBattery.Current",
        "unit": "amperes",
        "base_min": -150.0,
        "base_max": 200.0,
        "noise": 10.0,
    },
    {
        "path": "Vehicle.Powertrain.TractionBattery.StateOfCharge",
        "unit": "percent",
        "base_min": 10.0,
        "base_max": 100.0,
        "noise": 0.2,
    },
    {
        "path": "Vehicle.Cabin.HVAC.PowerDraw",
        "unit": "watts",
        "base_min": 200.0,
        "base_max": 2500.0,
        "noise": 50.0,
    },
    {
        "path": "Vehicle.Powertrain.ElectricMotor.CoolantTemperature",
        "unit": "celsius",
        "base_min": 30.0,
        "base_max": 65.0,
        "noise": 1.2,
    },
]

DRIVE_CONTEXTS = [
    "URBAN_STOP_GO",
    "HIGHWAY_CRUISE",
    "IDLE",
    "CHARGING",
]


def seed_data(days: int = 30) -> None:
    print(f"[CertusVSS Seeder] Initializing database at: {DB_PATH}")
    init_database()

    total_minutes = days * 24 * 60  # 43,200 minutes for 30 days
    current_time_s = int(time.time())
    start_epoch_m = (current_time_s // 60) - total_minutes

    print(
        f"[CertusVSS Seeder] Generating {total_minutes} minutes x {len(SIGNALS)} signals "
        f"= {total_minutes * len(SIGNALS):,} rollup records..."
    )

    rollup_rows = []
    events_rows = []
    batch_size = 20_000

    # Simulating diurnal commute cycles
    with get_writer_connection() as conn:
        cursor = conn.cursor()

        # Clean existing rollups to allow idempotent reseeding
        cursor.execute("DELETE FROM telemetry_rollup_1m")
        cursor.execute("DELETE FROM vehicle_events")
        cursor.execute("DELETE FROM fault_freeze_frames")

        for m in range(total_minutes):
            epoch_m = start_epoch_m + m
            minute_of_day = (epoch_m % 1440)
            hour_of_day = minute_of_day / 60.0

            # Commute hours vs night idle/charging
            if 7.0 <= hour_of_day <= 9.0 or 17.0 <= hour_of_day <= 19.0:
                context = "URBAN_STOP_GO" if random.random() < 0.6 else "HIGHWAY_CRUISE"
                driving = True
            elif 10.0 <= hour_of_day <= 16.0:
                context = "HIGHWAY_CRUISE" if random.random() < 0.4 else "IDLE"
                driving = context != "IDLE"
            elif 23.0 <= hour_of_day or hour_of_day <= 5.0:
                context = "CHARGING" if random.random() < 0.8 else "IDLE"
                driving = False
            else:
                context = "IDLE"
                driving = False

            # Synthetic variations for signals
            cycle_phase = (m % 1440) / 1440.0 * 2 * math.pi
            temp_diurnal = math.sin(cycle_phase) * 5.0  # warmer in afternoon

            # SoC depletion and charging cycles
            soc_val = max(15.0, min(98.0, 50.0 + 35.0 * math.cos(cycle_phase)))

            for sig in SIGNALS:
                path = sig["path"]
                unit = sig["unit"]
                sample_count = 600  # 10Hz raw stream across 60 seconds

                if path == "Vehicle.Speed":
                    if driving and context == "HIGHWAY_CRUISE":
                        val_avg = round(random.uniform(70.0, 110.0), 2)
                        val_min = round(max(0.0, val_avg - random.uniform(5.0, 15.0)), 2)
                        val_max = round(val_avg + random.uniform(5.0, 15.0), 2)
                    elif driving and context == "URBAN_STOP_GO":
                        val_avg = round(random.uniform(15.0, 45.0), 2)
                        val_min = 0.0
                        val_max = round(val_avg + random.uniform(10.0, 30.0), 2)
                    else:
                        val_avg, val_min, val_max = 0.0, 0.0, 0.0
                        sample_count = 600

                elif path == "Vehicle.Powertrain.TractionBattery.Temperature":
                    base = 32.0 + temp_diurnal
                    if context == "CHARGING":
                        base += 4.0
                    val_avg = round(base + random.uniform(-0.4, 0.4), 2)
                    val_min = round(val_avg - random.uniform(0.1, 0.5), 2)
                    val_max = round(val_avg + random.uniform(0.1, 0.5), 2)

                elif path == "Vehicle.Powertrain.TractionBattery.Current":
                    if context == "CHARGING":
                        val_avg = round(random.uniform(-60.0, -25.0), 2)  # negative = charging
                        val_min = round(val_avg - 5.0, 2)
                        val_max = round(val_avg + 3.0, 2)
                    elif driving:
                        val_avg = round(random.uniform(20.0, 120.0), 2)
                        val_min = round(max(-20.0, val_avg - 30.0), 2)  # regen braking
                        val_max = round(val_avg + 40.0, 2)
                    else:
                        val_avg, val_min, val_max = 1.2, 0.8, 1.8

                elif path == "Vehicle.Powertrain.TractionBattery.StateOfCharge":
                    val_avg = round(soc_val, 2)
                    val_min = round(max(0.0, soc_val - 0.2), 2)
                    val_max = round(min(100.0, soc_val + 0.2), 2)

                elif path == "Vehicle.Cabin.HVAC.PowerDraw":
                    if driving or context == "CHARGING":
                        val_avg = round(random.uniform(600.0, 1800.0), 1)
                        val_min = round(val_avg - 150.0, 1)
                        val_max = round(val_avg + 200.0, 1)
                    else:
                        val_avg, val_min, val_max = 0.0, 0.0, 0.0

                elif path == "Vehicle.Powertrain.ElectricMotor.CoolantTemperature":
                    base = 42.0 + temp_diurnal
                    if driving and context == "HIGHWAY_CRUISE":
                        base += 12.0
                    val_avg = round(base + random.uniform(-0.8, 0.8), 2)
                    val_min = round(val_avg - 1.0, 2)
                    val_max = round(val_avg + 1.2, 2)
                else:
                    val_avg, val_min, val_max = 0.0, 0.0, 0.0

                val_delta = round(val_max - val_min, 2)

                rollup_rows.append((
                    epoch_m,
                    path,
                    val_avg,
                    val_min,
                    val_max,
                    val_delta,
                    sample_count,
                    unit,
                    context,
                    "SIMULATED",
                ))

            # Occasionally inject diagnostic trouble events
            if m % 7200 == 1200:  # ~every 5 days
                event_sec = epoch_m * 60 + 30
                events_rows.append((
                    event_sec,
                    "THERMAL_WARN",
                    "P0A7F",
                    "WARNING",
                    "Vehicle.Powertrain.TractionBattery.Temperature",
                    46.8,
                    '{"ambient_temp": 38.5, "speed_kmh": 104.2, "soc_pct": 42.0}',
                    "SIMULATED",
                ))

            if len(rollup_rows) >= batch_size:
                cursor.executemany(
                    """
                    INSERT INTO telemetry_rollup_1m (
                        bucket_epoch_m, vss_path, val_avg, val_min, val_max,
                        val_delta, sample_count, unit, drive_context, provenance
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rollup_rows,
                )
                rollup_rows.clear()
                print(f"[CertusVSS Seeder] Committed batch at minute {m}/{total_minutes}...")

        if rollup_rows:
            cursor.executemany(
                """
                INSERT INTO telemetry_rollup_1m (
                    bucket_epoch_m, vss_path, val_avg, val_min, val_max,
                    val_delta, sample_count, unit, drive_context, provenance
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rollup_rows,
            )
            rollup_rows.clear()

        if events_rows:
            cursor.executemany(
                """
                INSERT INTO vehicle_events (
                    timestamp_utc_s, event_category, dtc_code, severity,
                    trigger_signal, trigger_value, context_json, provenance
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                events_rows,
            )

        conn.commit()

    db_file = Path(DB_PATH)
    file_size_mb = db_file.stat().st_size / (1024 * 1024)
    print(f"\n[CertusVSS Seeder] SUCCESS!")
    print(f"-> Database path: {db_file.resolve()}")
    print(f"-> Total Database Size: {file_size_mb:.2f} MB (Constraint budget: < 30 MB)")
    print(f"-> Total 1-min Rollups Inserted: {total_minutes * len(SIGNALS):,}")
    print(f"-> Total Diagnostic Events Inserted: {len(events_rows)}")


if __name__ == "__main__":
    seed_data(days=30)
