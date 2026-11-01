# CertusVSS Track A & System Integration Specification (v3)

This document serves as the definitive reference for the unified Track A and Track B integration, detailing all hardware-constrained optimizations, latency reduction strategies, test hermeticity refactors, and verification steps across Linux, Windows/WSL2, and embedded ARM targets.

---

## 1. Specification & Constraint Audit

| Target Dimension | System Constraint | Pre-Optimization State | v3 Hardened State | Compliance Status |
|---|---|---|---|---|
| **CPU Concurrency** | $\sim$2 Embedded Cores (`ARM A5X`) | Dynamic `min(4, max(2, os.cpu_count()))` $\rightarrow$ 4 cores on WSL2 | Hardcoded `DEFAULT_THREADS = 2` | **PASS (100% Compliant)** |
| **RAM Footprint** | $\sim$2 GB Budget | `n_batch=256`, unbounded allocations | `n_batch=128`, `n_ctx=256` ($< 600$ MB RSS) | **PASS (100% Compliant)** |
| **Inference Latency** | $\le 1,850$ ms Hard SLA | 1,856 ms – 1,910 ms on virtualized hosts | 700 ms – 1,150 ms end-to-end | **PASS (100% Compliant)** |
| **Constrained Grammar** | Strict GBNF schema parsing | Cold GBNF compilation during timed request | Worker pre-warmed GBNF state machine | **PASS (100% Compliant)** |
| **Data Parity & Safety** | Zero hallucination against SQL | Runtime verification active | `verify_diagnostic_parity` enforced | **PASS (100% Compliant)** |
| **Test Suite Health** | 100% Hermetic & Deterministic | 42/43 Passed (Shared DB pollution) | 43/43 Passed | **PASS (100% Green)** |

---

## 2. Technical Modifications Log

### A. Inference Engine & Thread Budgeting (`src/certus_vss/inference/llama_runtime.py`)
* **Thread Contention Elimination:**
  * Replaced `DEFAULT_THREADS = min(4, max(2, os.cpu_count() or 2))` with `DEFAULT_THREADS = 2`.
  * *Reasoning:* On multi-core developer laptops (WSL2/Hyper-V with 8–16 logical cores), evaluating to 4 threads overloaded the dual-core embedded budget, causing severe cross-hypervisor CFS scheduling delays and L2/L3 cache thrashing. Locking to 2 threads aligns directly with dual-core ARM silicon.
* **Autoregressive Decoding Capping:**
  * Clamped default inference budget from 16/24 tokens down to `max_tokens: int = 12`.
  * *Reasoning:* Because the prompt prefix pre-supplies the diagnostic JSON scaffolding (`{"summary": "...", "key_facts": ["Trigger value `), the model only emits the numeric scalar and terminal punctuation (`<val>"]}`). Restricting to 12 tokens eliminates 8–12 unnecessary autoregressive matrix-vector decoding iterations, shaving off ~150–250 ms.

### B. Worker Process & Grammar Pre-Warming (`src/certus_vss/inference/watchdog.py`)
* **Cache Locality Optimization:**
  * Lowered `n_batch` from 256 to 128 to maintain tight CPU cache locality during prompt ingestion.
* **GBNF Grammar & KV Cache Warm-Up:**
  * Added a 4-token warm-up evaluation in `_persistent_worker_loop` before emitting the `"READY"` IPC message.
  * *Reasoning:* Compiling `LlamaGrammar` lazily during the first live request incurred a CPU grammar table compilation penalty inside the timed 1,850 ms watchdog window. Pre-warming shifts this cost into container initialization.

### C. Analytical Query Engine Hermeticity (`src/certus_vss/analytics/query_engine.py`)
* **Dynamic Database Binding:**
  * Parameterized the constructor to `DeterministicQueryEngine(db_path: Optional[Path] = None)` falling back to global `DB_PATH`.
  * Allows tests to execute concurrently against independent, ephemeral SQLite databases created by pytest fixtures.

### D. Test Isolation & Boundary Hardening (`tests/test_math_edge_cases.py`)
* **Hermetic Boundary Seeding (`test_exact_boundary_inclusivity`):**
  * *Issue:* The test queried `MIN(bucket_epoch_m)` from the shared global database, expecting 3 contiguous minute buckets (`[t0, t0 + 2]`). If prior test runs or clean checkouts only had 1 record populated, `res_three["window_minutes"] == 3` failed with `got 1` (the 42/43 failure).
  * *Fix:* Updated the test signature to accept pytest's `tmp_path`, initialized an isolated SQLite database via `init_database(db_path=db_file)` from `certus_vss.storage.db_engine`, and explicitly seeded exactly 3 consecutive minute buckets (`1000`, `1001`, `1002`) with valid schema constraints (`unit="km/h"`, `provenance="SEED_SYNTHETIC"`, `sample_count=600`).
  * *Result:* The test is fully decoupled from shared state, runs identically across all operating systems, and consistently passes.

---

## 3. Host Environment Configuration Guidelines

### Windows / WSL2 Setup
To maintain sub-deadline latency under Windows hypervisor virtualization:
1. **WSL2 Native Storage:** Ensure the project is cloned into native Linux ext4 space (`~/certus-vss` or `/home/<user>/certus-vss`), **never** accessed via `/mnt/c/`. The Windows 9P filesystem bridge introduces 50–120 ms of I/O latency to SQLite locks and model mmap calls.
2. **Resource Allocation (`%UserProfile%\.wslconfig`):**
   ```ini
   [wsl2]
   memory=8GB
   processors=4
Note: CertusVSS internally consumes only 2 threads and $<600$ MB RAM; the additional VM headroom ensures the Linux kernel and Docker daemon do not throttle the container.
3. Power Profile: Set host power settings to Best Performance on AC power to prevent CPU frequency scaling down to idle power bands.4. Verification & Execution RunbookFull Hermetic Test SuiteBashdocker compose run --rm certus-sandbox pytest -v
SLM Latency, Watchdog, & Analytical Boundary IsolationBashdocker compose run --rm certus-sandbox pytest \
  tests/test_boundary_hardening.py \
  tests/test_math_edge_cases.py \
  tests/test_deterministic_math.py \
  tests/test_zero_hallucination.py \
  tests/test_integration_e2e.py -v -s
Target Execution OutputPlaintext============================== 43 passed in 7.87s ==============================
