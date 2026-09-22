"""Small, dependency-free schedule history database.

History is an observability feature, not part of the crash-recovery contract.
Every write is transactional and callers deliberately treat failures as
non-fatal so a full disk cannot strand a schedule candidate mid-processing.
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from src.season import season_for_date
from src.team_identity import TeamIdentityResolver, normalize_team
from src.pools import pool_letter, pool_rank

LOG = logging.getLogger("schedule_monitor.history")


class HistoryStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(str(self.path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
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
                    season TEXT,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    gym TEXT,
                    pool TEXT,
                    pool_position TEXT,
                    rotation_matrix TEXT,
                    summary TEXT NOT NULL,
                    PRIMARY KEY (content_hash, logical_id)
                );
                CREATE TABLE IF NOT EXISTS parsed_game_team (
                    content_hash TEXT NOT NULL,
                    logical_id TEXT NOT NULL,
                    team_normalized TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    pool_position TEXT,
                    PRIMARY KEY (content_hash, logical_id, team_normalized),
                    FOREIGN KEY (content_hash, logical_id)
                        REFERENCES parsed_game(content_hash, logical_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS parsed_game_team_identity_idx
                    ON parsed_game_team(team_normalized);
                CREATE TABLE IF NOT EXISTS team_identity (
                    season TEXT NOT NULL,
                    canonical_normalized TEXT NOT NULL,
                    canonical_name TEXT NOT NULL,
                    PRIMARY KEY (season, canonical_normalized)
                );
                CREATE TABLE IF NOT EXISTS team_alias (
                    season TEXT NOT NULL,
                    alias_normalized TEXT NOT NULL,
                    canonical_normalized TEXT NOT NULL,
                    PRIMARY KEY (season, alias_normalized),
                    FOREIGN KEY (season, canonical_normalized)
                        REFERENCES team_identity(season, canonical_normalized) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS team_identity_candidate (
                    season TEXT NOT NULL,
                    alias_normalized TEXT NOT NULL,
                    candidate_normalized TEXT NOT NULL,
                    score REAL NOT NULL,
                    PRIMARY KEY (season, alias_normalized, candidate_normalized)
                );
                CREATE INDEX IF NOT EXISTS team_alias_identity_idx ON team_alias(season, canonical_normalized);
                CREATE TABLE IF NOT EXISTS parsed_pool_team (
                    content_hash TEXT NOT NULL REFERENCES schedule_revision(content_hash) ON DELETE CASCADE,
                    game_date TEXT NOT NULL,
                    season TEXT NOT NULL,
                    pool TEXT NOT NULL,
                    team_normalized TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    pool_position TEXT,
                    PRIMARY KEY (content_hash, game_date, pool, team_normalized)
                );
                CREATE INDEX IF NOT EXISTS parsed_pool_team_week_idx ON parsed_pool_team(season, game_date, content_hash);
            """)
            # ``CREATE TABLE IF NOT EXISTS`` cannot add a column to a deployed
            # database.  Add and backfill season identity exactly once while
            # retaining every auditable PDF revision.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(parsed_game)")}
            if "season" not in columns:
                db.execute("ALTER TABLE parsed_game ADD COLUMN season TEXT")
            if "rotation_matrix" not in columns:
                db.execute("ALTER TABLE parsed_game ADD COLUMN rotation_matrix TEXT")
            team_columns = {row["name"] for row in db.execute("PRAGMA table_info(parsed_game_team)")}
            if "pool_position" not in team_columns:
                db.execute("ALTER TABLE parsed_game_team ADD COLUMN pool_position TEXT")
            missing = list(db.execute("SELECT rowid, game_date FROM parsed_game WHERE season IS NULL OR season=''"))
            db.executemany("UPDATE parsed_game SET season=? WHERE rowid=?", [
                (season_for_date(row["game_date"]), row["rowid"]) for row in missing
            ])
            db.execute("CREATE INDEX IF NOT EXISTS parsed_game_season_idx ON parsed_game(season)")

    def detect(self, content_hash: str, detected_at: str, source_url: str) -> None:
        with self._connect() as db:
            db.execute("""INSERT INTO schedule_revision(content_hash, detected_at, source_url)
                          VALUES (?, ?, ?) ON CONFLICT(content_hash) DO NOTHING""",
                       (content_hash, detected_at, source_url))

    def record_events(self, content_hash: str, events: list[dict], parsed_at: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE schedule_revision SET parsed_at=COALESCE(parsed_at, ?) WHERE content_hash=?",
                       (parsed_at, content_hash))
            # Explicitly remove associations as well as their parent rows.  This
            # keeps a retry of the same parsed revision safe on legacy SQLite
            # connections and makes the all-or-nothing replacement obvious.
            db.execute("DELETE FROM parsed_game_team WHERE content_hash=?", (content_hash,))
            db.execute("DELETE FROM parsed_pool_team WHERE content_hash=?", (content_hash,))
            db.execute("DELETE FROM parsed_game WHERE content_hash=?", (content_hash,))
            db.executemany("""INSERT INTO parsed_game
                (content_hash, logical_id, source_team, game_date, season, start_time, end_time, gym, pool, pool_position, rotation_matrix, summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", [
                (content_hash, event["uid"], event["source_team"], event["date"], season_for_date(event["date"]), event["start"].isoformat(),
                 event["end"].isoformat(), event.get("gym"), event.get("pool"),
                 event.get("pool_position"), json.dumps(event.get("rotation_matrix") or []), event["summary"])
                for event in events
            ])
            resolver = TeamIdentityResolver(db)
            persisted, roster_rows = [], []
            for event in events:
                resolver.resolve(season_for_date(event["date"]), event["source_team"])
                seen = set()
                for _normalized, display, position in self._event_teams(event):
                    decision = resolver.resolve(season_for_date(event["date"]), display)
                    if decision.canonical_normalized in seen:
                        continue
                    seen.add(decision.canonical_normalized)
                    if decision.action == "learned":
                        LOG.info('Team alias learned: %r -> %r [season %s, score=%.1f]', display, decision.canonical_name, event["season"] if "season" in event else season_for_date(event["date"]), decision.score)
                    elif decision.action == "uncertain" and resolver.last_uncertain_new:
                        LOG.info('Team alias not learned: %r ~ %r [season %s, score=%.1f]', display, decision.candidate, season_for_date(event["date"]), decision.score)
                    persisted.append((content_hash, event["uid"], decision.canonical_normalized, display, position))
                for pool, teams in self._event_rosters(event):
                    for _normalized, display, position in teams:
                        decision = resolver.resolve(season_for_date(event["date"]), display)
                        roster_rows.append((content_hash, event["date"], season_for_date(event["date"]), pool,
                                            decision.canonical_normalized, display, position))
            db.executemany("""INSERT INTO parsed_game_team
                (content_hash, logical_id, team_normalized, display_name, pool_position)
                VALUES (?, ?, ?, ?, ?)""", persisted)
            db.executemany("""INSERT OR REPLACE INTO parsed_pool_team
                (content_hash, game_date, season, pool, team_normalized, display_name, pool_position)
                VALUES (?, ?, ?, ?, ?, ?, ?)""", roster_rows)

    @staticmethod
    def _event_teams(event: dict) -> list[tuple[str, str, str | None]]:
        """Accept structured parser data while reading pre-feature callers too."""
        result: list[tuple[str, str, str | None]] = []
        seen: set[str] = set()
        for team in event.get("pool_teams", []):
            if isinstance(team, dict):
                display = str(team.get("name") or team.get("display_name") or "")
                normalized = normalize_team(team.get("normalized_name") or team.get("team_normalized") or display)
                position = team.get("position")
            else:
                display, normalized = str(team), normalize_team(team)
                position = None
            if normalized and display and normalized not in seen:
                seen.add(normalized)
                result.append((normalized, display, str(position) if position is not None else None))
        return result

    @classmethod
    def _event_rosters(cls, event: dict) -> list[tuple[str, list[tuple[str, str, str | None]]]]:
        """Read complete parser rosters, with a useful fallback for old data."""
        rosters = []
        for item in event.get("league_pools", []):
            pool = item.get("pool") if isinstance(item, dict) else None
            if pool:
                rosters.append((str(pool), cls._event_teams({"pool_teams": item.get("teams", [])})))
        if rosters:
            return rosters
        source = (normalize_team(event.get("source_team")), str(event.get("source_team") or ""), event.get("pool_position"))
        return [(str(event.get("pool")), [source, *cls._event_teams(event)])] if event.get("pool") else []

    def record_stage(self, content_hash: str, stage: str, at: str) -> None:
        column = {"calendar": "calendar_at", "email": "email_at", "completed": "completed_at"}[stage]
        with self._connect() as db:
            db.execute(f"UPDATE schedule_revision SET {column}=COALESCE({column}, ?) WHERE content_hash=?", (at, content_hash))

    def dashboard(self, user_team_names: list[str] | None = None) -> dict:
        """Return explicit current, deduplicated and historical UI datasets.

        ``current_games`` comes only from the most recently detected revision
        that parsed successfully.  ``analytics_games`` is one latest
        observation per logical session across all successful revisions.
        ``pool_observations`` uses the same one-point-per-session dataset for
        Pool Movement, while ``games`` retains every parsed revision row.
        This makes semantics a data-layer contract rather than a UI accident.
        """
        with self._connect() as db:
            resolver = TeamIdentityResolver(db, persist=False)
            revisions = [dict(row) for row in db.execute("SELECT * FROM schedule_revision ORDER BY detected_at DESC")]
            games = [dict(row) for row in db.execute("""SELECT g.*, r.detected_at FROM parsed_game g
                JOIN schedule_revision r ON r.content_hash=g.content_hash
                ORDER BY game_date, start_time""")]
            latest = db.execute("""SELECT content_hash FROM schedule_revision
                WHERE parsed_at IS NOT NULL ORDER BY detected_at DESC LIMIT 1""").fetchone()
            current_games = [] if latest is None else [dict(row) for row in db.execute("""SELECT g.*, r.detected_at
                FROM parsed_game g JOIN schedule_revision r ON r.content_hash=g.content_hash
                WHERE g.content_hash=? ORDER BY game_date, start_time""", (latest["content_hash"],))]
            analytics_games = [dict(row) for row in db.execute("""SELECT * FROM (
                SELECT g.*, r.detected_at,
                  ROW_NUMBER() OVER (PARTITION BY g.logical_id ORDER BY r.detected_at DESC) AS observation_rank
                FROM parsed_game g JOIN schedule_revision r ON r.content_hash=g.content_hash
                WHERE r.parsed_at IS NOT NULL
            ) WHERE observation_rank=1 ORDER BY game_date, start_time""")]
            for collection in (games, current_games, analytics_games):
                for game in collection:
                    game["rotation_matrix"] = json.loads(game.pop("rotation_matrix") or "[]")
                    game["pool_teams"] = [dict(row) for row in db.execute("""
                        SELECT team_normalized, display_name, pool_position FROM parsed_game_team
                        WHERE content_hash=? AND logical_id=?
                        ORDER BY CAST(pool_position AS INTEGER), display_name COLLATE NOCASE
                    """, (game["content_hash"], game["logical_id"]))]
                    for team in game["pool_teams"]:
                        # ``display_name`` remains the raw PDF value in SQLite;
                        # the dashboard substitutes its stable canonical display
                        # name while aggregating by season identity.
                        raw_name = team["display_name"]
                        decision = resolver.resolve(game["season"], raw_name)
                        team["team_normalized"] = decision.canonical_normalized
                        team["display_name"] = decision.canonical_name
                        if team["pool_position"] is None:
                            team.pop("pool_position")
                    source = resolver.resolve(game["season"], game["source_team"])
                    game["source_team_normalized"] = source.canonical_normalized
                    game["source_team_canonical_name"] = source.canonical_name
            known_seasons = {game["season"] for game in [*current_games, *analytics_games]}
            user_identities = {resolver.resolve(season, name).canonical_normalized
                               for season in known_seasons for name in (user_team_names or []) if name}
            roster_rows = [dict(row) for row in db.execute("""SELECT p.* FROM parsed_pool_team p
                JOIN (SELECT season, game_date, content_hash FROM (
                    SELECT p.season, p.game_date, p.content_hash, r.detected_at,
                    ROW_NUMBER() OVER (PARTITION BY p.season, p.game_date ORDER BY r.detected_at DESC) AS revision_rank
                    FROM parsed_pool_team p JOIN schedule_revision r ON r.content_hash=p.content_hash
                    WHERE r.parsed_at IS NOT NULL
                ) WHERE revision_rank=1) latest
                ON latest.season=p.season AND latest.game_date=p.game_date AND latest.content_hash=p.content_hash
                ORDER BY p.season, p.game_date, p.pool, CAST(p.pool_position AS INTEGER), p.display_name COLLATE NOCASE""")]
            weekly_rosters: dict[tuple[str, str], dict[str, list[dict]]] = {}
            for row in roster_rows:
                decision = resolver.resolve(row["season"], row["display_name"])
                row["team_normalized"], row["display_name"] = decision.canonical_normalized, decision.canonical_name
                weekly_rosters.setdefault((row["season"], row["game_date"]), {}).setdefault(row["pool"], []).append(row)

        self._add_team_statistics(analytics_games)
        analytics_by_id = {game["logical_id"]: game for game in analytics_games}
        for game in current_games:
            latest = analytics_by_id.get(game["logical_id"])
            if latest:
                game["pool_teams"] = latest["pool_teams"]
        # Databases created before complete-league roster capture still have a
        # selected-pool snapshot per historical week.  Use it as a partial
        # fallback so comparisons remain honest: incoming teams not present in
        # that snapshot become missing_previous_week diagnostics, never "new".
        for game in analytics_games:
            key, pool = (game["season"], game["game_date"]), game.get("pool")
            if pool and pool not in weekly_rosters.get(key, {}):
                weekly_rosters.setdefault(key, {})[pool] = [
                    {"team_normalized": game["source_team_normalized"], "display_name": game["source_team_canonical_name"]},
                    *game.get("pool_teams", []),
                ]
        team_history = self._team_history(analytics_games)
        all_time_by_identity = {row["team_normalized"]: row["weeks_together"] for row in team_history}
        seasons = sorted({game["season"] for game in analytics_games}, reverse=True)
        current_season = season_for_date(date.today())
        if current_season not in seasons:
            seasons.insert(0, current_season)
        team_history_by_season = {}
        for season in seasons:
            records = self._team_history([game for game in analytics_games if game["season"] == season])
            for record in records:
                record["all_time_meetings"] = all_time_by_identity[record["team_normalized"]]
            team_history_by_season[season] = records
        pool_movement = self._current_pool_movement(current_games, weekly_rosters, user_identities)
        return {"revisions": revisions, "games": games, "current_games": current_games,
                "analytics_games": analytics_games, "pool_observations": analytics_games,
                "team_history": team_history, "team_history_by_season": team_history_by_season,
                "seasons": seasons, "current_season": current_season, "pool_movement": pool_movement}

    @staticmethod
    def _current_pool_movement(current_games, weekly_rosters, user_identities):
        """Derive a current-pool comparison from consecutive weekly rosters."""
        if not current_games:
            return {"status": "unavailable", "movements": [], "validation": {"valid": True, "warnings": [], "errors": []}}
        current = max(current_games, key=lambda row: (row["game_date"], row.get("start_time", ""), row["logical_id"]))
        season, current_date, current_pool = current["season"], current["game_date"], current.get("pool")
        current_letter = pool_letter(current_pool)
        current_roster = weekly_rosters.get((season, current_date), {}).get(current_pool, [])
        if not current_roster:
            current_roster = [{"team_normalized": current["source_team_normalized"], "display_name": current["source_team_canonical_name"]}, *current.get("pool_teams", [])]
        dates = sorted(day for (row_season, day) in weekly_rosters if row_season == season and day < current_date)
        previous_date = dates[-1] if dates else None
        previous_rosters = weekly_rosters.get((season, previous_date), {}) if previous_date else {}
        warnings, errors, movements = [], [], []
        previous_by_identity = {}
        for pool, teams in previous_rosters.items():
            for team in teams:
                identity = team["team_normalized"]
                if identity in previous_by_identity:
                    errors.append(f"duplicate previous canonical identity {identity}")
                previous_by_identity[identity] = pool
        seen = set()
        for team in current_roster:
            identity, name = team["team_normalized"], team["display_name"]
            if identity in seen:
                errors.append(f"duplicate current canonical identity {identity}")
                continue
            seen.add(identity)
            item = {"canonical_team_id": identity, "canonical_name": name, "current_pool": current_letter or current_pool,
                    "current_date": current_date, "previous_date": previous_date, "is_user_team": identity in user_identities}
            if not previous_date:
                item.update({"status": "season_start", "direction": "season_start", "delta": 0, "previous_pool": None})
            else:
                previous_pool = previous_by_identity.get(identity)
                previous_letter, previous_rank, current_rank = pool_letter(previous_pool), pool_rank(previous_pool), pool_rank(current_pool)
                item["previous_pool"] = previous_letter or previous_pool
                if previous_pool is None:
                    item.update({"status": "missing_previous_week", "direction": "missing_previous_week", "delta": None})
                    warnings.append(f"missing_previous_week team={name!r} canonical={identity!r}")
                    LOG.warning("Pool movement unresolved: season=%s week=%s team=%r canonical=%r reason=missing_previous_week", season, current_date, name, identity)
                elif previous_rank is None or current_rank is None:
                    item.update({"status": "unsupported_pool", "direction": "data_issue", "delta": None})
                    warnings.append(f"unsupported pool for {name!r}: {previous_pool!r} -> {current_pool!r}")
                else:
                    delta = previous_rank - current_rank
                    direction = "up" if delta > 0 else "down" if delta < 0 else "same"
                    item.update({"status": "ok", "direction": direction, "delta": abs(delta)})
                    if abs(delta) > 1:
                        warnings.append(f"multi-pool movement team={name!r} delta={abs(delta)}")
            movements.append(item)
        if previous_date and current_letter:
            counts = Counter(item["direction"] for item in movements)
            expected_up, expected_down = (1, 0) if current_letter == "A" else (0, 1) if current_letter == "H" else (1, 1)
            if counts["up"] != expected_up or counts["down"] != expected_down:
                warnings.append(f"pool={current_letter} up={counts['up']} down={counts['down']} same={counts['same']} missing={counts['missing_previous_week']} expected_up={expected_up} expected_down={expected_down}")
        return {"status": "season_start" if not previous_date else "ok", "season": season, "week": current_date,
                "pool": current_letter or current_pool, "previous_week": previous_date, "movements": movements,
                "validation": {"valid": not warnings and not errors, "warnings": warnings, "errors": errors}}

    def repair_team_identities(self, season: str | None = None, dry_run: bool = False) -> dict:
        """Learn safe aliases from retained latest schedule observations.

        This never rewrites PDF revision rows or touches notification stages;
        only identity metadata is added, making the operation repeatable.
        """
        with self._connect() as db:
            resolver = TeamIdentityResolver(db, persist=not dry_run)
            season_filter, params = ("AND g.season=?", (season,)) if season else ("", ())
            rows = list(db.execute(f"""SELECT game_date, g.season, t.display_name
                FROM (
                    SELECT g.*, r.detected_at,
                    ROW_NUMBER() OVER (PARTITION BY g.logical_id ORDER BY r.detected_at DESC) observation_rank
                    FROM parsed_game g JOIN schedule_revision r ON r.content_hash=g.content_hash
                    WHERE r.parsed_at IS NOT NULL
                ) g JOIN parsed_game_team t ON t.content_hash=g.content_hash AND t.logical_id=g.logical_id
                WHERE observation_rank=1 {season_filter}
                ORDER BY game_date, g.start_time, g.logical_id, t.display_name COLLATE NOCASE""", params))
            before = len({(row["season"], normalize_team(row["display_name"])) for row in rows})
            learned, uncertain, created = [], [], []
            resolved = set()
            for row in rows:
                decision = resolver.resolve(row["season"], row["display_name"])
                resolved.add((row["season"], decision.canonical_normalized))
                if decision.action == "learned":
                    learned.append(decision)
                elif decision.action == "uncertain":
                    uncertain.append(decision)
                elif decision.action == "new":
                    created.append(decision)
            after = len(resolved)
            return {"season": season, "dry_run": dry_run, "observations": len(rows), "before": before,
                    "after": after, "learned": learned, "uncertain": uncertain, "created": created}

    def backfill_pool_rosters(self, season: str, dry_run: bool = False, pdf_dir: Path | None = None) -> dict:
        """Safely fill missing full rosters from retained, immutable PDFs.

        This deliberately bypasses ``record_events``: it neither creates a
        revision nor touches calendar/email/completion timestamps.
        """
        pdf_dir = Path(pdf_dir) if pdf_dir else self.path.parent / "pdfs"
        result = {"season": season, "dry_run": dry_run, "weeks": [], "inspected": 0,
                  "requiring_backfill": 0, "rows_to_add": 0, "rows_added": 0, "failed": []}
        with self._connect() as db:
            rows = list(db.execute("""SELECT content_hash, game_date FROM (
                SELECT g.content_hash, g.game_date, g.logical_id, r.detected_at,
                    ROW_NUMBER() OVER (PARTITION BY g.logical_id ORDER BY r.detected_at DESC) AS revision_rank
                FROM parsed_game g JOIN schedule_revision r ON r.content_hash=g.content_hash
                WHERE g.season=? AND r.parsed_at IS NOT NULL
            ) WHERE revision_rank=1 GROUP BY content_hash, game_date ORDER BY game_date, content_hash""", (season,)))
            resolver = TeamIdentityResolver(db, persist=not dry_run)
            for revision in rows:
                content_hash, game_date = revision["content_hash"], revision["game_date"]
                result["inspected"] += 1
                existing = list(db.execute("SELECT * FROM parsed_pool_team WHERE content_hash=? AND game_date=?", (content_hash, game_date)))
                report = {"week": game_date, "content_hash": content_hash, "existing_rows": len(existing), "reconstructed_rows": 0, "would_add": 0}
                try:
                    reconstructed = self._reconstruct_pool_rosters(db, resolver, content_hash, game_date, pdf_dir)
                    errors = self._validate_reconstructed_rosters(db, content_hash, game_date, reconstructed)
                    if errors:
                        raise ValueError("; ".join(errors))
                except Exception as exc:
                    report["status"], report["error"] = "failed", str(exc)
                    result["failed"].append(report)
                    result["weeks"].append(report)
                    LOG.warning("Pool roster backfill failed: season=%s week=%s hash=%s reason=%s", season, game_date, content_hash, exc)
                    continue
                existing_keys = {(row["pool"], row["team_normalized"]) for row in existing}
                additions = [row for row in reconstructed if (row[3], row[4]) not in existing_keys]
                report.update({"reconstructed_rows": len(reconstructed), "would_add": len(additions),
                               "status": "complete" if not additions else "backfill"})
                result["rows_to_add"] += len(additions)
                if additions:
                    result["requiring_backfill"] += 1
                    if not dry_run:
                        db.executemany("""INSERT OR IGNORE INTO parsed_pool_team
                            (content_hash, game_date, season, pool, team_normalized, display_name, pool_position)
                            VALUES (?, ?, ?, ?, ?, ?, ?)""", additions)
                        result["rows_added"] += len(additions)
                result["weeks"].append(report)
        return result

    @staticmethod
    def _reconstruct_pool_rosters(db, resolver, content_hash, game_date, pdf_dir):
        pdf_path = Path(pdf_dir) / f"{content_hash}.pdf"
        if not pdf_path.is_file():
            raise FileNotFoundError(f"retained PDF not found: {pdf_path}")
        source = list(db.execute("SELECT source_team, gym FROM parsed_game WHERE content_hash=? AND game_date=?", (content_hash, game_date)))
        if not source:
            raise ValueError("no parsed game metadata for revision")
        from pdfminer.high_level import extract_text
        from src.parser import ScheduleParser
        text = extract_text(str(pdf_path)).replace("\f", "\n")
        configured_gyms = [row["gym"] for row in source if row["gym"]]
        settings_path = Path(pdf_dir).parent / "settings.json"
        try:
            configured_gyms.extend(json.loads(settings_path.read_text(encoding="utf-8")).get("gyms", []))
        except (OSError, ValueError, AttributeError):
            pass
        events = ScheduleParser(text, team_names=list({row["source_team"] for row in source}),
                               gyms=list(dict.fromkeys(configured_gyms))).parse()
        rosters = [item for event in events for item in event.get("league_pools", [])]
        if not rosters:
            raise ValueError("PDF parser could not reconstruct a complete league roster")
        canonical = {}
        season = season_for_date(game_date)
        for roster in rosters:
            pool = str(roster.get("pool") or "")
            for team in roster.get("teams", []):
                display = str(team.get("name") or team.get("display_name") or "")
                decision = resolver.resolve(season, display)
                canonical[(pool, decision.canonical_normalized)] = (
                    content_hash, game_date, season, pool, decision.canonical_normalized, display,
                    str(team.get("position")) if team.get("position") is not None else None,
                )
        return list(canonical.values())

    @staticmethod
    def _validate_reconstructed_rosters(db, content_hash, game_date, rows):
        errors, by_identity, by_pool = [], {}, {}
        for _hash, _date, _season, pool, identity, _display, _position in rows:
            if not pool_letter(pool):
                errors.append(f"unsupported pool {pool!r}")
            if identity in by_identity and by_identity[identity] != pool:
                errors.append(f"canonical team {identity!r} appears in both {by_identity[identity]} and {pool}")
            by_identity[identity] = pool
            by_pool[pool] = by_pool.get(pool, 0) + 1
        if not rows:
            errors.append("parser reconstructed zero roster rows")
        if any(count < 2 for count in by_pool.values()):
            errors.append("one or more parsed pools has fewer than two teams")
        # The historic selected-pool data is an integrity cross-check, not a
        # source to overwrite. Every old peer must still resolve into its pool.
        selected = list(db.execute("""SELECT g.pool, g.source_team, t.display_name
            FROM parsed_game g LEFT JOIN parsed_game_team t
              ON t.content_hash=g.content_hash AND t.logical_id=g.logical_id
            WHERE g.content_hash=? AND g.game_date=?""", (content_hash, game_date)))
        resolver = TeamIdentityResolver(db, persist=False)
        season = season_for_date(game_date)
        for item in selected:
            expected = item["pool"]
            for raw in (item["source_team"], item["display_name"]):
                if raw and by_identity.get(resolver.resolve(season, raw).canonical_normalized) != expected:
                    errors.append(f"selected-pool consistency failure for {raw!r}")
        return errors

    @staticmethod
    def _add_team_statistics(games: list[dict]) -> None:
        """Annotate each latest weekly observation with encounter history."""
        all_time_prior_by_team: dict[str, list[dict]] = {}
        season_prior_by_team: dict[tuple[str, str], list[dict]] = {}
        previous_team_ids: dict[tuple[str, str], set[str]] = {}
        for game in sorted(games, key=lambda row: (row["game_date"], row["start_time"], row["logical_id"])):
            game["season"] = game.get("season") or season_for_date(game["game_date"])
            season = game["season"]
            source_identity = normalize_team(game.get("source_team"))
            immediately_previous = previous_team_ids.get((season, source_identity), set())
            for team in game.get("pool_teams", []):
                identity = team["team_normalized"]
                season_prior = season_prior_by_team.get((season, identity), [])
                all_time_prior = all_time_prior_by_team.get(identity, [])
                season_count = len(season_prior)
                classification = "NEW THIS SEASON" if not season_count else "SAME AS LAST WEEK" if identity in immediately_previous else "RETURNING"
                team.update({"classification": classification, "prior_encounters": season_count,
                             "encounter_number": season_count + 1,
                             "all_time_encounters": len(all_time_prior) + 1,
                             "first_together": all_time_prior[0]["game_date"] if all_time_prior else None,
                             "last_together": all_time_prior[-1]["game_date"] if all_time_prior else None})
            # A session is the atomic weekly encounter even if its PDF is revised.
            for team in game.get("pool_teams", []):
                identity = team["team_normalized"]
                all_time_prior_by_team.setdefault(identity, []).append(game)
                season_prior_by_team.setdefault((season, identity), []).append(game)
            previous_team_ids[(season, source_identity)] = {team["team_normalized"] for team in game.get("pool_teams", [])}

    @staticmethod
    def _team_history(games: list[dict]) -> list[dict]:
        by_team: dict[str, dict] = {}
        for game in games:
            for team in game.get("pool_teams", []):
                record = by_team.setdefault(team["team_normalized"], {"team_normalized": team["team_normalized"],
                                                                          "team": team["display_name"], "weeks_together": 0,
                                                                          "first_seen": game["game_date"], "last_together": game["game_date"],
                                                                          "seasons": set()})
                record["weeks_together"] += 1
                record["seasons"].add(game.get("season") or season_for_date(game["game_date"]))
                if game["game_date"] < record["first_seen"]:
                    record["first_seen"] = game["game_date"]
                if game["game_date"] >= record["last_together"]:
                    record["team"], record["last_together"] = team["display_name"], game["game_date"]
        records = []
        for row in by_team.values():
            row["seasons_together"] = len(row.pop("seasons"))
            records.append(row)
        return sorted(records, key=lambda row: (
            -row["weeks_together"],
            -datetime.fromisoformat(row["last_together"]).date().toordinal(),
            row["team"].casefold(),
        ))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Repair season-scoped volleyball team identities")
    parser.add_argument("command", choices=["repair-team-identities", "pool-movement", "backfill-pool-rosters"])
    parser.add_argument("--season", required=True, help="Season to repair, e.g. 2026-27")
    parser.add_argument("--dry-run", action="store_true", help="Show planned aliases without recording them")
    parser.add_argument("--db", help="History SQLite path (defaults to $RUNTIME_DIR/history.sqlite3)")
    parser.add_argument("--pdf-dir", help="Retained PDF directory (defaults beside the history database)")
    args = parser.parse_args(argv)
    path = Path(args.db) if args.db else Path(os.environ.get("RUNTIME_DIR", "runtime")) / "history.sqlite3"
    store = HistoryStore(path)
    if args.command == "backfill-pool-rosters":
        result = store.backfill_pool_rosters(args.season, args.dry_run, Path(args.pdf_dir) if args.pdf_dir else None)
        print(f"Season {args.season}\n")
        for week in result["weeks"]:
            print(f"{week['week']}: existing pool-team rows: {week['existing_rows']}")
            if week["status"] == "failed":
                print("  failed:", week["error"])
            else:
                print(f"  reconstructed roster rows: {week['reconstructed_rows']}")
                print("  complete: yes" if not week["would_add"] else f"  {'would add' if args.dry_run else 'added'}: {week['would_add']}")
        print(f"\nSummary:\n  logical weeks inspected: {result['inspected']}\n  weeks requiring backfill: {result['requiring_backfill']}\n  rows {'to add' if args.dry_run else 'added'}: {result['rows_to_add'] if args.dry_run else result['rows_added']}\n  notification/calendar actions: 0")
        return 1 if result["failed"] else 0
    if args.command == "pool-movement":
        try:
            from src.settings import load
            user_names = load().get("team_names", [])
        except ValueError:
            user_names = []
        result = store.dashboard(user_names).get("pool_movement", {})
        if result.get("season") != args.season:
            print(f"No current pool movement for season {args.season}.")
            return 1
        print(f"Season {result['season']}\nWeek: {result['week']}\nPool: {result['pool']}\n")
        print(f"{'TEAM':24} {'PREVIOUS':10} {'CURRENT':9} MOVEMENT")
        for item in result["movements"]:
            direction = item["direction"].upper()
            if item.get("delta") and item["direction"] in {"up", "down"}:
                direction += f" {item['delta']}"
            marker = "  [YOUR TEAM]" if item.get("is_user_team") else ""
            print(f"{item['canonical_name'][:24]:24} {str(item.get('previous_pool') or '?'):10} {str(item.get('current_pool') or '?'):9} {direction}{marker}")
        validation = result.get("validation", {})
        print("\nValidation:")
        print("Status:", "OK" if validation.get("valid") else "WARNING")
        for message in [*validation.get("errors", []), *validation.get("warnings", [])]:
            print("-", message)
        return 0
    result = store.repair_team_identities(args.season, args.dry_run)
    verb = "Would learn" if args.dry_run else "Learned"
    for item in result["learned"]:
        print(f'{verb}: "{item.normalized}" -> "{item.canonical_name}" [score={item.score:.1f}]')
    for item in result["uncertain"]:
        print(f'Not merged (ambiguous/low confidence): "{item.normalized}" ~ "{item.candidate}" [score={item.score:.1f}]')
    print(f"Season {args.season}: observations={result['observations']}, unique teams {result['before']} -> {result['after']}, aliases {len(result['learned'])}, dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
