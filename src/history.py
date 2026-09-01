"""Small, dependency-free schedule history database.

History is an observability feature, not part of the crash-recovery contract.
Every write is transactional and callers deliberately treat failures as
non-fatal so a full disk cannot strand a schedule candidate mid-processing.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

from src.settings import normalize_team
from src.season import season_for_date


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
                    summary TEXT NOT NULL,
                    PRIMARY KEY (content_hash, logical_id)
                );
                CREATE TABLE IF NOT EXISTS parsed_game_team (
                    content_hash TEXT NOT NULL,
                    logical_id TEXT NOT NULL,
                    team_normalized TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    PRIMARY KEY (content_hash, logical_id, team_normalized),
                    FOREIGN KEY (content_hash, logical_id)
                        REFERENCES parsed_game(content_hash, logical_id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS parsed_game_team_identity_idx
                    ON parsed_game_team(team_normalized);
            """)
            # ``CREATE TABLE IF NOT EXISTS`` cannot add a column to a deployed
            # database.  Add and backfill season identity exactly once while
            # retaining every auditable PDF revision.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(parsed_game)")}
            if "season" not in columns:
                db.execute("ALTER TABLE parsed_game ADD COLUMN season TEXT")
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
            db.execute("DELETE FROM parsed_game WHERE content_hash=?", (content_hash,))
            db.executemany("""INSERT INTO parsed_game
                (content_hash, logical_id, source_team, game_date, season, start_time, end_time, gym, pool, pool_position, summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", [
                (content_hash, event["uid"], event["source_team"], event["date"], season_for_date(event["date"]), event["start"].isoformat(),
                 event["end"].isoformat(), event.get("gym"), event.get("pool"),
                 event.get("pool_position"), event["summary"])
                for event in events
            ])
            db.executemany("""INSERT INTO parsed_game_team
                (content_hash, logical_id, team_normalized, display_name)
                VALUES (?, ?, ?, ?)""", [
                (content_hash, event["uid"], normalized, display)
                for event in events
                for normalized, display in self._event_teams(event)
            ])

    @staticmethod
    def _event_teams(event: dict) -> list[tuple[str, str]]:
        """Accept structured parser data while reading pre-feature callers too."""
        result: list[tuple[str, str]] = []
        seen: set[str] = set()
        for team in event.get("pool_teams", []):
            if isinstance(team, dict):
                display = str(team.get("name") or team.get("display_name") or "")
                normalized = normalize_team(team.get("normalized_name") or team.get("team_normalized") or display)
            else:
                display, normalized = str(team), normalize_team(team)
            if normalized and display and normalized not in seen:
                seen.add(normalized)
                result.append((normalized, display))
        return result

    def record_stage(self, content_hash: str, stage: str, at: str) -> None:
        column = {"calendar": "calendar_at", "email": "email_at", "completed": "completed_at"}[stage]
        with self._connect() as db:
            db.execute(f"UPDATE schedule_revision SET {column}=COALESCE({column}, ?) WHERE content_hash=?", (at, content_hash))

    def dashboard(self) -> dict:
        """Return explicit current, deduplicated and historical UI datasets.

        ``current_games`` comes only from the most recently detected revision
        that parsed successfully.  ``analytics_games`` is one latest
        observation per logical session across all successful revisions.
        ``pool_observations`` uses the same one-point-per-session dataset for
        Pool Movement, while ``games`` retains every parsed revision row.
        This makes semantics a data-layer contract rather than a UI accident.
        """
        with self._connect() as db:
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
                    game["pool_teams"] = [dict(row) for row in db.execute("""
                        SELECT team_normalized, display_name FROM parsed_game_team
                        WHERE content_hash=? AND logical_id=? ORDER BY display_name COLLATE NOCASE
                    """, (game["content_hash"], game["logical_id"]))]

        self._add_team_statistics(analytics_games)
        analytics_by_id = {game["logical_id"]: game for game in analytics_games}
        for game in current_games:
            latest = analytics_by_id.get(game["logical_id"])
            if latest:
                game["pool_teams"] = latest["pool_teams"]
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
        return {"revisions": revisions, "games": games, "current_games": current_games,
                "analytics_games": analytics_games, "pool_observations": analytics_games,
                "team_history": team_history, "team_history_by_season": team_history_by_season,
                "seasons": seasons, "current_season": current_season}

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
