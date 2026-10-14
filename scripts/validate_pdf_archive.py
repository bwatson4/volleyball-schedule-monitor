"""Replay every local PDF with temporary state/SQLite and blocked networking.

This is a development tool, never a production runner. Optional read-only
export metadata supplies independent historic expectations and incident hashes.
"""
import argparse
import gc
import hashlib
import json
import os
import socket
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def classify(text, diagnostics, events, wanted):
    import main
    from src.settings import normalize_text
    if main._publisher_notice(text) and not events:
        return "waiting_notice"
    if main._no_games_notice(text) and not events:
        return "explicit_no_games"
    if normalize_text(wanted) not in normalize_text(text):
        return "other_league_or_unrelated"
    if events:
        return "team_scheduled"
    if diagnostics.get("structure_validated") and not diagnostics.get("alias_matched"):
        return "validated_team_absent"
    return "incomplete_or_unrecognized"


def evaluate(path, settings, scratch, recorded=(), recorded_rosters=()):
    import main
    from src.fetcher import DownloadedPDF
    from src.history import HistoryStore
    from src.parser import ScheduleParser
    from src.state import ScheduleState
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    result = {"filename": str(path), "sha256": digest, "schedule_date": None,
              "classification": None, "team_found": False, "parser_events": 0,
              "events_generated": 0, "expected": "No invented events; preserve known-good on empty/unselected PDF",
              "comparison": "No independently recorded expectations", "pass": True}
    events, diagnostics = [], {}
    try:
        text = main._pdf_text(path)
        parser = ScheduleParser(text, settings["team_names"], settings["gyms"], pdf_path=path)
        events = parser.parse()
        diagnostics = parser.diagnostics
        result["classification"] = classify(text, diagnostics, events, settings["schedule_match_text"])
        result.update(schedule_date=diagnostics["date"], team_found=diagnostics["alias_matched"], parser_events=len(events))
    except Exception as exc:
        result["classification"] = "extraction_or_parse_failure"
        result["diagnostic"] = str(exc)
    result["diagnostics"] = diagnostics
    if recorded:
        def event_key(event):
            return (event["date"], event["source_team"], event["gym"], event["pool"],
                    str(event["pool_position"]), event["start"].isoformat(), event["end"].isoformat())
        expected = {(row["game_date"], row["source_team"], row.get("gym"), row.get("pool"),
                     str(row.get("pool_position")), row["start_time"], row["end_time"]) for row in recorded}
        observed = {event_key(event) for event in events}
        result["comparison"] = "Matches recorded historical games" if observed == expected else "Recorded historical games differ"
        result["expected"] = f"{len(expected)} recorded historical event(s)"
        result["pass"] &= observed == expected
        rotations = [row for row in recorded if row.get("rotation_matrix")]
        result["recorded_rotation_comparisons"] = len(rotations)
        for row in rotations:
            event = next((event for event in events if event["date"] == row["game_date"] and event["source_team"] == row["source_team"]), None)
            result["pass"] &= event is not None and json.loads(json.dumps(event["rotation_matrix"])) == json.loads(row["rotation_matrix"])
    if recorded_rosters:
        expected_rosters = {(row["game_date"], row["pool"], row["display_name"], str(row["pool_position"])) for row in recorded_rosters}
        observed_rosters = {(event["date"], pool["pool"], team["name"], str(team["position"]))
                            for event in events for pool in event.get("league_pools", []) for team in pool["teams"]}
        result["recorded_roster_rows_compared"] = len(expected_rosters)
        result["pass"] &= expected_rosters == observed_rosters
    state = ScheduleState(scratch / "state.json")
    state.data.update(completed={"hash": "baseline-known-good"}, last_successful_parsed="baseline-parse",
                      last_successfully_completed_run="baseline-completion")
    state.save()
    history = HistoryStore(scratch / "history.sqlite3")
    document = DownloadedPDF("https://example.invalid/archive.pdf", path, digest)
    fetcher = type("Fetcher", (), {"get_schedule_urls": lambda _: [document.url], "download": lambda _, url: document})()
    calendar_calls, email_calls = [], []
    calendar = type("Calendar", (), {"add_or_update_events": lambda _, ev: calendar_calls.append(ev)})()
    mailer = type("Mailer", (), {"send": lambda _, ev: email_calls.append(ev)})()
    def forbidden(*args, **kwargs):
        raise AssertionError("Real network/calendar/email operation attempted during archive replay")
    outcomes = []
    safe_settings = {**settings, "email_recipients": ["test@example.invalid"]}
    with patch("src.settings.load", return_value=safe_settings), \
         patch("src.calendar.CalendarManager", forbidden), patch("src.emailer.EmailSender", forbidden), \
         patch.object(socket.socket, "connect", forbidden):
        for _ in range(2):
            outcomes.append(main.run(fetcher=fetcher, state=state, history_factory=lambda _: history,
                calendar_factory=lambda: calendar, mailer_factory=lambda **_: mailer))
    result["events_generated"] = sum(len(call) for call in calendar_calls)
    result["actual"] = (f"runs={outcomes}; availability={state.data.get('schedule_availability', {}).get('status')}; "
                        f"calendar/email mock calls={len(calendar_calls)}/{len(email_calls)}")
    revisions = history.dashboard()["revisions"]
    result["pass"] &= len(revisions) <= 1 and len(calendar_calls) <= 1 and len(email_calls) <= 1
    if result["classification"] == "team_scheduled":
        result["pass"] &= outcomes == [True, True] and calendar_calls == email_calls == [events]
    else:
        result["pass"] &= not calendar_calls and not email_calls
        result["pass"] &= state.data["completed"]["hash"] == "baseline-known-good"
        result["pass"] &= state.data["last_successful_parsed"] == "baseline-parse"
    if result["classification"] == "validated_team_absent":
        result["pass"] &= outcomes == [True, True]
        result["pass"] &= state.data["schedule_availability"]["status"] == "team_not_scheduled"
    if result["classification"] == "explicit_no_games":
        result["pass"] &= outcomes == [True, True] and state.data["schedule_availability"]["status"] == "no_games_available"
    if result["classification"] == "waiting_notice":
        result["pass"] &= outcomes == [True, True] and state.data["schedule_availability"]["status"] == "waiting_for_schedule"
    return result


def validate(directories, settings, metadata=None):
    metadata = metadata or {}
    # Do not deduplicate: report every file examined and the unique byte count.
    paths = sorted({path.resolve() for directory in directories for path in Path(directory).glob("*.pdf")})
    if not paths:
        raise ValueError("No PDF files discovered; no validation performed")
    rows = []
    with tempfile.TemporaryDirectory(prefix="pdf-archive-validation-", dir=ROOT / "tmp") as work:
        for index, path in enumerate(paths):
            scratch = Path(work) / str(index)
            scratch.mkdir()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            recorded = [row for row in metadata.get("history", {}).get("parsed_game", []) if row["content_hash"] == digest]
            rosters = [row for row in metadata.get("history", {}).get("parsed_pool_team", []) if row["content_hash"] == digest]
            rows.append(evaluate(path, settings, scratch, recorded, rosters))
        # sqlite3's transaction context does not close connections. Collect
        # HistoryStore's released handles before Windows removes scratch files.
        gc.collect()
    inventory = metadata.get("pdfs", [])
    actual_hashes = {row["sha256"] for row in rows}
    archived_candidate = metadata.get("state", {}).get("candidate", {})
    fixture_hash = hashlib.sha256((ROOT / "tests/fixtures/pdfs/publisher-135.pdf").read_bytes()).hexdigest()
    coverage_complete = bool(inventory) and all(
        any(Path(row["filename"]).name == item["filename"] and row["sha256"] == item["sha256"] for row in rows)
        for item in inventory)
    return {"total_pdfs_tested": len(rows), "unique_pdf_hashes": len(actual_hashes),
            "pi_export_count": metadata.get("pdf_count"),
            "pi_export_coverage_complete": coverage_complete,
            "candidate_detected_at": archived_candidate.get("detected_at"),
            "candidate_hash": archived_candidate.get("hash"), "october14_fixture_hash": fixture_hash,
            "candidate_matches_october14_fixture": (archived_candidate["hash"] == fixture_hash if archived_candidate.get("hash") else None),
            "all_safety_and_recorded_comparisons_pass": all(row["pass"] for row in rows) and (not inventory or coverage_complete), "rows": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-dir", action="append", required=True)
    parser.add_argument("--settings", required=True)
    parser.add_argument("--metadata")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    (ROOT / "tmp").mkdir(exist_ok=True)
    # Test credentials only; never load a production environment or invoke main().
    for key, value in {"ICLOUD_USERNAME": "test@example.invalid", "ICLOUD_PASSWORD": "test-only",
                       "GMAIL_USERNAME": "test@example.invalid", "GMAIL_APP_PASSWORD": "test-only",
                       "EMAIL_RECIPIENTS": "test@example.invalid"}.items():
        os.environ[key] = value
    settings = json.loads(Path(args.settings).read_text())
    settings = settings.get("settings", settings)
    if "team_names" not in settings and "team_name" in settings:
        settings["team_names"] = [settings["team_name"]]
    metadata = json.loads(Path(args.metadata).read_text()) if args.metadata else None
    report = validate(args.pdf_dir, settings, metadata)
    Path(args.output).write_text(json.dumps(report, indent=2) + "\n")
    print(f"Tested {report['total_pdfs_tested']} PDF files ({report['unique_pdf_hashes']} unique hashes); "
          f"safety/recorded comparisons pass={report['all_safety_and_recorded_comparisons_pass']}")
    sys.exit(0 if report["all_safety_and_recorded_comparisons_pass"] else 1)
