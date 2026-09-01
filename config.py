"""Configuration read from the environment (never from source control)."""
from __future__ import annotations
import os
from pathlib import Path

def env_str(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not (value and value.strip()):
        raise RuntimeError(f"Missing required environment variable: {name}")
    return (value or "").strip()

def env_list(name: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]

BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = Path(env_str("RUNTIME_DIR", str(BASE_DIR / "runtime")))
STATE_FILE = RUNTIME_DIR / "schedule_state.json"
PDF_DIR = RUNTIME_DIR / "pdfs"
HISTORY_FILE = RUNTIME_DIR / "history.sqlite3"
PAGE_URL = env_str("PAGE_URL", "https://kvapack.ca/adult-indoor/")
KEYWORD = env_str("KEYWORD", "wednesday night")
PDF_MAX_BYTES = int(env_str("PDF_MAX_BYTES", "8388608"))
HTTP_TIMEOUT_SECONDS = float(env_str("HTTP_TIMEOUT_SECONDS", "20"))
SMTP_TIMEOUT_SECONDS = float(env_str("SMTP_TIMEOUT_SECONDS", "30"))
CALDAV_TIMEOUT_SECONDS = float(env_str("CALDAV_TIMEOUT_SECONDS", "45"))
CALENDAR_INDEX = int(env_str("CALENDAR_INDEX", "1"))
CALENDAR_NAME = env_str("CALENDAR_NAME", "")
ICLOUD_USERNAME = env_str("ICLOUD_USERNAME", required=True)
ICLOUD_APP_PASSWORD = env_str("ICLOUD_PASSWORD", required=True)
GMAIL_USERNAME = env_str("GMAIL_USERNAME", required=True)
GMAIL_APP_PASSWORD = env_str("GMAIL_APP_PASSWORD", required=True)
EMAIL_RECIPIENTS = env_list("EMAIL_RECIPIENTS")
if not EMAIL_RECIPIENTS: raise RuntimeError("EMAIL_RECIPIENTS must contain at least one address")
if any("@" not in address for address in EMAIL_RECIPIENTS): raise RuntimeError("EMAIL_RECIPIENTS contains an invalid address")
HEADERS = {"User-Agent": "volleyball-schedule-monitor/1.0"}
TIME_FORMAT = env_str("TIME_FORMAT", "12 Hour")
# Kept as a backwards-compatible default for callers that pass explicit pools.
# KVA headings are detected dynamically by ScheduleParser.
POOLS = [f"{letter} POOL" for letter in "ABCDEFGH"]
