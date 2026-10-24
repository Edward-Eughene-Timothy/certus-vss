from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DATA_DIR = REPO_ROOT / "data"
CONFIG_DIR = REPO_ROOT / "configs"
MODELS_DIR = REPO_ROOT / "models"
DB_PATH = DATA_DIR / "certus_telemetry.db"
MODEL_GGUF_PATH = MODELS_DIR / "qwen2.5-0.5b-instruct-q4_k_m.gguf"

# Hardware Budgets
MAX_RAM_MB = 1200
WATCHDOG_TIMEOUT_MS = 1500
MODEL_STARTUP_TIMEOUT_MS = 30000
