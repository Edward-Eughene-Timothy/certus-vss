# CertusVSS: Phase 2 Engineering & Safety Review
**Target Audience**: Aryan  
**Status**: Completed & Verified (`8/8 pytest suite passing`)  
**Real-Time Deadline**: < 1,850.0 ms (Achieved: **~685 ms end-to-end**)  

---

## 1. Executive Summary
Phase 2 establishes a deterministic, real-time diagnostic synthesis pipeline for CertusVSS running inside an air-gapped Fedora x86 container. The pipeline combines zero-copy SQLite telemetry extraction with a grammar-constrained Small Language Model (Qwen 2.5 0.5B Instruct Q4_K_M GGUF) pinned to 2 CPU threads. 

Rather than relying on unconstrained probabilistic generation, the system functions as an **inference-gated serialization and verification pipeline** with strict fail-closed safety properties.

---

## 2. Core Architectural Pillars

### A. Fail-Closed Evidence Extraction (`evidence_builder.py`)
* **Design Principle**: Zero fallback defaults. If database records are missing, corrupted, or contain non-finite numbers (`NaN`, `Inf`), the builder instantly raises `EvidenceMissingError` rather than guessing or manufacturing telemetry.
* **Data Contract**: Directly queries SQLite rollup tables (`telemetry_rollup_1m`) for exact 24-hour baseline aggregates (`average`, `min`, `max`, `spread`) and high-frequency freeze-frame waveforms.

### B. Persistent Supervised Inference Worker (`watchdog.py` & `llama_runtime.py`)
* **Process Isolation**: Uses a persistent background worker process via Python's `spawn` context and bidirectional IPC `Pipe` to avoid C++ thread deadlocks or OpenMP fork contention.
* **Preemptive Timeout Enforcer**: The supervisor measures end-to-end latency starting at request ingress (covering SQL queries, evidence builder, SLM generation, and parity validation). If execution exceeds **1,850 ms**, the supervisor sends `SIGKILL` to terminate the worker instantly, discarding the request and automatically respawning a clean worker instance.

### C. Grammar-Based Normal Form & Parity Gate (`response.gbnf` & `validator.py`)
* **GBNF Constraints**: Constrains SLM token logits into a strict numeric completion followed by a fixed JSON closing structure, eliminating trailing token loops and syntax malformation.
* **Post-Generation Parity Gate**: Operates as an independent verification layer using a robust regular expression (`_NUMBER_PATTERN`) to extract all numeric tokens from the generated output. Every extracted number is cross-referenced against SQLite ground truth; any ungrounded metric or missing trigger value immediately triggers `ParityMismatchError`.

---

## 3. Verification & Test Suite Summary (`pytest -v`)
The test suite covers database pragmas, mathematical determinism, edge cases, process-crash recovery, and safety gates:

1. **`test_db_pragmas.py`**: Verifies WAL mode, synchronous settings, and memory-mapped I/O pragmas.
2. **`test_deterministic_math.py`**: Asserts mathematical rollup consistency across time windows.
3. **`test_math_edge_cases.py`**: Validates empty-window handling and exact boundary inclusivity.
4. **`test_intent_parsing.py`**: Verifies deterministic VSS signal query routing and fail-closed rejection of unknown query intents.
5. **`test_power_cut.py`**: Simulates an abrupt child-process crash (`os._exit(1)`) mid-transaction; verifies that automatic SQLite WAL recovery restores database integrity (`integrity_check == 'ok'`) and rolls back uncommitted dirty states.
6. **`test_zero_hallucination.py`**: Asserts end-to-end latency under 1,850 ms, validates fail-closed evidence stripping, tests explicit rejection of hallucinated numbers (`37.4`) and substring traps (`142.85`), and exercises watchdog `SIGKILL` preemption and worker recovery.
