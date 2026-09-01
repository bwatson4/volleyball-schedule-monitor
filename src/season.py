"""Shared canonical season identity for schedule dates.

KVA's practical playing year is September through April, but persisted data
uses the complete Sep 1 through Aug 31 boundary so summer scheduling and
history remain unambiguous.
"""
from __future__ import annotations

from datetime import date, datetime


def season_for_date(value: date | datetime | str) -> str:
    """Return ``YYYY-YY`` for a date in the canonical Sep--Aug season."""
    if isinstance(value, str):
        value = date.fromisoformat(value[:10])
    if isinstance(value, datetime):
        value = value.date()
    start_year = value.year if value.month >= 9 else value.year - 1
    return f"{start_year}-{(start_year + 1) % 100:02d}"
