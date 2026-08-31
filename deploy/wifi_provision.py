#!/usr/bin/env python3
"""Systemd entry point kept outside the application scanner."""
from src.wifi_provision import run_service

if __name__ == "__main__":
    run_service()
