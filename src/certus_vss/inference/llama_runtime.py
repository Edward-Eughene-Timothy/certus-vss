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

def summarize_evidence(evidence_json: dict[str, Any]) -> str:
    """Summarize recorded evidence without inventing measurements."""
    if not isinstance(evidence_json, dict):
        raise ValueError("evidence_json must be a dictionary")

    lines = [
        f"Intent: {evidence_json.get('intent', 'not specified')}",
        f"Records returned: {evidence_json.get('row_count', 0)}",
    ]

    requested_signal = evidence_json.get("requested_signal")
    if requested_signal:
        lines.append(f"Requested signal: {requested_signal}")

    observed_signals = evidence_json.get("observed_signals", [])
    if observed_signals:
        lines.append(
            "Observed signals: " + ", ".join(str(s) for s in observed_signals)
        )

    missing_signals = evidence_json.get("missing_signals", [])
    if missing_signals:
        lines.append(
            "IMPORTANT: The following requested signals have NO matching "
            "observations. Their values are unknown, not zero: "
            + ", ".join(str(s) for s in missing_signals)
            + ". Do not claim these signals are present or that no signals "
            "are missing."
        )

    records = evidence_json.get("evidence", [])
    for record in records:
        if not isinstance(record, dict):
            continue

        signal = record.get("vss_path")
        value = record.get("val_avg")
        unit = record.get("unit")

        if signal is not None and value is not None:
            measurement = f"{signal}: recorded average {value}"
            if unit:
                measurement += f" {unit}"
            lines.append(measurement)

    if not records:
        lines.append("No evidence records were returned.")

    lines.append(
        "These records alone do not establish a diagnosis or root cause."
    )
    return "\n".join(lines)

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
    evidence_summary = summarize_evidence(evidence_json)

    prompt = (
        "You are a vehicle telemetry explanation assistant. "
        "Explain the supplied evidence accurately and concisely. "
        "Treat the structured evidence and evidence summary as the only sources "
        "of factual information. "
        "Do not contradict recorded values, invent measurements, or infer "
        "diagnoses or root causes that the evidence does not establish. "
        "Distinguish missing data from a measured value of zero. "
        "If the evidence is insufficient, say so clearly.\n\n"
        f"Evidence summary:\n{evidence_summary}\n\n"
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
    """Generate an explanation grounded in recorded telemetry evidence."""
    if not isinstance(evidence_json, dict):
        raise ValueError("evidence_json must be a dictionary")

    missing_signals = evidence_json.get("missing_signals", [])
    evidence_records = evidence_json.get("evidence", [])

    # Use a deterministic response whenever requested signals are missing.
    # This prevents the model from contradicting the available evidence.
    if missing_signals:
        lines = []

        if evidence_records:
            lines.append("Recorded telemetry measurements:")

            for record in evidence_records:
                if not isinstance(record, dict):
                    continue

                signal = record.get("vss_path")
                value = record.get("val_avg")
                unit = record.get("unit")

                if signal is not None and value is not None:
                    measurement = f"- {signal}: recorded average {value}"
                    if unit:
                        measurement += f" {unit}"
                    lines.append(measurement)

            if len(lines) == 1:
                lines.append("- Evidence records were returned, but no usable measurements were found.")

        signals = ", ".join(str(signal) for signal in missing_signals)
        lines.append(
            f"Missing telemetry: No matching observations were found for {signals}."
        )
        lines.append("Missing signal values are unknown, not zero.")
        lines.append(
            "The available evidence does not establish a diagnosis or root cause."
        )
        return "\n".join(lines)

    # No requested signals are missing, so use the local model as before.
    watchdog = Watchdog(timeout_ms=MODEL_STARTUP_TIMEOUT_MS)
    return watchdog.run(lambda: _generate_with_model(evidence_json))