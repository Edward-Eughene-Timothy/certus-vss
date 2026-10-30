# CertusVSS: Phase 3 Unified Integration & Hardening Report

**Branch:** `integration/phase3-unified`

**Milestone Commit:** `00d88bb`

**Target Audience:** Aryan (Track B Lead), Edward (Track A Lead), Reviewers

**Test Status:** 43/43 Passing (5.33s execution in Docker sandbox)

---

## 1. Executive Summary & Core Integration Strategy

The primary objective of Phase 3 was unifying **Track A** (Deterministic Inference, Parity Gating, SQLite WAL Storage Engine) and **Track B** (65-byte Fixed Binary Ingestion, CAN/CARLA Streaming, 1-Minute Rollups, Event-Window Capture) into a single, cohesive, production-ready codebase without sacrificing the core invariants of either track.

```
+-----------------------------------------------------------------------------------------+
|                                    TRACK B (Ingestion)                                  |
|  CARLA Adapter / SocketCAN / Simulator ---> 65-Byte Fixed Ring Buffer (2.23 MiB bytearray)|
+---------------------------------------------+-------------------------------------------+
                                              |
                       +----------------------+----------------------+
                       |                                             |
                       v (Every 60s)                                 v (On Fault/Safety Event)
         +----------------------------+                +-------------------------------+
         | Tier 2: 1-Minute Rollups   |                | Tier 3 & Tier 4: Event Capture|
         | (val_avg, min, max, delta) |                | (vehicle_events + freeze_frame|
         +-------------+--------------+                +---------------+---------------+
                       |                                               |
                       +----------------------+------------------------+
                                              |
                                              v
+-----------------------------------------------------------------------------------------+
|                                    TRACK A (Inference)                                  |
|    EvidenceBuilder ---> Fail-Closed Validation ---> Qwen 2.5 0.5B (GBNF Grammars)       |
|                                                    |                                    |
|                                                    v                                    |
|                              Strict Numeric Parity Gate (<1,850 ms Watchdog)            |
+-----------------------------------------------------------------------------------------+

```

### Key Architectural Decisions

1. **Preserved Track A's Supervised SLM & Parity Engine:** Aryan's branch (`origin/feature/b4-scheduler`) contained an experimental text-generation summarizer. We preserved Track A's prefix-forced Qwen 2.5 0.5B runtime with GBNF grammar constraints, multiprocessing watchdog isolation (1,850 ms deadline), and regex-based numerical parity validation (`validator.py`).
2. **Adopted Track B's Ingestion & Storage Architecture:** Integrated Track B's memory-bounded `TelemetryRingBuffer` (allocating exactly 2.23 MiB for 36,000 frames in a flat `bytearray`), VSS normalizer, CARLA actor adapter, and the 1-minute `rollup_worker.py`.
3. **Eliminated Diagnostic Trouble Code (DTC) Fabrication:** Refactored event capture and evidence extraction so that events without an ECU-reported DTC remain `None` rather than fabricating real SAE codes like `P0A7F` or `C1001`.

---

## 2. Inventory of Changes by Subsystem

### A. Storage Engine (`src/certus_vss/storage/db_engine.py`)

* **Problem Addressed:** Track B's automated test suites pass transient database paths (e.g., `tmp_path / "events.sqlite"`), whereas Track A's initial implementation hardcoded `DB_PATH`. This caused 10 tests to fail with `TypeError: init_database() got an unexpected keyword argument 'db_path'`.
* **Modifications Made:**
* Updated `init_database(db_path: Path = DB_PATH, schema_path: Path = SCHEMA_PATH)` to accept parameterized database and schema paths, automatically creating parent directories.
* Updated `get_db_connection(db_path: Path = DB_PATH)` to yield managed, writable connections with automated commit on success and rollback on exceptions.
* Maintained `get_writer_connection()` and `get_readonly_connection(db_path: Path = DB_PATH)` with strict automotive SQLite PRAGMAs:
* `PRAGMA journal_mode = WAL;`
* `PRAGMA synchronous = NORMAL;`
* `PRAGMA cache_size = -16000;` (16 MB ceiling)
* `PRAGMA mmap_size = 33554432;` (32 MB memory-mapped I/O)
* `PRAGMA foreign_keys = ON;`


* Added compatibility aliases expected across Track B modules: `storage_dir`, `repo_root`, `data_dir`, `db_path`, `schema_path`.



### B. Event Capture (`src/certus_vss/storage/event_capture.py`)

* **Problem Addressed:** The initial bridge automatically substituted missing diagnostic codes with `P0A7F` (for battery categories) and `C1001` (for chassis/collision). As noted in code review, `P0A7F` strictly designates *Hybrid/EV Battery Pack Deterioration* in SAE J2012, and `C1001` is manufacturer-specific. Fabricating real DTCs for generic over-temperature or simulation spikes corrupts diagnostic integrity.
* **Modifications Made:**
* **Removed Code Fabrication:** `dtc_code` now respects ground truth. If the incoming event dictionary does not contain an explicit DTC, `dtc_code` is stored as `NULL` (`None` in Python).
* **Preserved Dynamic Triggers:** Preserved caller-supplied `trigger_signal` (e.g., `"Vehicle.Collision"`) while correctly recording pre- and post-fault freeze-frame offsets into `fault_freeze_frames`.
* **Context Payload Enrichment:** Propagated `simulated_fault_code` (e.g., `"SIM-THERMAL-001"`), `source`, `intensity`, and `other_actor_id` cleanly into `context_json`.
* **Asynchronous Scheduling:** Integrated with Track B's `PostEventCaptureScheduler` to schedule delayed freeze-frame finalization without blocking the real-time ingestion loop.



### C. Evidence Construction (`src/certus_vss/analytics/evidence_builder.py`)

* **Problem Addressed:** The validator previously enforced `dtc_code` as a required non-empty string in `req_str`, immediately raising `EvidenceMissingError` whenever a telemetry-only or simulated event occurred without an ECU DTC.
* **Modifications Made:**
* Extracted `dtc_code` from the strict required string check (`("event_category", "severity", "trigger_signal")`).
* If `dtc_code` is null in SQLite, `clean_dtc` is set to `None` and passed through in the evidence payload under `evidence["event"]["dtc_code"]`.
* Verified that freeze-frame waveform extraction (`get_freeze_frame_waveform`) and 24-hour historical baseline metrics (`get_signal_metrics`) continue to operate with strict floating-point validation.



### D. Inference Runtime & Parity Gate (`src/certus_vss/inference/llama_runtime.py`)

* **Problem Addressed:** `SLMDiagnosticRuntime.generate_diagnostic()` required a valid `dtc_code` string to build its prompt prefix, causing non-DTC events to crash.
* **Modifications Made:**
* Replaced the hard-assertion on `dtc_code` with a dynamic fault tag:
* If `dtc_code` is present: `tag = f"Fault {dtc}"` (e.g., `"Fault P0A7F at 47.5"`)
* If `dtc_code` is `None`: `tag = f"Event {category}"` (e.g., `"Event THERMAL_WARNING at 47.5"`)


* Prefixed the model generation prompt deterministically:
```python
prefix = f'{{"summary": "{tag} at {tv}", "key_facts": ["Trigger value '

```


* Verified compatibility with `validator.py`: `_NUMBER_PATTERN` ignores words and alphanumeric prefixes, solely validating that the numeric trigger value (`tv`) and waveform claims strictly match SQLite ground truth without hallucination.



---

## 3. Test Suites & Verification Coverage

The test suite now comprises **43 automated tests** executing in **5.33 seconds** within the Docker sandbox container.

```
root@97215784c540:/app# pytest -v
============================= test session starts ==============================
collected 43 items

tests/test_boundary_hardening.py::test_full_60s_rollup_window_aggregation PASSED [  2%]
tests/test_boundary_hardening.py::test_ring_buffer_wraparound_eviction_integrity PASSED [  4%]
tests/test_boundary_hardening.py::test_null_dtc_diagnostic_purity PASSED        [  6%]
tests/test_db_pragmas.py::test_pragmas PASSED                                   [  9%]
tests/test_deterministic_math.py::test_deterministic_computations PASSED         [ 11%]
tests/test_event_capture.py::test_record_event_saves_available_pre_and_post_samples PASSED [ 13%]
tests/test_event_capture.py::test_capture_post_event_samples_later_and_is_idempotent PASSED [ 16%]
tests/test_event_capture.py::test_event_rejects_invalid_timestamp PASSED        [ 18%]
tests/test_event_capture.py::test_scheduler_captures_later_telemetry_automatically PASSED [ 20%]
tests/test_ingestion_stream.py::test_simulator_yields_changing_normalized_frames PASSED [ 23%]
tests/test_ingestion_stream.py::test_start_simulation_pushes_frames_into_buffer PASSED [ 25%]
tests/test_ingestion_stream.py::test_vss_normalizer_maps_alias_and_converts_speed PASSED [ 27%]
tests/test_ingestion_stream.py::test_vss_normalizer_rejects_unknown_and_nonfinite_values PASSED [ 30%]
tests/test_ingestion_stream.py::test_carla_adapter_converts_velocity_and_keeps_unavailable_signals_null PASSED [ 32%]
tests/test_ingestion_stream.py::test_carla_adapter_can_fill_project_specific_signals PASSED [ 34%]
tests/test_ingestion_stream.py::test_carla_adapter_rejects_unknown_extra_signal PASSED [ 37%]
tests/test_ingestion_stream.py::test_carla_adapter_stream_integrates_with_ring_buffer PASSED [ 39%]
tests/test_integration_e2e.py::test_end_to_end_track_a_track_b_integration PASSED [ 41%]
tests/test_intent_parsing.py::test_query_type_routing_and_rejection PASSED     [ 44%]
tests/test_math_edge_cases.py::test_empty_window_handling PASSED                 [ 46%]
tests/test_math_edge_cases.py::test_exact_boundary_inclusivity PASSED           [ 48%]
tests/test_math_edge_cases.py::test_unequal_sample_count_semantics PASSED       [ 51%]
tests/test_power_cut.py::test_sqlite_process_crash_recovery PASSED              [ 53%]
tests/test_ring_buffer.py::test_fixed_storage_is_below_24_mib_for_default_capacity PASSED [ 55%]
tests/test_ring_buffer.py::test_retains_latest_frames_when_count_limit_reached PASSED [ 58%]
tests/test_ring_buffer.py::test_timestamp_window_includes_boundaries PASSED    [ 60%]
tests/test_ring_buffer.py::test_naive_iso_timestamp_is_treated_as_utc PASSED   [ 62%]
tests/test_ring_buffer.py::test_expiration_occurs_when_stream_time_advances PASSED [ 65%]
tests/test_ring_buffer.py::test_explicit_expire_works_when_stream_is_paused PASSED [ 67%]
tests/test_ring_buffer.py::test_out_of_order_frame_is_rejected_without_mutating_buffer PASSED [ 69%]
tests/test_ring_buffer.py::test_missing_or_invalid_timestamp_and_signal_rejected PASSED [ 72%]
tests/test_ring_buffer.py::test_snapshots_are_detached_from_input_and_buffer PASSED [ 74%]
tests/test_ring_buffer.py::test_unknown_signals_rejected_to_keep_fixed_layout PASSED [ 76%]
tests/test_ring_buffer.py::test_concurrent_appends_remain_bounded PASSED        [ 79%]
tests/test_rollup_worker.py::test_rollup_writes_completed_minutes_and_metadata PASSED [ 81%]
tests/test_rollup_worker.py::test_repeat_flush_is_idempotent PASSED             [ 83%]
tests/test_rollup_worker.py::test_no_write_until_a_minute_is_complete PASSED   [ 86%]
tests/test_rollup_worker.py::test_missing_signals_are_skipped_not_counted_as_zero PASSED [ 88%]
tests/test_rollup_worker.py::test_stationary_vehicle_is_classified_as_idle PASSED [ 90%]
tests/test_rollup_worker.py::test_stationary_vehicle_charging_is_classified_as_charging PASSED [ 93%]
tests/test_rollup_worker.py::test_steady_high_speed_is_classified_as_highway_cruise PASSED [ 95%]
tests/test_rollup_worker.py::test_stop_and_go_driving_is_classified_as_urban PASSED [ 97%]
tests/test_zero_hallucination.py::test_slm_end_to_end_latency_and_safety_gates PASSED [100%]

============================== 43 passed in 5.33s ==============================

```

### Newly Added Test Modules

#### 1. `tests/test_integration_e2e.py`

Validates the complete lifecycle across both tracks:

* High-frequency samples are ingested into Track B's `TelemetryRingBuffer`.
* Completed minutes are flushed to Tier 2 (`telemetry_rollup_1m`) via `flush_buffer_to_rollup()`.
* An abnormal spike is detected, and `record_safety_event()` commits the event and freeze frames.
* Track A's `SLMDiagnosticRuntime.diagnose_event_end_to_end()` executes under deadline, returning a `VERIFIED` status with exact numerical grounding.

#### 2. `tests/test_boundary_hardening.py`

Hardening tests addressing the reviewer's specific recommendations:

* **`test_full_60s_rollup_window_aggregation`**: Streams 600 distinct frames (10 Hz for 60.0s) with linearly varying values ($10 \to 70$ km/h) into `TelemetryRingBuffer`. Asserts that the rollup worker computes exact aggregates (`min=10.0`, `max=70.0`, `avg=40.0`, `delta=60.0`, `sample_count=600`).
* **`test_ring_buffer_wraparound_eviction_integrity`**: Pushes 150 frames through a 100-capacity buffer to assert FIFO wraparound behavior, validating that frame 0–49 are evicted and frames 50–149 remain bit-identical without memory expansion.
* **`test_null_dtc_diagnostic_purity`**: Records an event with `dtc_code=None` and verifies that the SLM generates `"Event THERMAL_WARNING at 49.2"` while passing the Parity Gate and omitting any fabricated SAE codes (`P0A7F`).

---

## 4. File-by-File Git Diff Summary (`00d88bb`)

| File Path | Nature of Change | Summary of Adjustments |
| --- | --- | --- |
| `src/certus_vss/storage/db_engine.py` | Modified | Added `db_path` parameterization to `init_database` and `get_db_connection`; restored Track B aliases; preserved WAL PRAGMAs. |
| `src/certus_vss/storage/event_capture.py` | Modified | Removed automatic `P0A7F`/`C1001` fallback; supports pure `None` DTCs; preserved dynamic `trigger_signal`. |
| `src/certus_vss/analytics/evidence_builder.py` | Modified | Made `dtc_code` nullable; removed `dtc_code` from strict string validation array. |
| `src/certus_vss/inference/llama_runtime.py` | Modified | Dynamic prefix generation supporting both `"Fault <DTC>"` and `"Event <CATEGORY>"`. |
| `src/certus_vss/ingestion/*` | Added / Track B | Brought in `carla_adapter.py`, `vss_normalizer.py`, `ring_buffer.py`, `sim_stream.py`, `can_adapter.py`. |
| `src/certus_vss/storage/capture_scheduler.py` | Added / Track B | Background post-event capture timer scheduler. |
| `src/certus_vss/storage/rollup_worker.py` | Added / Track B | 1-minute aggregation worker with driving context classification. |
| `tests/test_integration_e2e.py` | Added | Full end-to-end integration test (Ring buffer $\to$ Rollup $\to$ Event Capture $\to$ SLM $\to$ Parity Gate). |
| `tests/test_boundary_hardening.py` | Added | 600-frame rollup boundary, 36k buffer wraparound, and pure null-DTC tests. |
| `docs/track_b/*` | Added / Track B | Imported Aryan's progress documentation and architectural specifications (`B1`–`B4`). |

---

## 5. Instructions for Running & Verifying Locally

To independently verify this baseline in the container sandbox:

```bash
# 1. Switch to the unified integration branch
git checkout integration/phase3-unified

# 2. Start the container sandbox
docker compose run --rm certus-sandbox bash

# 3. Inside the container, re-seed baseline data (if needed)
python3 data/seeds/seed_historical.py

# 4. Run the full 43-test suite
pytest -v

```

All 43 tests will execute and pass in approximately 5 seconds.
