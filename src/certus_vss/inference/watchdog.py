"""
Timeout protection for potentially stalled inference operations.
"""

import multiprocessing
import traceback
from typing import Any, Callable


def _worker(operation: Callable[[], Any], result_queue: Any) -> None:
    """Run the operation in a child process and return its outcome."""
    try:
        result_queue.put(("ok", operation()))
    except BaseException:
        result_queue.put(("error", traceback.format_exc()))


class Watchdog:
    """Run an operation in a process with an enforceable timeout."""

    def __init__(self, timeout_ms: int = 1500):
        if (
            not isinstance(timeout_ms, int)
            or isinstance(timeout_ms, bool)
            or timeout_ms <= 0
        ):
            raise ValueError("timeout_ms must be a positive integer")

        self.timeout_ms = timeout_ms

    def run(self, operation: Callable[[], Any]) -> Any:
        """Return the result, or raise TimeoutError if the deadline expires."""
        context = multiprocessing.get_context()
        result_queue = context.Queue()
        process = context.Process(
            target=_worker,
            args=(operation, result_queue),
            daemon=True,
        )

        process.start()
        process.join(self.timeout_ms / 1000)

        if process.is_alive():
            process.terminate()
            process.join(timeout=0.2)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join()
            result_queue.close()
            raise TimeoutError(
                f"Inference exceeded {self.timeout_ms} ms"
            )

        try:
            status, value = result_queue.get(timeout=0.2)
        except Exception as exc:
            raise RuntimeError(
                "Inference worker exited without returning a result"
            ) from exc
        finally:
            result_queue.close()

        if status == "error":
            raise RuntimeError(f"Inference operation failed:\n{value}")

        return value