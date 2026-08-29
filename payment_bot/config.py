"""
Configuration module — loads all settings from environment variables.

Never hardcode secrets. All sensitive values come from .env or the environment.
"""

import os
import sys
import logging
from dotenv import load_dotenv

# Load .env file (no-op if it doesn't exist)
load_dotenv()

logger = logging.getLogger(__name__)


def _require_env(name: str) -> str:
    """Return an environment variable or exit with a clear error."""
    value = os.getenv(name, "").strip()
    if not value:
        logger.critical("Missing required environment variable: %s", name)
        sys.exit(1)
    return value


# ── Telegram ─────────────────────────────────────────────────────────────────
BOT_TOKEN: str = _require_env("BOT_TOKEN")
LOGS_ID: str = _require_env("LOGS_ID")        # Chat/channel ID for payment logs

# ── UPI ──────────────────────────────────────────────────────────────────────
UPI_ID: str = _require_env("UPI_ID")           # e.g. merchant@upi
UPI_NAME: str = _require_env("UPI_NAME")       # Display name on the QR

# ── Gmail (IMAP) ─────────────────────────────────────────────────────────────
GMAIL_EMAIL: str = _require_env("GMAIL_EMAIL")
GMAIL_APP_PASSWORD: str = _require_env("GMAIL_APP_PASSWORD")

# ── Limits ───────────────────────────────────────────────────────────────────
MIN_AMOUNT = 1           # INR
MAX_AMOUNT = 100_000     # INR
MAX_DECIMAL_PLACES = 2

# ── Deployment mode ──────────────────────────────────────────────────────────
LOCAL_MODE: bool = os.getenv("LOCAL_MODE", "false").strip().lower() in ("true", "1", "yes")
PORT: int = int(os.getenv("PORT", "10000"))
RENDER_EXTERNAL_URL: str = os.getenv("RENDER_EXTERNAL_URL", "").strip()

# ── Database ─────────────────────────────────────────────────────────────────
DB_PATH: str = os.getenv("DB_PATH", "payments.db")
