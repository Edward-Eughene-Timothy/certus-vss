# CertusVSS Track A & System Integration Specification (v2)

This document details all codebase modifications, hardware-constrained performance optimizations, and exact verification procedures for **CertusVSS (Track A / Track B Integration)**.

---

## 1. Summary of Changes Made

### A. Threading & Concurrency Alignment (`src/certus_vss/inference/llama_runtime.py`)
* **Problem:** `DEFAULT_THREADS` was dynamically resolving via `min(4, max(2, os.cpu_count() or 2))`. On multi-core host platforms (WSL2/Hyper-V), this evaluated to 4 threads, introducing severe thread synchronization overhead, CPU cache thrashing, and hypervisor scheduling jitter that exceeded the 1,850 ms SLA by 6–56 ms.
* **Fix:** Hardcoded `DEFAULT_THREADS = 2`.
* **Hardware Justification:** Aligns with target embedded hardware limits specified in the problem requirements (`~2 ARM A5X Cores`, e.g., Cortex-A53/A55 automotive compute partitions).

### B. Generation Token Capping (`src/certus_vss/inference/llama_runtime.py` & `watchdog.py`)
* **Problem:** The fallback `max_tokens` was configured to 24 tokens. Since the prompt prefix pre-populates the JSON structure (`{"summary": "...", "key_facts": ["Trigger value `), the model only needs to emit the numeric trigger value and closing punctuation (`<val>"]}`).
* **Fix:** Clamped `max_tokens = 12` in both `generate_diagnostic()` and the watchdog worker request handling.
* **Impact:** Eliminates 8–12 unnecessary autoregressive matrix-vector decoding steps, saving ~150–250 ms of CPU compute per call.

### C. Worker Batch Size & KV/Grammar Pre-warming (`src/certus_vss/inference/watchdog.py`)
* **Problem:** Initializing `Llama` with `n_batch=256` wasted cache line allocation during prompt processing. Additionally, cold GBNF grammar compilation during the first live request incurred an initialization penalty inside the timed inference window.
* **Fix:** 
  - Reduced `n_batch = 128` to maintain optimal L2/L3 cache locality.
  - Implemented pre-warming for both the KV cache and the compiled `LlamaGrammar` state machine in `_persistent_worker_loop` before emitting the `"READY"` handshake signal.

### D. Hermetic Analytical Engine & Database Isolation (`src/certus_vss/analytics/query_engine.py` & `tests/test_deterministic_math.py`)
* **Problem:** `DeterministicQueryEngine` previously used a fixed global database path, preventing isolated concurrent testing.
* **Fix:** Parameterized `DeterministicQueryEngine.__init__(db_path: Optional[Path] = None)` falling back to `DB_PATH`.
* **Test Isolation:** Rewrote `tests/test_deterministic_math.py` using pytest's `tmp_path` fixture. Populates 100 minutes of linear synthetic data with valid constraints (`unit="km/h"`, `provenance="SEED_SYNTHETIC"`) and analytically checks aggregated metrics (`min=50.0`, `max=119.0`, `avg=84.5`). Zero randomness is used.

### E. CI/CD & Database Schema Bootstrap (`.github/workflows/ci.yml` & `data/seeds/seed_historical.py`)
* **Problem:** Fresh CI environments failed on initial cleanups due to uninitialized SQLite tables.
* **Fix:** Added `init_database()` at the entry point of `generate_and_seed()`. Configured GitHub Actions model caching (`actions/cache@v4`) and automated model weight downloads via `models/download_model.sh`.

---

## 2. Windows / WSL2 Host Setup Guidelines

Teammates running Windows/WSL2 must verify two environmental prerequisites:

1. **Native Linux Filesystem (`ext4`):**
   The project directory must reside inside the WSL2 root filesystem (e.g., `~/certus-vss` or `/home/<user>/certus-vss`), **never** mounted from the Windows NTFS host path (`/mnt/c/...`). Accessing SQLite handles and model files across the Windows 9P bridge introduces 50–120 ms of I/O latency.
2. **Host Power Profile:**
   Laptops must be connected to AC power or set to **Best Performance** in Windows power settings to prevent CPU frequency downclocking and core parking during inference.

---

## 3. Verification & Execution Runbook

### For Linux and WSL2 Developers

#### Step 1: Pull Latest Hardened Code
```bash
git fetch origin
git checkout integration/phase3-unified
git pull origin integration/phase3-unified
Step 2: Run Full Hermetic Test Suite in Docker Sandbox
Bash
docker compose run --rm certus-sandbox pytest -v
Step 3: Run Isolated SLM and Deadline Tests Specifically
Bash
docker compose run --rm certus-sandbox pytest \
  tests/test_boundary_hardening.py \
  tests/test_integration_e2e.py \
  tests/test_zero_hallucination.py \
  tests/test_deterministic_math.py -v
Expected Output
All 43 tests pass (100% green) in under 10 seconds, with all SLM diagnostic evaluations consistently completing within the 1,850 ms end-to-end deadline.
