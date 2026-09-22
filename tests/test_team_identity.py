from datetime import datetime
import sqlite3

from src.history import HistoryStore
from src.team_identity import normalize_team


def event(uid, day, teams):
    return {"uid": uid, "source_team": "Mine", "date": day, "start": datetime.fromisoformat(day + "T19:00:00"),
            "end": datetime.fromisoformat(day + "T20:00:00"), "summary": "Mine", "pool_teams": teams}


def record(store, digest, item):
    store.detect(digest, "2026-08-01T00:00:00+00:00", "https://example/" + digest)
    store.record_events(digest, [item], "2026-08-01T00:00:00+00:00")


def test_block_buster_backfill_is_canonical_idempotent_and_preserves_revisions(tmp_path):
    path = tmp_path / "history.sqlite3"; store = HistoryStore(path)
    record(store, "sep16", event("sep16", "2026-09-16", ["Block Busters"]))
    record(store, "sep23", event("sep23", "2026-09-23", ["Block Buster"]))
    store.record_stage("sep16", "calendar", "2026-09-01T01:00:00+00:00")
    store.record_stage("sep16", "email", "2026-09-01T01:01:00+00:00")
    # Simulate the already-installed pre-feature database: remove the new
    # metadata, while retaining every raw revision spelling.
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM team_alias"); db.execute("DELETE FROM team_identity")
    dry = store.repair_team_identities("2026-27", dry_run=True)
    assert [(x.normalized, x.canonical_name) for x in dry["learned"]] == [("block buster", "Block Busters")]
    result = store.repair_team_identities("2026-27")
    assert len(result["learned"]) == 1
    again = store.repair_team_identities("2026-27")
    assert not again["learned"]
    data = store.dashboard(); rows = data["team_history_by_season"]["2026-27"]
    assert [(row["team"], row["weeks_together"], row["first_seen"]) for row in rows] == [("Block Busters", 2, "2026-09-16")]
    assert len(data["revisions"]) == 2 and len(data["games"]) == 2
    revision = next(row for row in data["revisions"] if row["content_hash"] == "sep16")
    assert revision["calendar_at"] == "2026-09-01T01:00:00+00:00" and revision["email_at"] == "2026-09-01T01:01:00+00:00"


def test_normalization_handles_case_spacing_unicode_quotes_and_punctuation():
    assert normalize_team("  O’Malley--SET!!  ") == normalize_team("o'malley set") == "omalley set"


def test_one_edit_long_name_learns_but_short_names_are_conservative(tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    record(store, "one", event("one", "2026-09-16", ["Thunderbolts", "AB"]))
    record(store, "two", event("two", "2026-09-23", ["Thunderboltz", "AC"]))
    rows = store.dashboard()["team_history_by_season"]["2026-27"]
    names = {row["team"]: row["weeks_together"] for row in rows}
    assert names["Thunderbolts"] == 2
    assert names["AB"] == names["AC"] == 1


def test_similar_and_ambiguous_names_are_not_automatically_merged(tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    record(store, "one", event("one", "2026-09-16", ["The Spikers", "The Spikers Red"]))
    record(store, "two", event("two", "2026-09-23", ["The Spikers Blu"]))
    # The typo is close to both existing canonical teams, so its confidence
    # lead is too small; all three names remain identities.
    rows = store.dashboard()["team_history_by_season"]["2026-27"]
    assert len(rows) == 3


def test_aliases_survive_store_reload_and_do_not_cross_seasons(tmp_path):
    path = tmp_path / "history.sqlite3"; store = HistoryStore(path)
    record(store, "one", event("one", "2026-09-16", ["Block Busters"]))
    record(store, "two", event("two", "2026-09-23", ["Block Buster"]))
    reloaded = HistoryStore(path)
    assert reloaded.dashboard()["team_history_by_season"]["2026-27"][0]["weeks_together"] == 2
    record(reloaded, "new-season", event("new-season", "2027-09-16", ["Block Buster"]))
    assert reloaded.dashboard()["team_history_by_season"]["2027-28"][0]["team"] == "Block Buster"
