# Track A Engineering Documentation: Deterministic Storage, Analytics Core & SLM Interface

## Executive Summary
Track A provides the deterministic persistence, high-performance analytical engine, and edge SLM evidence-boxing pipeline for CertusVSS. It guarantees sub-15ms SQL query responses under strict kernel cgroup constraints (2 CPU cores, 2,048 MiB RAM limit) while preserving absolute mathematical determinism without calculation drift.

---

## 1. Physical Storage Architecture & Optimization

### Clustered Storage Scheme (`telemetry_rollup_1m`)
- **Table Topology:** Clustered B-tree organized `WITHOUT ROWID` on `(vss_path, bucket_epoch_m)`.
- **Primary Key Inversion:** By ordering `vss_path` first, SQLite physically clusters each signal's multi-day historical data into contiguous on-disk pages.
- **Index Elimination & Compaction:**
  - Removed redundant secondary indexes (`idx_rollup_vss_time` and `idx_rollup_context_time`).
  - Total database footprint reduced from **60.0 MB to 31.0 MB** across 259,200 rows (30 days of 1-minute historical data across 6 canonical signals).
  - Freelist count: **5 pages (compacted, zero file fragmentation)**.
- **Engine Concurrency:** Configured with `PRAGMA journal_mode = WAL`, `PRAGMA synchronous = NORMAL`, and `PRAGMA foreign_keys = ON`. Thread isolation enforced through dedicated read-only connections (`?mode=ro`).

---

## 2. Telemetry Tiers & Schema Contract

| Tier | Table | Schema / Resolution | Primary Key | Description |
|---|---|---|---|---|
| **Tier 1** | *In-Memory (Track B)* | 10 Hz raw ring buffer | RAM | 36,000 frames (60 min) in 65-byte fixed records. |
| **Tier 2** | `telemetry_rollup_1m` | 1-minute statistical rollups | `(vss_path, bucket_epoch_m)` | Clustered table storing `val_avg`, `val_min`, `val_max`, `val_delta`, `sample_count`, `unit`, `drive_context`, `provenance`. |
| **Tier 3** | `fault_freeze_frames` | 10 Hz fault waveform ($\pm 120\text{ s}$) | `(event_id, offset_ms, vss_path)` | 2,401 points per signal for fault diagnosis. |
| **Tier 4** | `vehicle_events` | Discrete diagnostic events | `event_id AUTOINCREMENT` | Diagnostic trouble codes (DTCs), severity, trigger signals, and contextual JSON telemetry. |

---

## 3. Mathematical Verification & Boundary Semantics

The deterministic analytics engine was formally validated against pure Python arithmetic models in `tests/test_deterministic_math.py` and `tests/test_math_edge_cases.py`:

1. **Deterministic Parity (3-Day Full Window / 4,321 Buckets):**
   - Evaluated Signal: `Vehicle.Powertrain.TractionBattery.Temperature`
   - Total Raw Samples Reconstructed: **2,592,600 samples**
   - Average: SQL `28.67` vs Python `28.67` (**0.00% drift**)
   - Minimum: SQL `25.75` vs Python `25.75`
   - Maximum: SQL `34.52` vs Python `34.52`
   - Spread ($\Delta$): SQL `8.77` vs Python `8.77`
2. **Average Semantics Specification:**
   - In CertusVSS Tier 2, `ROUND(AVG(val_avg), 2)` computes an **interval-uniform time average** where each bucket represents an equal 60-second time interval ($\Delta t = 60\text{ s}$).
   - Edge-case unit tests assert that transient CAN packet dropouts (e.g., 60 samples vs 600 samples) yield the exact interval-uniform mean (`25.00`), while also verifying the alternative sample-weighted mean (`37.27`).
3. **Temporal Inclusivity & Safety:**
   - Strict inclusive `BETWEEN` boundary conditions confirmed: $[t_0, t_0]$ returns exactly 1 minute bucket; $[t_0, t_0 + 2]$ returns exactly 3 buckets.
   - Out-of-range and empty windows return `status: "NO_DATA"` with valid query timing and zero exceptions.

---

## 4. Empirical Performance Benchmark (N = 500 Queries)

Conducted under hardware constraints via Linux cgroups (2 physical cores, 2,048 MiB cgroup limit). Evaluated 100 queries across each of the 5 diagnostic workloads with response validation:

================================================================================
EMPIRICAL LATENCY REPORT (CATEGORICAL BREAKDOWN, N=500)
Workload Category      | Count | Min (ms) | p50 (ms) | p95 (ms) | p99 (ms)
Rollup_7Day            | 100   | 1.162    | 1.512    | 1.922    | 2.048
Rollup_30Day           | 100   | 3.543    | 4.635    | 6.293    | 6.625

Rollup_24Hour          | 100   | 0.317    | 0.406    | 0.516    | 0.586

Diagnostic_Events      | 100   | 0.165    | 0.193    | 0.250    | 0.288

Freeze_Frames          | 100   | 1.279    | 1.431    | 2.043    | 2.495

OVERALL (AGGREGATE)    | 500   | 0.165    | 1.393    | 5.179    | 6.293
================================================================================
MEMORY AUDIT (PROCESS RSS vs. DOCKER CGROUP)
Process Resident Memory (VmRSS) :    15.42 MiB
Process Peak Resident Memory    :    44.48 MiB (Target Budget: < 1200.00 MiB)
Container Cgroup Current Memory :    57.09 MiB
Container Cgroup Peak Recorded  :   104.79 MiB
Container Cgroup Hardware Limit :  2048.00 MiB
VERDICT: PASS (All categories validated; p95 < 15.0 ms and cgroup memory limits strictly enforced)


---

## 5. Integration Contract for Track B (Aryan)

To ensure Track B ingestion flushes seamlessly into Track A analytics, the following interfaces are fixed:

### A. Canonical VSS Signals (6 Signals)
Per `configs/vss_signals.json`, the authoritative 6-signal schema is:
1. `Vehicle.Speed` (km/h)
2. `Vehicle.Powertrain.TractionBattery.StateOfCharge` (%)
3. `Vehicle.Powertrain.TractionBattery.Temperature` (°C)
4. `Vehicle.Powertrain.TractionBattery.Current` (A)
5. `Vehicle.Cabin.HVAC.PowerDraw` (W)
6. `Vehicle.Powertrain.ElectricMotor.CoolantTemperature` (°C)

### B. Tier 2 Rollup Flush Contract (`storage/rollup_worker.py`)
- Target Table: `telemetry_rollup_1m`
- Primary Key: `(vss_path, bucket_epoch_m)`
- Insert Mode: `INSERT OR IGNORE` or `INSERT OR REPLACE`
- `bucket_epoch_m`: Unix epoch minute (`timestamp_s // 60`).
- `val_delta`: Defined as last sample minus first sample in that minute bucket.
- `drive_context`: Must be classified into `URBAN_STOP_GO`, `HIGHWAY_CRUISE`, `IDLE`, or `CHARGING`. (Do not default to `"unknown"` in production).

### C. Tier 3 Freeze-Frame Contract (`storage/event_capture.py`)
- Target Table: `fault_freeze_frames`
- Schema: `(event_id, offset_ms, vss_path, raw_value)`
- Offset Window: $\pm 120,000\text{ ms}$ relative to fault trigger ($offset\_ms \in [-120000, +120000]$).
- Expected Samples: Exactly **2,401 points** per signal at 10 Hz.

---

## 6. Phase 2: Edge SLM Interface Scaffold

Track A includes the inference infrastructure ready for local GGUF execution:
- **Model:** Qwen 2.5 0.5B Instruct (`qwen2.5-0.5b-instruct-q4_k_m.gguf`, ~398 MB).
- **Inference Runtime:** `llama-cpp-python` (CPU-bound, 2 threads).
- **Grammar Enforcement:** `response.gbnf` guarantees strict JSON output without markdown wrappers:
  ```json
  {
    "summary": "Concise automotive engineering assessment",
    "key_facts": ["Exact trigger metrics", "Waveform behavior", "Severity rating"],
    "confidence": "VERIFIED"
  }
Monotonic Latency Watchdog: time.monotonic() enforces a hard deadline cutoff at 1,850 ms, preserving the sub-2.0s end-to-end response budget.

7. Verification Commands
Run the full Track A acceptance suite inside Docker:

Bash
# 1. Deterministic math parity and edge cases
python3 tests/test_math_edge_cases.py
python3 tests/test_deterministic_math.py

# 2. 500-query latency and memory cgroup benchmark
python3 tests/benchmark_sql_suite.py
