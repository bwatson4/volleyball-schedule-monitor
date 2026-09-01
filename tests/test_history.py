from datetime import datetime

from src.history import HistoryStore


def game(pool="A POOL", position="1"):
    return {"uid": "volleyball-schedule-monitor-20260909-example-1", "source_team": "Example",
            "date": "2026-09-09", "start": datetime(2026, 9, 9, 19), "end": datetime(2026, 9, 9, 20),
            "gym": "Example Gym", "pool": pool, "pool_position": position, "summary": "Example Volleyball"}


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
