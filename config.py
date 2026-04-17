"""
config.py
---------
Loads and validates all environment variables required by the system.
Every other module imports from this file — no module reads os.environ directly.

Required .env keys:
  OLLAMA_MODEL, OLLAMA_BASE_URL (optional, defaults to localhost)
  SMTP_EMAIL, SMTP_PASSWORD, NOTIFY_EMAIL
  SQLITE_DB_PATH (optional, default: ./data/books.db)
  OUTPUT_DIR    (optional, default: ./outputs)
"""

import os
import logging
from dotenv import load_dotenv

log = logging.getLogger(__name__)

# Load .env from the directory where config.py exists
env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=env_path)


def _require(key: str) -> str:
    """
    Fetch a required environment variable.

    Args:
        key: The environment variable name.

    Returns:
        The string value of the variable.

    Raises:
        EnvironmentError: If the variable is not set or is empty.
    """
    value = os.environ.get(key, "").strip()
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{key}' is not set. "
            "Check your .env file or shell environment."
        )
    return value


def _optional(key: str, default: str = "") -> str:
    """
    Fetch an optional environment variable with a fallback default.

    Args:
        key:     The environment variable name.
        default: Value to return when the variable is missing.

    Returns:
        The string value or the supplied default.
    """
    return os.environ.get(key, default).strip()


# ── Ollama (local LLM) ────────────────────────────────────────────────────────

OLLAMA_MODEL: str    = _optional("OLLAMA_MODEL", "qwen2.5-coder:3b")
OLLAMA_BASE_URL: str = _optional("OLLAMA_BASE_URL", "http://localhost:11434")

# ── SQLite (local database) ───────────────────────────────────────────────────

SQLITE_DB_PATH: str = _optional("SQLITE_DB_PATH", "./data/books.db")

# ── Local Output Directory ────────────────────────────────────────────────────

OUTPUT_DIR: str = _optional("OUTPUT_DIR", "./outputs")

# ── SMTP / Email (Gmail STARTTLS) ─────────────────────────────────────────────

# Hard-coded for Gmail per spec — no SMTP_HOST or SMTP_PORT env vars needed.
SMTP_HOST: str     = "smtp.gmail.com"
SMTP_PORT: int     = 587
SMTP_USER: str     = _require("SMTP_EMAIL")       # sender Gmail address
SMTP_PASSWORD: str = _require("SMTP_PASSWORD")    # Gmail App Password (16-char)
NOTIFY_EMAIL: str  = _require("NOTIFY_EMAIL")

# ── Optional tunables ─────────────────────────────────────────────────────────

LOG_LEVEL: str = _optional("LOG_LEVEL", "INFO").upper()

log.debug("Configuration loaded successfully.")
