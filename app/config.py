import os
import json
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv("CHEMELLM_DATA_DIR", str(ROOT / "data"))).resolve()
ARTIFACTS = DATA / "artifacts"
for folder in (DATA, ARTIFACTS):
    folder.mkdir(parents=True, exist_ok=True)
AUTH_TOKEN = os.getenv("CHEMELLM_AUTH_TOKEN", "")
MAX_UPLOAD = 25 * 1024 * 1024
SETTINGS_PATH = DATA / "model-settings.json"
_settings_lock = threading.Lock()


def saved_model_settings():
    if not SETTINGS_PATH.exists():
        return {}
    with SETTINGS_PATH.open(encoding="utf-8") as source:
        return json.load(source)


def save_model_settings(value):
    """Atomically save server-side overrides; blank keys preserve the existing secret."""
    with _settings_lock:
        current = model_config()
        data = value.model_dump(exclude={"api_key", "clear_api_key"})
        data["key"] = "" if value.clear_api_key else value.api_key or current["key"]
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=SETTINGS_PATH.parent, suffix=".tmp", delete=False) as output:
                temp_path = Path(output.name)
                if os.name != "nt":
                    os.chmod(temp_path, 0o600)
                json.dump(data, output, ensure_ascii=False, indent=2)
            os.replace(temp_path, SETTINGS_PATH)
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink()

def model_config():
    provider = os.getenv("MODEL_PROVIDER", "openai_compatible")
    result = {
        "provider": provider,
        "base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1") if provider == "ollama" else os.getenv("MODEL_BASE_URL", "https://api.openai.com/v1"),
        "key": os.getenv("MODEL_API_KEY", ""),
        "models": {name: os.getenv("MODEL_" + name.upper(), "") for name in ("fast", "balanced", "deep")},
        "vision_model": os.getenv("MODEL_VISION", ""),
        "reasoning_supported": os.getenv("MODEL_REASONING_SUPPORTED", "false").lower() == "true",
        "timeout": min(300, max(10, int(os.getenv("MODEL_TIMEOUT_SECONDS", "120")))),
        "iterations": min(20, max(1, int(os.getenv("MAX_AGENT_ITERATIONS", "8")))),
    }
    result.update(saved_model_settings())
    return result
