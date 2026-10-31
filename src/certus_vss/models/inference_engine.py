"""
inference_engine.py: Edge SLM inference wrapper for CertusVSS.
Interfaces with llama.cpp to generate natural-language explanations
from JSON evidence blocks under a strict 1,500ms monotonic watchdog timer.
"""

from pathlib import Path
import time
import json
import logging
from typing import Dict, Any, Optional

try:
    from llama_cpp import Llama
    LLAMA_AVAILABLE = True
except ImportError:
    LLAMA_AVAILABLE = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CertusInference")

# Resolve model path relative to project root
MODELS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "models"
MODEL_PATH = MODELS_DIR / "qwen2.5-0.5b-instruct-q4_k_m.gguf"

SYSTEM_PROMPT = (
    "You are CertusVSS, an embedded automotive intelligence assistant. "
    "Your core rule is 'SQL Computes, SLM Explains.' "
    "You will receive a JSON Evidence Block containing pre-calculated metrics from SQLite. "
    "Summarize these facts clearly and concisely. NEVER perform calculations, "
    "never invent numbers, and never contradict the provided JSON values."
)


class CertusInferenceEngine:
    def __init__(self, model_path: Path = MODEL_PATH, n_threads: int = 2):
        self.model_path = model_path
        self.n_threads = n_threads
        self.llm = None
        self._initialize_model()

    def _initialize_model(self) -> None:
        if not LLAMA_AVAILABLE:
            logger.warning("llama-cpp-python is not installed. Running in mock fallback mode.")
            return

        if not self.model_path.exists():
            logger.warning(f"GGUF model not found at {self.model_path}. Running in mock fallback mode.")
            return

        logger.info(f"[CertusVSS] Loading Qwen SLM model from {self.model_path}...")
        try:
            self.llm = Llama(
                model_path=str(self.model_path),
                n_ctx=2048,
                n_threads=self.n_threads,
                n_gpu_layers=0,  # Pure CPU/ARM execution bound to cgroups
                verbose=False
            )
            logger.info("[CertusVSS] SLM loaded successfully into memory (~398 MB footprint).")
        except Exception as e:
            logger.error(f"Failed to load llama model: {e}")
            self.llm = None

    def explain_evidence(self, evidence: Dict[str, Any], persona: str = "DRIVER", timeout_ms: int = 1500) -> str:
        """
        Generates a natural language explanation from the evidence block
        with an absolute monotonic watchdog timer.
        """
        start_time = time.monotonic()
        
        # Format prompt according to persona
        persona_directive = (
            "Provide a clear, driver-friendly summary focusing on comfort and range."
            if persona == "DRIVER"
            else "Provide a technical service summary with precise telemetry parameters."
        )

        prompt = (
            f"System: {SYSTEM_PROMPT}\n"
            f"Directive: {persona_directive}\n"
            f"Evidence JSON: {json.dumps(evidence)}\n"
            f"Explanation:"
        )

        # Fallback mode if model binary/weights aren't present yet
        if not self.llm:
            time.sleep(0.1)  # Simulate brief inference delay
            if persona == "DRIVER":
                return f"[Mock Driver Summary] Vehicle operating normally based on evidence block."
            else:
                return f"[Mock Engineer Summary] Telemetry metrics verified. Query type: {evidence.get('query_type')}."

        # Real inference with watchdog check
        output_tokens = []
        try:
            stream = self.llm(
                prompt,
                max_tokens=128,
                temperature=0.1,
                stop=["<|im_end|>", "System:"],
                stream=True
            )
            
            for chunk in stream:
                # Check absolute monotonic deadline
                elapsed_ms = (time.monotonic() - start_time) * 1000.0
                if elapsed_ms > timeout_ms:
                    logger.warning(f"Watchdog timeout triggered at {elapsed_ms:.1f}ms! Terminating generation.")
                    output_tokens.append("\n[Warning: Explanation truncated by 1,500ms watchdog limit.]")
                    break
                
                text_piece = chunk["choices"][0]["text"]
                output_tokens.append(text_piece)
                
            return "".join(output_tokens).strip()

        except Exception as e:
            logger.error(f"Inference error: {e}")
            return f"[Error during SLM synthesis: {str(e)}]"


if __name__ == "__main__":
    engine = CertusInferenceEngine()
    sample_evidence = {
        "query_type": "HISTORICAL_TELEMETRY_SUMMARY",
        "time_range_days": 30,
        "metrics": {"speed_avg": 64.8, "temp_max": 41.2}
    }
    print("[Test Driver Persona]")
    print(engine.explain_evidence(sample_evidence, persona="DRIVER"))
    print("\n[Test Engineer Persona]")
    print(engine.explain_evidence(sample_evidence, persona="ENGINEER"))
