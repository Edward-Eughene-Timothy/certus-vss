# CertusVSS Integration & Hardening Documentation (v2)

This document records all architectural updates, bug fixes, and verification milestones implemented during the final hardening phase of **CertusVSS Track A (Inference / Watchdog / Analytical Core)** and its integration with Track B.

---

## 1. Summary of Key Architectural Updates

### A. Hermetic Test Isolation (`tests/test_deterministic_math.py`)
* **Update:** Refactored `test_deterministic_math.py` to be fully hermetic by utilizing pytest's ephemeral `tmp_path` fixture.
* **Mechanism:** Spawns a dedicated SQLite database instance per test run, initializes the schema via `init_database(db_path=test_db)`, populates 100 minutes of linear synthetic data with valid constraints (`unit`, `provenance`), and verifies mathematical parity against analytical expectations.

### B. Parameterized Read-Only Query Engine (`src/certus_vss/analytics/query_engine.py`)
* **Update:** Added optional `db_path: Optional[Path] = None` parameter to `DeterministicQueryEngine.__init__`.
* **Mechanism:** Defaults to `DB_PATH` if unassigned, while allowing test suites to query isolated database instances without touching persistent disk storage.

### C. SLM Diagnostic Runtime & Watchdog Hardening (`src/certus_vss/inference/llama_runtime.py` & `watchdog.py`)
* **Update:** 
  - Scaled default CPU threads to `min(4, os.cpu_count() or 2)` to prevent starvation on lower-clocked or virtualized developer hardware.
  - Added pre-warming of both KV cache and GBNF grammar samplers inside `_persistent_worker_loop` before signaling `"READY"`.
  - Capped completion tokens (`max_tokens=16`) to accelerate output generation.
  - Added process liveness checking (`self._proc.is_alive()`) to distinguish real inference timeouts (`DeadlineExceededError`) from silent subprocess crashes (`RuntimeError`).

### D. CI/CD Pipeline and Environment Robustness (`.github/workflows/ci.yml` & `seed_historical.py`)
* **Update:**
  - Added `init_database()` call at the start of `seed_historical.py` so fresh CI runners automatically initialize SQLite tables before executing cleanup deletes.
  - Added GitHub Actions model caching (`actions/cache@v4`) and automated GBNF/GGUF artifact retrieval.

---

## 2. Verification & Test Suite Status
* **Total Test Coverage:** 43 / 43 tests passing successfully.
* **Execution Mode:** Hermetic, reproducible, and containerized via Docker sandbox.
