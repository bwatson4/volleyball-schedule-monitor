"""Central league pool ordering helpers."""
from __future__ import annotations

import re

POOL_LETTERS = "ABCDEFGH"
_POOL_RE = re.compile(r"^\s*([A-H])(?:\s+POOL)?\s*$", re.IGNORECASE)


def pool_letter(value: str | None) -> str | None:
    """Return the supported pool letter, or ``None`` for an invalid heading."""
    match = _POOL_RE.match(str(value or ""))
    return match.group(1).upper() if match else None


def pool_rank(value: str | None) -> int | None:
    """Return a zero-based rank where A is highest, without UI-specific logic."""
    letter = pool_letter(value)
    return POOL_LETTERS.index(letter) if letter else None
