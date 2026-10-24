"""
Local llama.cpp explanation runtime.

SQL and the evidence builder remain responsible for retrieving verified
data. The language model may explain supplied evidence but must not invent
measurements or diagnostic facts.
"""

import json
from pathlib import Path
from typing import Any

from certus_vss.common.config import (
    MODEL_GGUF_PATH,
    MODEL_STARTUP_TIMEOUT_MS,
    WATCHDOG_TIMEOUT_MS,
)
from certus_vss.inference.watchdog import Watchdog


def _generate_with_model(evidence_json: dict[str, Any]) -> str:
    """Generate an explanation using the local GGUF model."""
    model_path = Path(MODEL_GGUF_PATH)

    if not model_path.is_file():
        raise FileNotFoundError(
            f"Local model not found: {model_path}. "
            "Download the configured GGUF model before inference."
        )

    try:
        from llama_cpp import Llama
    except ImportError as exc:
        raise RuntimeError(
            "llama-cpp-python is not installed in this environment."
        ) from exc

    model = Llama(
        model_path=str(model_path),
        n_ctx=2048,
        n_gpu_layers=-1,
        verbose=False,
    )

    evidence_text = json.dumps(evidence_json, ensure_ascii=False, sort_keys=True)
    prompt = (
        "You are a vehicle telemetry explanation assistant. "
        "Explain only what the supplied evidence supports. "
        "Do not invent sensor readings, calculations, fault codes, or causes. "
        "If evidence is missing or inconclusive, say so clearly.\n\n"
        f"Verified evidence JSON:\n{evidence_text}\n\n"
        "Concise explanation:"
    )

    response = model.create_chat_completion(
        messages=[
            {
                "role": "system",
                "content": (
                    "Use only the provided evidence. Never claim that a "
                    "measurement or calculation exists unless it appears "
                    "in the evidence."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        max_tokens=200,
        temperature=0.1,
    )

    return response["choices"][0]["message"]["content"].strip()


def generate_explanation(evidence_json: dict[str, Any]) -> str:
    """Generate a local explanation with a bounded execution time."""
    if not isinstance(evidence_json, dict):
        raise ValueError("evidence_json must be a dictionary")

    watchdog = Watchdog(timeout_ms=MODEL_STARTUP_TIMEOUT_MS)
    return watchdog.run(lambda: _generate_with_model(evidence_json))