from __future__ import annotations

import os
from pathlib import Path

def load_env() -> None:
    """
    Load an optional local environment file into ``os.environ``.

    Systemd supplies protected values through ``EnvironmentFile``; local
    development may use ``.env`` (or ``ENV_FILE``).  A missing local file is
    therefore not an error--``config`` reports any required variables.
    """
    root = Path(__file__).resolve().parents[1]
    env_path = Path(os.environ.get("ENV_FILE", root / ".env"))

    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue

        key, value = s.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())
