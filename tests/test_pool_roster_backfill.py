from datetime import datetime

from src.history import HistoryStore


def event(uid, day, pool, peers):
    return {"uid": uid, "source_team": "Chewblaccas", "date": day,
            "start": datetime.fromisoformat(day + "T19:00:00"), "end": datetime.fromisoformat(day + "T20:00:00"),
            "gym": "Gym", "pool": f"{pool} POOL", "pool_position": "1", "summary": "Chewblaccas",
            "pool_teams": peers}


def record(store, digest, item):
    store.detect(digest, f"2026-09-{1 if digest == 'old' else 2:02d}T00:00:00+00:00", "https://example/" + digest)
    store.record_events(digest, [item], "2026-09-01T00:00:00+00:00")


def install_reconstructor(monkeypatch, store, data):
    def reconstruct(_db, resolver, content_hash, day, _pdf_dir):
        result = []
        for pool, names in data[day].items():
            for index, name in enumerate(names):
                identity = resolver.resolve("2026-27", name)
                result.append((content_hash, day, "2026-27", f"{pool} POOL", identity.canonical_normalized,
                               identity.canonical_name, str(index + 1)))
        return result
    monkeypatch.setattr(store, "_reconstruct_pool_rosters", reconstruct)


def test_backfill_adds_complete_roster_is_idempotent_and_preserves_revision_stages(tmp_path, monkeypatch):
    store = HistoryStore(tmp_path / "history.sqlite3")
    record(store, "old", event("old-week", "2026-09-16", "D", ["Stay"]))
    store.record_stage("old", "calendar", "2026-09-01T01:00:00+00:00")
    store.record_stage("old", "email", "2026-09-01T01:01:00+00:00")
    install_reconstructor(monkeypatch, store, {"2026-09-16": {
        "C": ["Down One", "Down Two"], "D": ["Chewblaccas", "Stay"], "E": ["Up One", "Up Two"]}})
    before = store.dashboard()["revisions"]
    dry = store.backfill_pool_rosters("2026-27", dry_run=True)
    assert dry["rows_to_add"] == 4
    assert store.dashboard()["revisions"] == before
    applied = store.backfill_pool_rosters("2026-27")
    assert applied["rows_added"] == 4
    again = store.backfill_pool_rosters("2026-27")
    assert again["rows_added"] == 0 and again["weeks"][0]["status"] == "complete"
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM parsed_pool_team WHERE content_hash='old'").fetchone()[0] == 6
        revision = db.execute("SELECT calendar_at, email_at FROM schedule_revision WHERE content_hash='old'").fetchone()
        assert tuple(revision) == ("2026-09-01T01:00:00+00:00", "2026-09-01T01:01:00+00:00")


def test_backfill_failure_leaves_partial_roster_untouched(tmp_path, monkeypatch):
    store = HistoryStore(tmp_path / "history.sqlite3")
    record(store, "old", event("old-week", "2026-09-16", "D", ["Stay"]))
    install_reconstructor(monkeypatch, store, {"2026-09-16": {"D": ["Chewblaccas"]}})
    result = store.backfill_pool_rosters("2026-27")
    assert result["failed"] and "fewer than two teams" in result["failed"][0]["error"]
    with store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM parsed_pool_team WHERE content_hash='old'").fetchone()[0] == 2


def test_backfill_removes_legacy_missing_previous_and_keeps_canonical_aliases(tmp_path, monkeypatch):
    store = HistoryStore(tmp_path / "history.sqlite3")
    record(store, "old", event("old-week", "2026-09-16", "D", ["Stay"]))
    record(store, "new", event("new-week", "2026-09-23", "D", ["Stay", "Block Buster", "Team Down"]))
    install_reconstructor(monkeypatch, store, {
        "2026-09-16": {"C": ["Team Down", "C Stay"], "D": ["Chewblaccas", "Stay"], "E": ["Block Busters", "E Stay"]},
        "2026-09-23": {"D": ["Chewblaccas", "Stay", "Block Buster", "Team Down"]},
    })
    store.backfill_pool_rosters("2026-27")
    movement = store.dashboard(["Chewblaccas"])["pool_movement"]
    by_identity = {item["canonical_team_id"]: item for item in movement["movements"]}
    assert by_identity["block buster"]["direction"] == "up"
    assert by_identity["team down"]["direction"] == "down"
    assert not any(item["status"] == "missing_previous_week" for item in movement["movements"])
