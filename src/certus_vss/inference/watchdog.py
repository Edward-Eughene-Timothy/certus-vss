"""
watchdog.py: Monotonic deadline tracking and persistent supervised inference worker.
"""
import time
import multiprocessing as mp
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any, Dict, Optional


class DeadlineExceededError(TimeoutError):
    """Raised when execution exceeds the configured monotonic deadline."""
    pass


class MonotonicWatchdog:
    def __init__(self, deadline_ms: float = 1850.0) -> None:
        self.deadline_ms = float(deadline_ms)
        self._start_ns: Optional[int] = None

    def start(self) -> None:
        self._start_ns = time.perf_counter_ns()

    def elapsed_ms(self) -> float:
        if self._start_ns is None:
            raise RuntimeError("Watchdog timer was not started.")
        return (time.perf_counter_ns() - self._start_ns) / 1_000_000.0

    def remaining_s(self) -> float:
        rem_ms = self.deadline_ms - self.elapsed_ms()
        return max(0.0, rem_ms / 1000.0)


def _persistent_worker_loop(conn: Connection, model_path_str: str, gbnf_str: str, n_threads: int) -> None:
    """Top-level worker process entry point (spawn-safe). Loads llama.cpp once."""
    from llama_cpp import Llama, LlamaGrammar

    llm = Llama(
        model_path=model_path_str,
        n_ctx=256,
        n_threads=n_threads,
        n_batch=256,
        verbose=False,
    )
    grammar = LlamaGrammar.from_string(gbnf_str)
    llm("w", max_tokens=1)
    conn.send(("READY", None))

    while True:
        try:
            if not conn.poll(timeout=None):
                continue
            msg = conn.recv()
        except EOFError:
            break

        if msg is None or msg.get("cmd") == "SHUTDOWN":
            break

        # Support deliberate stall injection for watchdog preemption testing
        stall_ms = float(msg.get("simulate_stall_ms", 0.0))
        if stall_ms > 0:
            time.sleep(stall_ms / 1000.0)

        try:
            res = llm(
                msg["prompt"],
                max_tokens=int(msg.get("max_tokens", 24)),
                temperature=0.0,
                grammar=grammar,
            )
            conn.send(("OK", {
                "text": res["choices"][0]["text"].strip(),
                "tokens": res["usage"]["completion_tokens"],
            }))
        except Exception as exc:
            conn.send(("ERR", str(exc)))
