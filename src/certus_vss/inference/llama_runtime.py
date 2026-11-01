"""
llama_runtime.py: Supervised SLM runtime with end-to-end deadline enforcement and parity gate.
"""
import json
import os
import multiprocessing as mp
from pathlib import Path
from typing import Any, Dict, Optional
from certus_vss.analytics.evidence_builder import EvidenceBuilder, EvidenceMissingError
from certus_vss.inference.watchdog import (
    DeadlineExceededError,
    MonotonicWatchdog,
    _persistent_worker_loop,
)
from certus_vss.inference.validator import ParityMismatchError, verify_diagnostic_parity

DEFAULT_MODEL_PATH = Path("/app/models/qwen2.5-0.5b-instruct-q4_k_m.gguf")
GRAMMAR_PATH = Path("/app/configs/response.gbnf")
DEFAULT_THREADS = min(4, max(2, os.cpu_count() or 2))


class SLMDiagnosticRuntime:
    def __init__(self, model_path: Optional[Path] = None, n_threads: Optional[int] = None) -> None:
        self.model_path = model_path or DEFAULT_MODEL_PATH
        if not self.model_path.exists():
            raise FileNotFoundError(f"Model missing at {self.model_path}")
        self.n_threads = n_threads if n_threads is not None else DEFAULT_THREADS
        self.gbnf_str = GRAMMAR_PATH.read_text(encoding="utf-8")
        self._ctx = mp.get_context("spawn")
        self._parent_conn = None
        self._proc = None
        self._start_worker()

    def _start_worker(self) -> None:
        self._kill_worker()
        parent_conn, child_conn = self._ctx.Pipe(duplex=True)
        proc = self._ctx.Process(
            target=_persistent_worker_loop,
            args=(child_conn, str(self.model_path), self.gbnf_str, self.n_threads),
            daemon=True,
        )
        proc.start()
        child_conn.close()
        try:
            status, info = parent_conn.recv()
        except EOFError as exc:
            proc.join(timeout=1.0)
            raise RuntimeError(
                f"SLM worker terminated unexpectedly during startup (exitcode={proc.exitcode})"
            ) from exc

        if status != "READY":
            proc.join(timeout=1.0)
            raise RuntimeError(f"Failed to initialize persistent SLM worker: {info}")
        self._parent_conn = parent_conn
        self._proc = proc

    def _kill_worker(self) -> None:
        if self._proc is not None and self._proc.is_alive():
            self._proc.kill()
            self._proc.join()
        if self._parent_conn is not None:
            try:
                self._parent_conn.close()
            except Exception:
                pass
        self._proc = None
        self._parent_conn = None

    def close(self) -> None:
        self._kill_worker()

    def generate_diagnostic(
        self,
        evidence: Dict[str, Any],
        max_tokens: int = 16,
        deadline_ms: float = 1850.0,
        simulate_stall_ms: float = 0.0,
        _watchdog: Optional[MonotonicWatchdog] = None,
    ) -> Dict[str, Any]:
        if self._proc is None or not self._proc.is_alive():
            self._start_worker()

        wd = _watchdog or MonotonicWatchdog(deadline_ms=deadline_ms)
        if _watchdog is None:
            wd.start()

        if not isinstance(evidence, dict):
            raise EvidenceMissingError("Evidence payload must be a dictionary")
        ev = evidence.get("event")
        if not isinstance(ev, dict):
            raise EvidenceMissingError("Missing event block in evidence")
        if ev.get("trigger_value") is None or isinstance(ev.get("trigger_value"), bool):
            raise EvidenceMissingError("Missing verified trigger_value")

        tv = round(float(ev["trigger_value"]), 2)
        dtc = ev.get("dtc_code")
        category = ev.get("category", "EVENT").strip().upper()

        tag = f"Fault {dtc}" if dtc else f"Event {category}"

        prefix = f'{{"summary": "{tag} at {tv}", "key_facts": ["Trigger value '
        prompt = f"Value={tv}\n" + prefix

        rem_s = wd.remaining_s()
        if rem_s <= 0:
            raise DeadlineExceededError(f"Deadline {deadline_ms}ms expired before inference dispatch")

        self._parent_conn.send({
            "cmd": "INFER",
            "prompt": prompt,
            "max_tokens": max_tokens,
            "simulate_stall_ms": simulate_stall_ms,
        })

        if not self._parent_conn.poll(timeout=rem_s):
            elapsed = wd.elapsed_ms()
            proc_alive = self._proc is not None and self._proc.is_alive()
            exit_code = None if proc_alive else (self._proc.exitcode if self._proc else None)
            self._kill_worker()
            self._start_worker()
            if not proc_alive:
                raise RuntimeError(
                    f"Inference worker subprocess crashed unexpectedly (exit_code={exit_code}) after {elapsed:.2f}ms"
                )
            raise DeadlineExceededError(
                f"Inference worker killed after exceeding {deadline_ms}ms budget (elapsed: {elapsed:.2f}ms)"
            )

        try:
            status, payload = self._parent_conn.recv()
        except EOFError as exc:
            exit_code = self._proc.exitcode if self._proc else None
            self._kill_worker()
            self._start_worker()
            raise RuntimeError(
                f"Inference worker pipe closed unexpectedly (exit_code={exit_code})"
            ) from exc

        if status != "OK":
            raise RuntimeError(f"Worker inference error: {payload}")

        raw = prefix + payload["text"]
        parsed_json = json.loads(raw)

        # Validate numerical parity against SQLite ground truth
        verify_diagnostic_parity(parsed_json, evidence)

        # System-controlled verification status (set only after parity gate succeeds)
        parsed_json["evidence_status"] = "VERIFIED"

        elapsed_ms = wd.elapsed_ms()
        if elapsed_ms > deadline_ms:
            raise DeadlineExceededError(f"End-to-end latency {elapsed_ms:.2f}ms exceeded {deadline_ms}ms")

        return {
            "status": "SUCCESS",
            "diagnostics": parsed_json,
            "verified_evidence": evidence,
            "latency_ms": round(elapsed_ms, 2),
            "tokens_generated": payload["tokens"],
            "model": "qwen2.5-0.5b-instruct-q4_k_m",
        }

    def diagnose_event_end_to_end(
        self,
        event_id: int,
        deadline_ms: float = 1850.0,
    ) -> Dict[str, Any]:
        """Measures full end-to-end latency: SQLite query + EvidenceBuilder + SLM inference + Parity check."""
        if self._proc is None or not self._proc.is_alive():
            self._start_worker()
        wd = MonotonicWatchdog(deadline_ms=deadline_ms)
        wd.start()
        builder = EvidenceBuilder()
        evidence = builder.build_event_evidence(event_id=event_id)
        return self.generate_diagnostic(evidence=evidence, deadline_ms=deadline_ms, _watchdog=wd)


if __name__ == "__main__":
    print(f"[Init] Starting Persistent Supervised SLM Worker ({DEFAULT_THREADS} CPU threads)...")
    rt = SLMDiagnosticRuntime()
    try:
        res = rt.diagnose_event_end_to_end(event_id=1, deadline_ms=1850.0)
        print("=" * 64)
        print(f"End-to-End Latency : {res['latency_ms']} ms (Budget: < 1850.0 ms)")
        print(f"Summary            : {res['diagnostics']['summary']}")
        print(f"Key Facts          : {res['diagnostics']['key_facts']}")
        print(f"Evidence Status    : {res['diagnostics']['evidence_status']}")
        print("=" * 64)
    finally:
        rt.close()
