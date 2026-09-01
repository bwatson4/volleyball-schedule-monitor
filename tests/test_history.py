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
