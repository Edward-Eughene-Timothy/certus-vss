"""
benchmark_sql_suite.py: Production acceptance benchmark for Tier 1.
Executes 500 validated queries across 5 distinct diagnostic workloads (100 each).
Enforces strict assertions on p95 latency, process RSS ceiling, and cgroup bounds.
"""

from pathlib import Path
import json
import math
import resource
import time
from typing import Any, Dict, List, Optional
from certus_vss.storage.db_engine import get_readonly_connection
from certus_vss.analytics.query_engine import DeterministicQueryEngine

CGROUP_DIR = Path("/sys/fs/cgroup")

SIGNALS = [
    "Vehicle.Speed",
    "Vehicle.Powertrain.TractionBattery.StateOfCharge",
    "Vehicle.Powertrain.TractionBattery.Temperature",
    "Vehicle.Powertrain.TractionBattery.Current",
    "Vehicle.Powertrain.TractionBattery.Voltage",
    "Vehicle.Cabin.HVAC.PowerDraw",
]

CONTEXTS = ["URBAN_STOP_GO", "HIGHWAY_CRUISE", "IDLE", "CHARGING", None]


def read_cgroup_val(filename: str) -> Optional[float]:
    path = CGROUP_DIR / filename
    try:
        val = path.read_text().strip()
        if val in ("max", ""):
            return None
        return int(val) / (1024 * 1024)
    except (OSError, ValueError):
        return None


def get_process_rss_mb() -> Dict[str, float]:
    """Reads process-level RSS and High Water Mark from /proc/self/status."""
    status_path = Path("/proc/self/status")
    rss_mb = 0.0
    hwm_mb = 0.0
    if status_path.exists():
        for line in status_path.read_text().splitlines():
            if line.startswith("VmRSS:"):
                rss_mb = int(line.split()[1]) / 1024.0
            elif line.startswith("VmHWM:"):
                hwm_mb = int(line.split()[1]) / 1024.0
    else:
        hwm_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
        rss_mb = hwm_mb
    return {"current_rss_mb": round(rss_mb, 2), "peak_hwm_mb": round(hwm_mb, 2)}


def calculate_percentile(data: List[float], percentile: float) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = (len(sorted_data) - 1) * percentile
    lower = math.floor(idx)
    upper = math.ceil(idx)
    if lower == upper:
        return round(sorted_data[int(idx)], 3)
    d0 = sorted_data[lower] * (upper - idx)
    d1 = sorted_data[upper] * (idx - lower)
    return round(d0 + d1, 3)


def get_latest_telemetry_epoch_s() -> int:
    with get_readonly_connection() as conn:
        row = conn.execute("SELECT MAX(bucket_epoch_m) FROM telemetry_rollup_1m;").fetchone()
        if row and row[0] is not None:
            return int(row[0]) * 60
    return int(time.time())


def get_deterministic_freeze_frame_target() -> Dict[str, Any]:
    target_signal = "Vehicle.Powertrain.TractionBattery.Temperature"
    with get_readonly_connection() as conn:
        row = conn.execute("""
            SELECT e.event_id, f.vss_path, COUNT(f.offset_ms) AS point_count
            FROM vehicle_events AS e
            JOIN fault_freeze_frames AS f ON f.event_id = e.event_id
            WHERE e.event_category = 'THERMAL_WARN'
              AND f.vss_path = ?
            GROUP BY e.event_id, f.vss_path
            ORDER BY e.timestamp_utc_s DESC, e.event_id DESC
            LIMIT 1;
        """, (target_signal,)).fetchone()

    if not row:
        raise RuntimeError(f"Validation Failure: No THERMAL_WARN event with signal {target_signal} found.")

    res = dict(row)
    assert res["point_count"] == 2401, (
        f"Validation Failure: Expected exactly 2,401 freeze-frame points for ±120s @ 10Hz, got {res['point_count']}"
    )
    return res


def validate_rollup(result: Dict[str, Any], label: str) -> None:
    assert result.get("status") == "SUCCESS", f"{label}: Query status failed ({result})"
    assert result.get("window_minutes", 0) > 0, f"{label}: Zero minute buckets returned"
    aggregates = result.get("aggregates") or {}
    assert aggregates.get("average") is not None, f"{label}: Returned null average"
    assert aggregates.get("min") is not None, f"{label}: Returned null min"
    assert aggregates.get("max") is not None, f"{label}: Returned null max"


def run_benchmark_suite() -> None:
    engine = DeterministicQueryEngine()
    telemetry_epoch_s = get_latest_telemetry_epoch_s()
    target_event = get_deterministic_freeze_frame_target()
    target_event_id = target_event["event_id"]
    target_vss_path = target_event["vss_path"]

    # Fix A: Exact inclusive minute bucket window math
    start_7d = telemetry_epoch_s - (((7 * 1440) - 1) * 60)
    start_30d = telemetry_epoch_s - (((30 * 1440) - 1) * 60)
    start_24h = telemetry_epoch_s - (((24 * 60) - 1) * 60)

    categories = {
        "Rollup_7Day": [],
        "Rollup_30Day": [],
        "Rollup_24Hour": [],
        "Diagnostic_Events": [],
        "Freeze_Frames": [],
    }

    print(f"[Benchmark] Telemetry Anchor Epoch : {telemetry_epoch_s} ({telemetry_epoch_s // 60}m)")
    print(f"[Benchmark] Verified Target Target : Event {target_event_id} ({target_vss_path}) [2,401 points]")
    print("[Benchmark] Executing 500 validated queries across 5 categories (100 each)...")

    for i in range(100):
        sig = SIGNALS[i % len(SIGNALS)]
        ctx = CONTEXTS[i % len(CONTEXTS)]

        # 1. 7-Day Rollup
        t0 = time.perf_counter()
        res_7d = engine.get_signal_metrics(
            vss_path=sig,
            start_epoch_s=start_7d,
            end_epoch_s=telemetry_epoch_s,
            context_filter=ctx,
        )
        elapsed = (time.perf_counter() - t0) * 1000.0
        validate_rollup(res_7d, f"7D-{sig}")
        categories["Rollup_7Day"].append(elapsed)

        # 2. 30-Day Rollup
        t0 = time.perf_counter()
        res_30d = engine.get_signal_metrics(
            vss_path=sig,
            start_epoch_s=start_30d,
            end_epoch_s=telemetry_epoch_s,
            context_filter=ctx,
        )
        elapsed = (time.perf_counter() - t0) * 1000.0
        validate_rollup(res_30d, f"30D-{sig}")
        categories["Rollup_30Day"].append(elapsed)

        # 3. 24-Hour Rollup
        t0 = time.perf_counter()
        res_24h = engine.get_signal_metrics(
            vss_path=sig,
            start_epoch_s=start_24h,
            end_epoch_s=telemetry_epoch_s,
            context_filter=ctx,
        )
        elapsed = (time.perf_counter() - t0) * 1000.0
        validate_rollup(res_24h, f"24H-{sig}")
        categories["Rollup_24Hour"].append(elapsed)

        # 4. Diagnostic Events Query
        t0 = time.perf_counter()
        res_ev = engine.get_recent_diagnostic_events(limit=(i % 3) + 1)
        elapsed = (time.perf_counter() - t0) * 1000.0
        assert res_ev["status"] == "SUCCESS", "Diagnostic events query failed"
        assert res_ev["event_count"] > 0, "No diagnostic events returned"
        categories["Diagnostic_Events"].append(elapsed)

        # 5. Freeze-Frame Waveform Extraction
        t0 = time.perf_counter()
        res_ff = engine.get_freeze_frame_waveform(
            event_id=target_event_id,
            vss_path=target_vss_path,
        )
        elapsed = (time.perf_counter() - t0) * 1000.0
        assert res_ff["status"] == "SUCCESS", "Freeze-frame query failed"
        assert res_ff["sample_count"] == 2401, f"Expected 2401 samples, got {res_ff['sample_count']}"
        categories["Freeze_Frames"].append(elapsed)

    proc_mem = get_process_rss_mb()
    cg_current = read_cgroup_val("memory.current")
    cg_peak = read_cgroup_val("memory.peak")
    cg_limit = read_cgroup_val("memory.max")

    print("\n" + "=" * 80)
    print("EMPIRICAL LATENCY REPORT (CATEGORICAL BREAKDOWN, N=500)")
    print("=" * 80)
    print(f"{'Workload Category':<22} | {'Count':<5} | {'Min (ms)':<8} | {'p50 (ms)':<8} | {'p95 (ms)':<8} | {'p99 (ms)':<8}")
    print("-" * 80)

    all_latencies: List[float] = []
    for cat_name, times in categories.items():
        all_latencies.extend(times)
        c_min = round(min(times), 3)
        c_p50 = calculate_percentile(times, 0.50)
        c_p95 = calculate_percentile(times, 0.95)
        c_p99 = calculate_percentile(times, 0.99)
        print(f"{cat_name:<22} | {len(times):<5} | {c_min:<8.3f} | {c_p50:<8.3f} | {c_p95:<8.3f} | {c_p99:<8.3f}")

    agg_p50 = calculate_percentile(all_latencies, 0.50)
    agg_p95 = calculate_percentile(all_latencies, 0.95)
    agg_p99 = calculate_percentile(all_latencies, 0.99)
    print("-" * 80)
    print(f"{'OVERALL (AGGREGATE)':<22} | {len(all_latencies):<5} | {min(all_latencies):<8.3f} | {agg_p50:<8.3f} | {agg_p95:<8.3f} | {agg_p99:<8.3f}")
    print("=" * 80)

    def fmt_mb(val: Optional[float], default_label: str = "N/A") -> str:
        return f"{val:>8.2f} MiB" if val is not None else f"{default_label:>12}"

    print("\n" + "=" * 80)
    print("MEMORY AUDIT (PROCESS RSS vs. DOCKER CGROUP)")
    print("=" * 80)
    print(f"Process Resident Memory (VmRSS) : {proc_mem['current_rss_mb']:>8.2f} MiB")
    print(f"Process Peak Resident Memory    : {proc_mem['peak_hwm_mb']:>8.2f} MiB (Target Budget: < 1200.00 MiB)")
    print(f"Container Cgroup Current Memory : {fmt_mb(cg_current)}")
    print(f"Container Cgroup Peak Recorded  : {fmt_mb(cg_peak)}")
    print(f"Container Cgroup Hardware Limit : {fmt_mb(cg_limit, 'unlimited')}")
    print("=" * 80)

    # Performance Assertions
    for cat_name, times in categories.items():
        c_p95 = calculate_percentile(times, 0.95)
        assert c_p95 < 15.0, f"FAIL: {cat_name} p95 latency ({c_p95} ms) exceeds 15.0 ms target!"

    # Memory Assertions
    assert proc_mem["peak_hwm_mb"] <= 1200.0, (
        f"FAIL: Process peak RSS ({proc_mem['peak_hwm_mb']} MiB) exceeded target budget of 1200.00 MiB"
    )
    if cg_limit is None:
        raise RuntimeError("Validation Failure: No finite cgroup memory limit detected")
    if cg_peak is None:
        raise RuntimeError("Validation Failure: Unable to read cgroup peak memory")
    assert cg_peak <= cg_limit, (
        f"FAIL: Cgroup peak {cg_peak:.2f} MiB exceeded limit {cg_limit:.2f} MiB"
    )

    print("\nVERDICT: PASS (All categories validated; p95 < 15.0 ms and cgroup memory limits strictly enforced)")


if __name__ == "__main__":
    run_benchmark_suite()
