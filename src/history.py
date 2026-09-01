"""Small, dependency-free schedule history database.

History is an observability feature, not part of the crash-recovery contract.
Every write is transactional and callers deliberately treat failures as
non-fatal so a full disk cannot strand a schedule candidate mid-processing.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


class HistoryStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(str(self.path))
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self):
        with self._connect() as db:
            db.executescript("""
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS schedule_revision (
                    content_hash TEXT PRIMARY KEY,
                    detected_at TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    parsed_at TEXT,
                    calendar_at TEXT,
                    email_at TEXT,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS parsed_game (
                    content_hash TEXT NOT NULL REFERENCES schedule_revision(content_hash) ON DELETE CASCADE,
                    logical_id TEXT NOT NULL,
                    source_team TEXT NOT NULL,
                    game_date TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    gym TEXT,
                    pool TEXT,
                    pool_position TEXT,
                    summary TEXT NOT NULL,
                    PRIMARY KEY (content_hash, logical_id)
                );
            """)

    def detect(self, content_hash: str, detected_at: str, source_url: str) -> None:
        with self._connect() as db:
            db.execute("""INSERT INTO schedule_revision(content_hash, detected_at, source_url)
                          VALUES (?, ?, ?) ON CONFLICT(content_hash) DO NOTHING""",
                       (content_hash, detected_at, source_url))

    def record_events(self, content_hash: str, events: list[dict], parsed_at: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE schedule_revision SET parsed_at=COALESCE(parsed_at, ?) WHERE content_hash=?",
                       (parsed_at, content_hash))
            db.execute("DELETE FROM parsed_game WHERE content_hash=?", (content_hash,))
            db.executemany("""INSERT INTO parsed_game
                (content_hash, logical_id, source_team, game_date, start_time, end_time, gym, pool, pool_position, summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", [
                (content_hash, event["uid"], event["source_team"], event["date"], event["start"].isoformat(),
                 event["end"].isoformat(), event.get("gym"), event.get("pool"),
                 event.get("pool_position"), event["summary"])
                for event in events
            ])

    def record_stage(self, content_hash: str, stage: str, at: str) -> None:
        column = {"calendar": "calendar_at", "email": "email_at", "completed": "completed_at"}[stage]
        with self._connect() as db:
            db.execute(f"UPDATE schedule_revision SET {column}=COALESCE({column}, ?) WHERE content_hash=?", (at, content_hash))

    def dashboard(self) -> dict:
        """Return lightweight data for the LAN UI without leaking hashes."""
        with self._connect() as db:
            revisions = [dict(row) for row in db.execute("SELECT * FROM schedule_revision ORDER BY detected_at DESC")]
            games = [dict(row) for row in db.execute("""SELECT g.*, r.detected_at FROM parsed_game g
                JOIN schedule_revision r ON r.content_hash=g.content_hash
                ORDER BY game_date, start_time""")]
        return {"revisions": revisions, "games": games}
