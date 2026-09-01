from datetime import datetime
import sqlite3

from src.history import HistoryStore


def game(pool="A POOL", position="1"):
    return {"uid": "volleyball-schedule-monitor-20260909-example-1", "source_team": "Example",
            "date": "2026-09-09", "start": datetime(2026, 9, 9, 19), "end": datetime(2026, 9, 9, 20),
            "gym": "Example Gym", "pool": pool, "pool_position": position, "summary": "Example Volleyball"}


def associated_game(uid, date, teams, **changes):
    event = game() | {"uid": uid, "date": date, "start": datetime.fromisoformat(date + "T19:00:00"),
                      "end": datetime.fromisoformat(date + "T20:00:00"), "pool_teams": teams}
    return event | changes


def test_revision_is_deduplicated_by_content_hash_and_stages_are_retained(tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    store.detect("hash", "2026-09-01T00:00:00+00:00", "https://first")
    store.detect("hash", "2026-09-02T00:00:00+00:00", "https://second")
    store.record_events("hash", [game()], "2026-09-01T01:00:00+00:00")
    store.record_stage("hash", "calendar", "2026-09-01T02:00:00+00:00")
    data = store.dashboard()
    assert len(data["revisions"]) == 1
    assert data["revisions"][0]["source_url"] == "https://first"
    assert data["revisions"][0]["calendar_at"]
    assert data["games"][0]["pool_position"] == "1"


def test_current_schedule_deduplicated_analytics_and_pool_observations(tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    first = game("C POOL", "4")
    changed = game("B POOL", "2") | {"start": datetime(2026, 9, 9, 20), "end": datetime(2026, 9, 9, 21), "gym": "New Gym"}
    removed = game("A POOL", "1") | {"uid": "volleyball-schedule-monitor-20260916-example-1", "date": "2026-09-16"}
    for digest, at, events in (("one", "2026-08-01T00:00:00+00:00", [first, removed]),
                               ("two", "2026-08-08T00:00:00+00:00", [first, removed]),
                               ("three", "2026-08-15T00:00:00+00:00", [changed])):
        store.detect(digest, at, "https://example/" + digest)
        store.record_events(digest, events, at)
    data = store.dashboard()
    # The newest parsed revision is the only current schedule; removed games
    # do not leak forward from an older PDF.
    assert [row["logical_id"] for row in data["current_games"]] == [changed["uid"]]
    # Three weekly PDFs still yield one logical session in analytics, using its
    # latest gym/time observation rather than fabricating played sessions.
    assert len(data["analytics_games"]) == 2
    latest = next(row for row in data["analytics_games"] if row["logical_id"] == changed["uid"])
    assert latest["gym"] == "New Gym" and latest["start_time"].endswith("20:00:00")
    assert sum(row["logical_id"] == changed["uid"] for row in data["analytics_games"]) == 1
    # Pool Movement uses the same one-point-per-session dataset and keeps the
    # latest pool, while all parsed revision rows remain available in games.
    assert len(data["games"]) == 5
    assert [row["pool"] for row in data["pool_observations"] if row["logical_id"] == changed["uid"]] == ["B POOL"]


def record_revision(store, digest, detected_at, event):
    store.detect(digest, detected_at, f"https://example/{digest}")
    store.record_events(digest, [event], detected_at)


def test_three_revisions_of_one_weekly_game_make_one_pool_movement_point(tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    event = game("D POOL", "4")
    for index, pool in enumerate(("D POOL", "D POOL", "C POOL"), start=1):
        record_revision(store, str(index), f"2026-08-{index:02d}T00:00:00+00:00", event | {"pool": pool})

    data = store.dashboard()
    assert len(data["games"]) == 3
    assert len(data["pool_observations"]) == 1
    assert data["pool_observations"][0]["pool"] == "C POOL"


def test_gym_only_change_makes_one_pool_movement_point(tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    event = game("C POOL", "3")
    record_revision(store, "old", "2026-08-01T00:00:00+00:00", event | {"gym": "Old Gym"})
    record_revision(store, "new", "2026-08-08T00:00:00+00:00", event | {"gym": "New Gym"})

    data = store.dashboard()
    assert len(data["pool_observations"]) == 1
    assert data["pool_observations"][0]["gym"] == "New Gym"


def test_pool_change_uses_latest_pool_for_same_game(tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    event = game("D POOL", "4")
    record_revision(store, "old", "2026-08-01T00:00:00+00:00", event)
    record_revision(store, "new", "2026-08-02T00:00:00+00:00", event | {"pool": "C POOL", "pool_position": "2"})

    point = store.dashboard()["pool_observations"]
    assert len(point) == 1 and point[0]["pool"] == "C POOL" and point[0]["pool_position"] == "2"


def test_separate_weekly_games_make_separate_chronological_points(tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    later = game("B POOL", "2") | {"uid": "later", "date": "2026-09-16",
                                    "start": datetime(2026, 9, 16, 19), "end": datetime(2026, 9, 16, 20)}
    earlier = game("D POOL", "4") | {"uid": "earlier"}
    record_revision(store, "later", "2026-08-01T00:00:00+00:00", later)
    record_revision(store, "earlier", "2026-08-02T00:00:00+00:00", earlier)

    points = store.dashboard()["pool_observations"]
    assert [(point["game_date"], point["pool"]) for point in points] == [
        ("2026-09-09", "D POOL"), ("2026-09-16", "B POOL")]


def test_team_history_classifies_new_same_and_returning_and_preserves_display_name(tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    record_revision(store, "one", "2026-08-01T00:00:00+00:00", associated_game("one", "2026-09-02", ["Team Alpha", "Team Bravo"]))
    record_revision(store, "two", "2026-08-02T00:00:00+00:00", associated_game("two", "2026-09-09", [" team   alpha ", "Team Charlie"]))
    record_revision(store, "three", "2026-08-03T00:00:00+00:00", associated_game("three", "2026-09-16", ["TEAM BRAVO", "Team Charlie"]))
    games = store.dashboard()["analytics_games"]
    first, second, third = games
    assert {item["display_name"]: item["classification"] for item in first["pool_teams"]} == {"Team Alpha": "NEW THIS SEASON", "Team Bravo": "NEW THIS SEASON"}
    alpha = next(item for item in second["pool_teams"] if item["team_normalized"] == "team alpha")
    assert alpha["classification"] == "SAME AS LAST WEEK" and alpha["encounter_number"] == 2 and alpha["last_together"] == "2026-09-02"
    bravo = next(item for item in third["pool_teams"] if item["team_normalized"] == "team bravo")
    assert bravo["classification"] == "RETURNING" and bravo["encounter_number"] == 2 and bravo["last_together"] == "2026-09-02"
    alpha_history = next(row for row in store.dashboard()["team_history"] if row["team"] == " team   alpha ")
    assert alpha_history["weeks_together"] == 2 and alpha_history["first_seen"] == "2026-09-02"


def test_three_revisions_with_changed_details_count_as_one_team_encounter(tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    weekly = associated_game("weekly", "2026-09-02", ["Team Alpha"])
    for digest, pool, hour in (("one", "A POOL", 19), ("two", "B POOL", 20), ("three", "C POOL", 21)):
        record_revision(store, digest, f"2026-08-0{len(digest)}T00:00:00+00:00", weekly | {"pool": pool, "gym": pool, "start": datetime(2026, 9, 2, hour), "end": datetime(2026, 9, 2, hour + 1)})
    record_revision(store, "new", "2026-08-09T00:00:00+00:00", associated_game("new-week", "2026-09-09", ["TEAM   ALPHA"]))
    data = store.dashboard()
    assert len(data["analytics_games"]) == 2
    record = data["team_history"][0]
    assert record["team"] == "TEAM   ALPHA" and record["weeks_together"] == 2
    assert record["first_seen"] == "2026-09-02" and record["last_together"] == "2026-09-09"
    assert data["analytics_games"][-1]["pool_teams"][0]["encounter_number"] == 2


def test_latest_revision_replaces_obsolete_team_membership_for_a_week(tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    record_revision(store, "original", "2026-08-01T00:00:00+00:00",
                    associated_game("week-one", "2026-09-02", ["Team Bravo"]))
    record_revision(store, "revised", "2026-08-02T00:00:00+00:00",
                    associated_game("week-one", "2026-09-02", ["Team Charlie"], gym="New Gym", pool="C POOL"))
    record_revision(store, "following", "2026-08-03T00:00:00+00:00",
                    associated_game("week-two", "2026-09-09", ["TEAM CHARLIE"]))
    data = store.dashboard()
    assert [team["display_name"] for team in data["current_games"][0]["pool_teams"]] == ["TEAM CHARLIE"]
    record = data["team_history"][0]
    assert record["team"] == "TEAM CHARLIE" and record["weeks_together"] == 2
    assert record["first_seen"] == "2026-09-02" and record["last_together"] == "2026-09-09"
    current_team = data["analytics_games"][-1]["pool_teams"][0]
    assert current_team["classification"] == "SAME AS LAST WEEK"
    assert current_team["prior_encounters"] == 1 and current_team["encounter_number"] == 2


def test_previous_logical_game_allows_calendar_gaps_and_tracks_first_and_last_prior(tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    record_revision(store, "one", "2026-08-01T00:00:00+00:00",
                    associated_game("one", "2026-09-02", ["Team Alpha"]))
    # Sep 9 is absent: the next stored logical game still defines last week.
    record_revision(store, "two", "2026-08-02T00:00:00+00:00",
                    associated_game("two", "2026-09-16", ["TEAM ALPHA"]))
    team = store.dashboard()["analytics_games"][-1]["pool_teams"][0]
    assert team["classification"] == "SAME AS LAST WEEK"
    assert team["prior_encounters"] == 1 and team["encounter_number"] == 2
    assert team["first_together"] == team["last_together"] == "2026-09-02"


def test_record_events_replaces_same_revision_transactionally(tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    store.detect("same", "2026-08-01T00:00:00+00:00", "https://example/same")
    store.record_events("same", [associated_game("one", "2026-09-02", ["Old Team"])], "2026-08-01T00:00:00+00:00")
    store.record_events("same", [associated_game("one", "2026-09-02", ["New Team"])], "2026-08-01T00:00:00+00:00")
    assert store.dashboard()["games"][0]["pool_teams"] == [{"team_normalized": "new team", "display_name": "New Team"}]


def test_season_scopes_new_returning_and_all_time_encounters(tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    record_revision(store, "old", "2026-01-01T00:00:00+00:00", associated_game("old", "2026-03-12", ["Team Alpha"]))
    record_revision(store, "first-new", "2026-09-01T00:00:00+00:00", associated_game("first-new", "2026-09-02", ["TEAM ALPHA"]))
    record_revision(store, "returning", "2026-09-08T00:00:00+00:00", associated_game("returning", "2026-09-09", ["Team Bravo"]))
    record_revision(store, "returning-two", "2026-09-15T00:00:00+00:00", associated_game("returning-two", "2026-09-16", ["TEAM ALPHA"]))
    games = store.dashboard()["analytics_games"]
    first_new = games[1]["pool_teams"][0]
    second_new = games[-1]["pool_teams"][0]
    assert first_new["classification"] == "NEW THIS SEASON"
    assert first_new["encounter_number"] == 1 and first_new["all_time_encounters"] == 2
    assert first_new["last_together"] == "2026-03-12"
    assert second_new["classification"] == "RETURNING"
    assert second_new["encounter_number"] == 2 and second_new["all_time_encounters"] == 3


def test_same_as_last_week_does_not_cross_a_season_boundary(tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    record_revision(store, "old", "2027-01-01T00:00:00+00:00", associated_game("old", "2027-08-31", ["Team Alpha"]))
    record_revision(store, "new", "2027-09-01T00:00:00+00:00", associated_game("new", "2027-09-01", ["Team Alpha"]))
    team = store.dashboard()["analytics_games"][-1]["pool_teams"][0]
    assert team["classification"] == "NEW THIS SEASON" and team["encounter_number"] == 1
    assert team["all_time_encounters"] == 2 and team["last_together"] == "2027-08-31"


def test_existing_database_is_migrated_and_backfilled_idempotently(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE schedule_revision (content_hash TEXT PRIMARY KEY, detected_at TEXT NOT NULL, source_url TEXT NOT NULL, parsed_at TEXT, calendar_at TEXT, email_at TEXT, completed_at TEXT);
            CREATE TABLE parsed_game (content_hash TEXT NOT NULL, logical_id TEXT NOT NULL, source_team TEXT NOT NULL, game_date TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL, gym TEXT, pool TEXT, pool_position TEXT, summary TEXT NOT NULL, PRIMARY KEY (content_hash, logical_id));
            INSERT INTO schedule_revision (content_hash, detected_at, source_url, parsed_at) VALUES ('legacy', '2027-01-01T00:00:00+00:00', 'https://example/legacy', '2027-01-01T00:00:00+00:00');
            INSERT INTO parsed_game VALUES ('legacy', 'legacy-game', 'Example', '2027-08-31', '2027-08-31T19:00:00', '2027-08-31T20:00:00', 'Gym', 'A POOL', '1', 'Example');
        """)
    HistoryStore(path)
    HistoryStore(path)
    with sqlite3.connect(path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(parsed_game)")}
        assert "season" in columns
        assert db.execute("SELECT season FROM parsed_game WHERE logical_id='legacy-game'").fetchone()[0] == "2026-27"
