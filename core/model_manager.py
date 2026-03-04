"""LLM integration - Gemini and Ollama support (based on S18Share)."""

import json
import os
import time
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODELS_JSON = PROJECT_ROOT / "config" / "models.json"


def _load_models_config() -> dict:
    """Load models.json from config/."""
    if MODELS_JSON.exists():
        return json.loads(MODELS_JSON.read_text())
    return {
        "defaults": {"text_generation": "gemini"},
        "models": {
            "gemini": {
                "type": "gemini",
                "model": "gemini-2.5-flash",
                "api_key_env": "GEMINI_API_KEY",
            },
            "phi4": {
                "type": "ollama",
                "model": "phi4",
                "url": {"generate": "http://127.0.0.1:11434/api/generate"},
            },
        },
    }


def _get_settings() -> dict:
    """Load settings from env or config."""
    try:
        settings_path = PROJECT_ROOT / "config" / "settings.json"
        if settings_path.exists():
            return json.loads(settings_path.read_text())
    except Exception:
        pass
    provider = os.getenv("PLAINSPEAK_MODEL_PROVIDER")
    model = os.getenv("PLAINSPEAK_MODEL")
    return {"agent": {"model_provider": provider, "default_model": model}}


class ModelManager:
    """
    Sync-first ModelManager for Gemini and Ollama.
    Based on S18Share core/model_manager.py.
    """

    _last_call = 0

    def __init__(self, model_name: str | None = None, provider: str | None = None):
        """
        Initialize ModelManager.

        Args:
            model_name: Model to use (e.g. "gemini-2.5-flash", "phi4")
            provider: "gemini" or "ollama". If None, uses settings/env.
        """
        self.config = _load_models_config()
        settings = _get_settings()
        agent_cfg = settings.get("agent", {})

        models_cfg = self.config.get("models", {})
        defaults = self.config.get("defaults", {})
        default_key = defaults.get("text_generation", "gemini")

        # Resolve model key: explicit args > settings/env > defaults.text_generation
        model_key = model_name or agent_cfg.get("default_model") or provider or agent_cfg.get("model_provider") or default_key

        # Look up in config; skip embedding-only models (e.g. nomic)
        model_entry = models_cfg.get(model_key, {})
        if not model_entry or model_entry.get("type") == "huggingface":
            model_key = default_key
            model_entry = models_cfg.get(model_key, {})

        self.model_type = model_entry.get("type", "gemini")
        self.model_name = model_entry.get("model", model_key)

        if self.model_type == "gemini":
            api_key_env = model_entry.get("api_key_env", "GEMINI_API_KEY")
            api_key = os.getenv(api_key_env)
            if not api_key:
                raise ValueError(
                    "GEMINI_API_KEY not set. Set it in env or .env for Gemini models."
                )
            from google import genai
            self.client = genai.Client(api_key=api_key)
            self.model_info = {"type": "gemini", "model": self.model_name}

        elif self.model_type == "ollama":
            self.client = None
            url_cfg = model_entry.get("url", {})
            generate_url = url_cfg.get("generate")
            if not generate_url:
                base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
                generate_url = f"{base}/api/generate"
            self.model_info = {
                "type": "ollama",
                "model": self.model_name,
                "url": {"generate": generate_url},
            }
        else:
            raise ValueError(f"Unknown provider: {self.model_type}")

    def _wait_for_rate_limit(self) -> None:
        """Enforce ~15 RPM limit for Gemini."""
        if self.model_type != "gemini":
            return
        now = time.time()
        elapsed = now - ModelManager._last_call
        if elapsed < 4.5:
            import time as t
            t.sleep(4.5 - elapsed)
        ModelManager._last_call = time.time()

    def generate_text(self, prompt: str) -> str:
        """Generate text from prompt. Sync API."""
        if self.model_type == "gemini":
            self._wait_for_rate_limit()
            response = self.client.models.generate_content(
                model=self.model_info["model"],
                contents=prompt,
            )
            return response.text.strip()

        elif self.model_type == "ollama":
            import urllib.request
            req = urllib.request.Request(
                self.model_info["url"]["generate"],
                data=json.dumps(
                    {"model": self.model_info["model"], "prompt": prompt, "stream": False}
                ).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.load(resp)
            return result.get("response", "").strip()

        raise NotImplementedError(f"Unsupported model type: {self.model_type}")
