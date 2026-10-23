"""Application settings. Secrets come from environment variables (.env file)."""
import os
import secrets
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")  # reads .env if it exists; does nothing otherwise


class Config:
    # If SECRET_KEY is not set, a random one is used. That is fine for a quick
    # local try-out, but everyone gets logged out whenever the app restarts.
    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

    DATABASE = os.environ.get("DATABASE_PATH") or str(BASE_DIR / "instance" / "jobtrack.db")

    # Session cookie protection
    SESSION_COOKIE_HTTPONLY = True      # JavaScript cannot read the cookie
    SESSION_COOKIE_SAMESITE = "Lax"     # extra protection against CSRF
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)

    MAX_CONTENT_LENGTH = 1024 * 1024    # reject request bodies larger than 1 MB
