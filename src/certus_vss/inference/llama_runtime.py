"""
llama_runtime.py: Supervised SLM runtime with end-to-end deadline enforcement and parity gate.
"""
import json
import os
import multiprocessing as mp
from pathlib import Path
from typing import Any, Dict, Optional
from certus_vss.analytics.evidence_builder import EvidenceBuilder, EvidenceMissingError
from certus_vss.analytics.query_engine import DeterministicQueryEngine
from certus_vss.inference.watchdog import (
    DeadlineExceededError,
    MonotonicWatchdog,
    _persistent_worker_loop,
)
from certus_vss.inference.validator import (
    ParityMismatchError,
    verify_diagnostic_parity,
    verify_acquisition_parity,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_MODEL_PATH = Path(
    os.environ.get(
        "CERTUS_MODEL_PATH",
        PROJECT_ROOT / "models" / "qwen2.5-0.5b-instruct-q4_k_m.gguf",
    )
)

GRAMMAR_PATH = Path(
    os.environ.get(
        "CERTUS_GRAMMAR_PATH",
        PROJECT_ROOT / "configs" / "response.gbnf",
    )
)

ACQUISITION_GRAMMAR_PATH = Path(
    os.environ.get(
        "CERTUS_ACQUISITION_GRAMMAR_PATH",
        PROJECT_ROOT / "configs" / "acquisition_response.gbnf",
    )
)

DEFAULT_THREADS = 2

def prepare_acquisition_inference_evidence(
    evidence: Dict[str, Any],
    max_samples: int = 3,
) -> Dict[str, Any]:
    """Create a bounded, traceable evidence payload for acquisition inference."""
    if not isinstance(evidence, dict):
        raise EvidenceMissingError("Acquisition evidence must be a dictionary")

    if isinstance(max_samples, bool) or not isinstance(max_samples, int):
        raise ValueError("max_samples must be an integer")

    if max_samples < 1:
        raise ValueError("max_samples must be positive")

    samples = evidence.get("samples")
    if not isinstance(samples, list) or not samples:
        raise EvidenceMissingError("Missing acquisition samples")

    returned_count = evidence.get("sample_count", len(samples))

    if (
        isinstance(returned_count, bool)
        or not isinstance(returned_count, int)
        or returned_count != len(samples)
    ):
        raise ValueError("sample_count does not match returned samples")

    if len(samples) <= max_samples:
        selected = list(samples)
        selection = "all"
    elif max_samples == 1:
        selected = [samples[0]]
        selection = "first"
    elif max_samples == 2:
        selected = [samples[0], samples[-1]]
        selection = "first_last"
    else:
        middle_index = len(samples) // 2
        selected = [samples[0], samples[middle_index], samples[-1]]
        selection = "first_middle_last"

    signal = evidence.get("signal") or samples[0].get("signal")
    unit = samples[0].get("unit")

    bounded = {
        "actor_id": evidence.get("actor_id"),
        "signal": signal,
        "unit": unit,
        "samples": [
            {
                "sample_id": sample.get("sample_id"),
                "value": sample.get("value"),
                "unit": sample.get("unit"),
            }
            for sample in selected
        ],
        "samples_returned": returned_count,
        "samples_supplied_to_model": len(selected),
        "sample_selection": selection,
    }

    return bounded

class SLMDiagnosticRuntime:
    def __init__(self, model_path: Optional[Path] = None, n_threads: Optional[int] = None) -> None:
        self.model_path = model_path or DEFAULT_MODEL_PATH
        if not self.model_path.exists():
            raise FileNotFoundError(f"Model missing at {self.model_path}")
        self.n_threads = n_threads if n_threads is not None else DEFAULT_THREADS
        self.gbnf_str = GRAMMAR_PATH.read_text(encoding="utf-8")
        self.acquisition_gbnf_str = ACQUISITION_GRAMMAR_PATH.read_text(
            encoding="utf-8"
        )
        self._ctx = mp.get_context("spawn")
        self._parent_conn = None
        self._proc = None
        self._start_worker()

    def _start_worker(self) -> None:
        self._kill_worker()
        parent_conn, child_conn = self._ctx.Pipe(duplex=True)
        proc = self._ctx.Process(
            target=_persistent_worker_loop,
            args=(
                child_conn,
                str(self.model_path),
                self.gbnf_str,
                self.n_threads,
                self.acquisition_gbnf_str,
            ),
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
        max_tokens: int = 12,
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

    def generate_acquisition(
        self,
        evidence: Dict[str, Any],
        max_tokens: int = 48,
        deadline_ms: float = 1850.0,
        simulate_stall_ms: float = 0.0,
        _watchdog: Optional[MonotonicWatchdog] = None,
    ) -> Dict[str, Any]:
        """Generate an explanation grounded in CARLA acquisition samples."""
        if not isinstance(evidence, dict):
            raise EvidenceMissingError("Acquisition evidence must be a dictionary")

        samples = evidence.get("samples")
        if not isinstance(samples, list) or not samples:
            raise EvidenceMissingError("Missing acquisition samples")

        inference_evidence = prepare_acquisition_inference_evidence(evidence)

        if self._proc is None or not self._proc.is_alive():
            self._start_worker()

        wd = _watchdog or MonotonicWatchdog(deadline_ms=deadline_ms)
        if _watchdog is None:
            wd.start()

        prompt = (
            "State the signal's measured value and unit in one short sentence. "
            "Include an exact numeric sample value in the summary. "
            "Put the exact signal identifier in the single key_fact. "
            "Use only supplied evidence; never invent measurements.\n"
            + json.dumps(inference_evidence, separators=(",", ":"))
            + '\n{"summary": "'
        )

        rem_s = wd.remaining_s()
        if rem_s <= 0:
            raise DeadlineExceededError(
                f"Deadline {deadline_ms}ms expired before inference dispatch"
            )

        self._parent_conn.send({
            "cmd": "INFER",
            "prompt": prompt,
            "max_tokens": max_tokens,
            "simulate_stall_ms": simulate_stall_ms,
            "grammar": "acquisition",
        })

        if not self._parent_conn.poll(timeout=rem_s):
            elapsed = wd.elapsed_ms()
            self._kill_worker()
            self._start_worker()
            raise DeadlineExceededError(
                f"Acquisition inference exceeded deadline "
                f"({elapsed:.2f}ms)"
            )

        try:
            status, payload = self._parent_conn.recv()
        except EOFError as exc:
            self._kill_worker()
            self._start_worker()
            raise RuntimeError(
                "Acquisition inference worker pipe closed unexpectedly"
            ) from exc

        if status != "OK":
            raise RuntimeError(f"Acquisition inference error: {payload}")

        raw = '{"summary": "' + payload["text"]

        try:
            parsed_json = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Invalid acquisition JSON: {exc}. "
                f"Generated tokens: {payload.get('tokens')}. "
                f"Raw output: {raw!r}"
            ) from exc

        verify_acquisition_parity(parsed_json, evidence)
        parsed_json["evidence_status"] = "VERIFIED"

        elapsed_ms = wd.elapsed_ms()
        if elapsed_ms > deadline_ms:
            raise DeadlineExceededError(
                f"Acquisition inference exceeded deadline "
                f"({elapsed_ms:.2f}ms)"
            )

        return {
            "status": "SUCCESS",
            "diagnostics": parsed_json,
            "verified_evidence": inference_evidence,
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

    def diagnose_acquisition_end_to_end(
        self,
        actor_id: int,
        signal: str,
        start_epoch_s: int,
        end_epoch_s: int,
        limit: int = 1000,
        deadline_ms: float = 1850.0,
    ) -> Dict[str, Any]:
        """Query persisted CARLA evidence and verify its SLM interpretation."""
        wd = MonotonicWatchdog(deadline_ms=deadline_ms)
        wd.start()

        query_engine = DeterministicQueryEngine()
        evidence_json = query_engine.build_evidence_block(
            "acquisition",
            actor_id=actor_id,
            signal=signal,
            start_epoch_s=start_epoch_s,
            end_epoch_s=end_epoch_s,
            limit=limit,
        )
        evidence = json.loads(evidence_json)

        if evidence.get("status") != "SUCCESS" or not evidence.get("samples"):
            raise EvidenceMissingError(
                f"No acquisition evidence found for actor {actor_id}, "
                f"signal {signal!r}, in the requested time range."
            )

        return self.generate_acquisition(
            evidence=evidence,
            deadline_ms=deadline_ms,
            _watchdog=wd,
        )

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
