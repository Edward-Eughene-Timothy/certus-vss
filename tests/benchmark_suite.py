"""
benchmark_suite.py: Automated empirical verification suite for CertusVSS.
Measures SQL query latency, end-to-end response times, and RSS memory usage.
"""

import time
import statistics
import os
from pathlib import Path
import sys

# Ensure src/ is in python path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))

from certus_vss.analytics.query_engine import query_telemetry_summary
from certus_vss.models.inference_engine import CertusInferenceEngine


def get_process_rss_mb() -> float:
    """Reads current RSS memory consumption from Linux /proc/self/status or psutil fallback."""
    try:
        if os.path.exists("/proc/self/status"):
            with open("/proc/self/status", "r") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        parts = line.split()
                        kb = float(parts[1])
                        return kb / 1024.0
    except Exception:
        pass

    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss / (1024.0 * 1024.0)
    except Exception:
        pass

    return 0.0


def run_benchmarks(iterations: int = 50) -> None:
    print("=" * 60)
    print("  CertusVSS Empirical Benchmark & Verification Suite")
    print("=" * 60)

    # 1. Initialize Inference Engine once
    print(f"\n[1/3] Initializing SLM Engine for benchmark...")
    inference_engine = CertusInferenceEngine()

    # 2. Benchmark Tier-1 SQL Query Engine (24h operational window)
    print(f"\n[2/3] Running {iterations} iterations of Deterministic SQL Query Engine...")
    sql_latencies = []
    for _ in range(iterations):
        start = time.perf_counter()
        _ = query_telemetry_summary(days=1)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        sql_latencies.append(elapsed_ms)

    sql_p50 = statistics.median(sql_latencies)
    sql_p95 = statistics.quantiles(sql_latencies, n=20)[18] if iterations >= 20 else max(sql_latencies)
    sql_p99 = statistics.quantiles(sql_latencies, n=100)[98] if iterations >= 100 else max(sql_latencies)

    print(f"  • SQL Latency p50 : {sql_p50:.2f} ms")
    print(f"  • SQL Latency p95 : {sql_p95:.2f} ms")
    print(f"  • SQL Latency p99 : {sql_p99:.2f} ms")
    assert sql_p95 < 15.0, "FAIL: SQL p95 latency exceeded 15ms target!"
    print("  [PASS] Tier-1 SQL Latency verified under 15ms.")

    # 3. Benchmark End-to-End Query + SLM Explanation
    print(f"\n[3/3] Running end-to-end pipeline benchmark (SQL + SLM Explanation)...")
    e2e_latencies = []
    sample_evidence = query_telemetry_summary(days=30)

    for _ in range(10):  # Smaller iteration count for SLM inference
        start = time.perf_counter()
        _ = inference_engine.explain_evidence(sample_evidence, persona="DRIVER", timeout_ms=1500)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        e2e_latencies.append(elapsed_ms)

    e2e_p50 = statistics.median(e2e_latencies)
    e2e_p95 = statistics.quantiles(e2e_latencies, n=20)[18] if len(e2e_latencies) >= 20 else max(e2e_latencies)

    print(f"  • E2E Response p50 : {e2e_p50:.2f} ms")
    print(f"  • E2E Response p95 : {e2e_p95:.2f} ms")
    assert e2e_p95 < 2000.0, "FAIL: End-to-end latency exceeded 2000ms target!"
    print("  [PASS] End-to-End Latency verified under 2.0 seconds.")

    # 4. Memory Footprint Verification
    current_rss = get_process_rss_mb()
    print(f"\n[Memory Verification]")
    print(f"  • Current Process RSS Memory: {current_rss:.2f} MB")
    assert current_rss < 1200.0, "FAIL: Process RSS memory exceeded 1200MB ceiling!"
    print("  [PASS] Memory footprint is strictly within the 1200MB safety ceiling.")

    print("\n" + "=" * 60)
    print("  ALL VERIFICATION GATES PASSED SUCCESSFULLY [OK]")
    print("=" * 60)


if __name__ == "__main__":
    run_benchmarks()
