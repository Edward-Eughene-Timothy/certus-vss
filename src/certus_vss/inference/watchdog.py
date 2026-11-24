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

def validate_acquisition_context(
    llm: Any,
    prompt: str,
    max_tokens: int,
    context_limit: int = 256,
) -> int:
    """Reject acquisition prompts that exceed the model context budget."""
    if max_tokens < 1:
        raise ValueError("max_tokens must be positive")

    prompt_tokens = len(
        llm.tokenize(
            prompt.encode("utf-8"),
            add_bos=True,
        )
    )

    if prompt_tokens + max_tokens > context_limit:
        raise ValueError(
            "Acquisition prompt exceeds context budget: "
            f"{prompt_tokens} prompt tokens + "
            f"{max_tokens} output tokens > "
            f"{context_limit} context tokens"
        )

    return prompt_tokens

def _persistent_worker_loop(
    conn: Connection,
    model_path_str: str,
    gbnf_str: str,
    n_threads: int,
    acquisition_gbnf_str: Optional[str] = None,
) -> None:
    """Load one model and support request-specific response grammars."""
    try:
        from llama_cpp import Llama, LlamaGrammar

        llm = Llama(
            model_path=model_path_str,
            n_ctx=256,
            n_threads=n_threads,
            n_batch=128,
            use_mmap=True,
            verbose=False,
        )

        grammars = {
            "diagnostic": LlamaGrammar.from_string(gbnf_str),
        }

        if acquisition_gbnf_str is not None:
            grammars["acquisition"] = LlamaGrammar.from_string(
                acquisition_gbnf_str
            )

        # Preserve the existing diagnostic warmup.
        warmup_prefix = (
            'Value=35.0\n'
            '{"summary": "Event THERMAL_WARNING at 35.0", '
            '"key_facts": ["Trigger value '
        )
        llm(
            warmup_prefix,
            max_tokens=4,
            temperature=0.0,
            grammar=grammars["diagnostic"],
        )

        conn.send(("READY", None))

    except Exception as init_exc:
        conn.send(("INIT_ERR", str(init_exc)))
        return

    while True:
        try:
            if not conn.poll(timeout=None):
                continue
            msg = conn.recv()
        except EOFError:
            break

        if msg is None or msg.get("cmd") == "SHUTDOWN":
            break

        stall_ms = float(msg.get("simulate_stall_ms", 0.0))
        if stall_ms > 0:
            time.sleep(stall_ms / 1000.0)

        try:
            grammar_name = msg.get("grammar", "diagnostic")

            if grammar_name not in grammars:
                raise ValueError(
                    f"Unsupported or unavailable grammar: {grammar_name}"
                )

            max_tokens = int(msg.get("max_tokens", 12))

            if max_tokens < 1:
                raise ValueError("max_tokens must be positive")

            if grammar_name == "acquisition":
                validate_acquisition_context(
                    llm,
                    msg["prompt"],
                    max_tokens,
                )

            res = llm(
                msg["prompt"],
                max_tokens=max_tokens,
                temperature=0.0,
                grammar=grammars[grammar_name],
            )

            conn.send(("OK", {
                "text": res["choices"][0]["text"].strip(),
                "tokens": res["usage"]["completion_tokens"],
            }))

        except Exception as exc:
            conn.send(("ERR", str(exc)))
