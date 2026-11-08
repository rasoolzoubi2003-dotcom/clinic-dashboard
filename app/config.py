"""Application settings loaded from environment variables (and an optional .env file)."""

import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        logger.warning("Invalid %s, using %s", name, default)
        return default


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./clinic.db")
CLINIC_TIMEZONE = os.getenv("CLINIC_TIMEZONE", "Asia/Amman")

# --- AI assistant -----------------------------------------------------------
# Local-first: Ollama is the default and needs no API key or internet connection.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
# Optional second provider, used only when the first one fails. Off when empty.
LLM_FALLBACK_PROVIDER = os.getenv("LLM_FALLBACK_PROVIDER", "").strip().lower()

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
# Low temperature makes small models more consistent at tool calling. Kept within 0-0.2.
OLLAMA_TEMPERATURE = min(max(_float("OLLAMA_TEMPERATURE", 0.1), 0.0), 0.2)
# The first request loads the model into memory, which can take a while on CPU.
OLLAMA_TIMEOUT = _float("OLLAMA_TIMEOUT", 120.0)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5")
