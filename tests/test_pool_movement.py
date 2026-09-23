from datetime import datetime
import sqlite3

from src.history import HistoryStore


def event(uid, day, pool, roster, source="Chewblaccas"):
    return {
        "uid": uid, "source_team": source, "date": day,
        "start": datetime.fromisoformat(day + "T19:00:00"),
        "end": datetime.fromisoformat(day + "T20:00:00"), "gym": "Gym",
        "pool": f"{pool} POOL", "pool_position": "1", "summary": source,
        "pool_teams": [name for name in roster if name != source],
        "league_pools": roster,
    }


def full_roster(**pools):
    return [{"pool": f"{letter} POOL", "teams": [
        {"name": name, "position": index + 1} for index, name in enumerate(names)
    ]} for letter, names in pools.items()]


def record(store, digest, item):
    detected = {"one": "2026-09-01", "two": "2026-09-02"}[digest]
    store.detect(digest, detected + "T00:00:00+00:00", "https://example/" + digest)
    store.record_events(digest, [item], "2026-09-01T00:00:00+00:00")


def test_current_pool_movement_uses_canonical_alias_and_validates_normal_pool(tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    prior = full_roster(C=["Team Down"], D=["Chewblaccas", "Stay 1", "Stay 2", "Stay 3"], E=["Block Busters"])
    current = full_roster(D=["Chewblaccas", "Stay 1", "Stay 2", "Stay 3", "Team Down", "Block Buster"])
    record(store, "one", {**event("week-one", "2026-09-16", "D", ["Chewblaccas", "Stay 1", "Stay 2", "Stay 3"]), "league_pools": prior})
    record(store, "two", {**event("week-two", "2026-09-23", "D", ["Chewblaccas", "Stay 1", "Stay 2", "Stay 3", "Team Down", "Block Buster"]), "league_pools": current})
    movement = store.dashboard(["Chewblaccas"])["pool_movement"]
    rows = {row["canonical_name"]: row for row in movement["movements"]}
    assert rows["Block Busters"]["previous_pool"] == "E" and rows["Block Busters"]["current_pool"] == "D"
    assert rows["Block Busters"]["direction"] == "up" and rows["Block Busters"]["delta"] == 1
    assert rows["Team Down"]["previous_pool"] == "C" and rows["Team Down"]["direction"] == "down"
    assert rows["Chewblaccas"]["is_user_team"] and rows["Chewblaccas"]["direction"] == "same"
    assert movement["validation"]["valid"]


def test_missing_and_multi_pool_movement_are_derived_but_warned(tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    record(store, "one", {**event("old", "2026-09-16", "D", ["Chewblaccas"]),
                          "league_pools": full_roster(D=["Chewblaccas"], F=["Far Team"])})
    record(store, "two", {**event("new", "2026-09-23", "D", ["Chewblaccas", "Far Team", "Missing Team"]),
                          "league_pools": full_roster(D=["Chewblaccas", "Far Team", "Missing Team"])})
    movement = store.dashboard(["Chewblaccas"])["pool_movement"]
    rows = {row["canonical_name"]: row for row in movement["movements"]}
    assert rows["Far Team"]["direction"] == "up" and rows["Far Team"]["delta"] == 2
    assert rows["Missing Team"]["status"] == "missing_previous_week"
    assert not movement["validation"]["valid"]


def test_first_week_and_pool_boundaries(tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    record(store, "one", {**event("first", "2026-09-16", "A", ["Chewblaccas"]), "league_pools": full_roster(A=["Chewblaccas"])})
    assert store.dashboard(["Chewblaccas"])["pool_movement"]["status"] == "season_start"
    record(store, "two", {**event("second", "2026-09-23", "A", ["Chewblaccas", "Promoted"]),
                          "league_pools": full_roster(A=["Chewblaccas", "Promoted"], B=["Other"])})
    # The missing historical promoted team is reported instead of inventing an A-boundary move.
    assert not store.dashboard(["Chewblaccas"])["pool_movement"]["validation"]["valid"]


def test_contextual_typo_is_learned_repaired_and_persisted(tmp_path):
    path = tmp_path / "history.sqlite3"
    store = HistoryStore(path)
    prior = full_roster(C=["Team Down"], D=["Chewblaccas", "Stay 1", "Stay 2", "Stay 3"], E=["Block Busters"])
    current = full_roster(C=["Other C"], D=["Chewblockas", "Stay 1", "Stay 2", "Stay 3", "Team Down", "Block Buster"], E=["Other E"])
    record(store, "one", {**event("old", "2026-09-16", "D", ["Chewblaccas", "Stay 1", "Stay 2", "Stay 3"]), "league_pools": prior})
    record(store, "two", {**event("new", "2026-09-23", "D", ["Chewblockas", "Stay 1", "Stay 2", "Stay 3", "Team Down", "Block Buster"], source="Chewblockas"), "league_pools": current})
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM team_alias WHERE alias_normalized='chewblockas'")
    result = store.repair_team_identities("2026-27")
    assert [(d.normalized, d.canonical_name) for d in result["learned"]] == [("chewblockas", "Chewblaccas")]
    assert not store.repair_team_identities("2026-27")["learned"]
    reloaded = HistoryStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT canonical_normalized FROM team_alias WHERE alias_normalized='chewblockas'").fetchone()[0] == "chewblaccas"
        assert db.execute("SELECT display_name FROM parsed_pool_team WHERE display_name='Chewblockas'").fetchone()[0] == "Chewblockas"
    movement = reloaded.dashboard(["Chewblaccas"])["pool_movement"]
    own = next(row for row in movement["movements"] if row["is_user_team"])
    assert (own["canonical_name"], own["previous_pool"], own["current_pool"], own["direction"]) == ("Chewblaccas", "D", "D", "same")
    assert not any(row["status"] == "missing_previous_week" for row in movement["movements"] if row["is_user_team"])
    assert reloaded.dashboard()["current_games"][0]["source_team_canonical_name"] == "Chewblaccas"
    assert next(row for row in movement["movements"] if row["canonical_name"] == "Block Busters")["direction"] == "up"


def test_context_requires_league_overlap_and_rejects_unrelated_or_ambiguous_names(tmp_path):
    for old_names, new_name, complete in [(["Chewblaccas"], "Chewblockas", False),
                                           (["Completely Different"], "Chewblockas", True),
                                           (["Chewblaccas", "Chewblarkas"], "Chewblockas", True)]:
        store = HistoryStore(tmp_path / (new_name + str(len(old_names)) + old_names[0] + ".sqlite3"))
        prior = full_roster(C=["Team Down"], D=[*old_names, "Stay 1", "Stay 2", "Stay 3"], E=["Team Up"])
        current = full_roster(D=[new_name, "Stay 1", "Stay 2", "Stay 3", "Team Down", "Team Up"])
        if complete:
            current += full_roster(C=["Other C"], E=["Other E"])
        record(store, "one", {**event("old", "2026-09-16", "D", old_names), "league_pools": prior})
        record(store, "two", {**event("new", "2026-09-23", "D", [new_name], source=new_name), "league_pools": current})
        assert not store.repair_team_identities("2026-27")["learned"]
        movements = store.dashboard()["pool_movement"]["movements"]
        assert any(row["canonical_name"] == new_name and row["status"] == "missing_previous_week"
                   for row in movements), (old_names, movements)
