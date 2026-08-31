# tests/conftest.py
import os
import pytest

# Configuration is intentionally fail-fast at import time; provide hermetic defaults
# before individual test modules import application code.
os.environ.setdefault("ICLOUD_USERNAME", "test@example.com")
os.environ.setdefault("ICLOUD_PASSWORD", "test-password")
os.environ.setdefault("GMAIL_USERNAME", "test@gmail.com")
os.environ.setdefault("GMAIL_APP_PASSWORD", "test-app-password")
os.environ.setdefault("EMAIL_RECIPIENTS", "recipient@example.com")
os.environ.setdefault("PAGE_URL", "https://example.com")
os.environ.setdefault("KEYWORD", "Volleyball")

@pytest.fixture(scope="session", autouse=True)
def _set_test_env():
    # Dummy but valid values
    os.environ.setdefault("ICLOUD_USERNAME", "test@example.com")
    os.environ.setdefault("ICLOUD_PASSWORD", "test-password")
    os.environ.setdefault("GMAIL_USERNAME", "test@gmail.com")
    os.environ.setdefault("GMAIL_APP_PASSWORD", "test-app-password")
    os.environ.setdefault("EMAIL_RECIPIENTS", "recipient@example.com")

    # Any other required env your config expects:
    os.environ.setdefault("PAGE_URL", "https://example.com")
    os.environ.setdefault("KEYWORD", "Volleyball")
