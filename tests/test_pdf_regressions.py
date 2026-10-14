"""Real publisher PDFs: deterministic parsing and isolated pipeline replay."""
import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest

import main
from src.fetcher import DownloadedPDF
from src.history import HistoryStore
from src.parser import ScheduleParser
from src.state import ScheduleState
from ui import _rotation_rounds

FIXTURES = Path(__file__).parent / "fixtures" / "pdfs"
GYMS = ["Pacway", "KCS", "TCC", "OLPH"]
ALIASES = ["Chewblockas", "Chewblaccas"]


def parse(filename, aliases=ALIASES):
    path = FIXTURES / filename
    parser = ScheduleParser(main._pdf_text(path), aliases, GYMS, pdf_path=path)
    return parser, parser.parse()


@pytest.mark.parametrize("filename,date,gym,position,start,end", [
    ("publisher-135.pdf", "2026-10-14", "KCS", "3", "19:00", "20:30"),
    ("publisher-132.pdf", "2026-10-07", "TCC", "3", "20:00", "21:30"),
    ("publisher-129.pdf", "2026-10-07", "TCC", "3", "20:00", "21:30"),
    ("publisher-122.pdf", "2026-09-23", "Pacway", "4", "19:00", "20:30"),
    ("publisher-120.pdf", "2026-09-23", "Pacway", "4", "19:00", "20:30"),
    ("september-16.pdf", "2026-09-16", "KCS", "3", "20:30", "22:00"),
])
def test_actual_schedules(filename, date, gym, position, start, end):
    parser, events = parse(filename)
    assert len(events) == 1
    event = events[0]
    assert (event["date"], event["gym"], event["pool"], event["pool_position"]) == (date, gym, "D POOL", position)
    assert event["start"] == datetime.fromisoformat(f"{date}T{start}")
    assert event["end"] == datetime.fromisoformat(f"{date}T{end}")
    assert parser.diagnostics["coordinate_blocks"] in (6, 8)
    assert len(event["pool_teams"]) == 4
    assert event["rotation_matrix"] == [
        {"round": "Game 1", "pairings": [(1, 5), (2, 3)]},
        {"round": "Game 2", "pairings": [(1, 4), (3, 5)]},
        {"round": "Game 3", "pairings": [(1, 3), (2, 4)]},
        {"round": "Game 4", "pairings": [(1, 2), (4, 5)]},
        {"round": "Game 5", "pairings": [(3, 4), (2, 5)]},
    ]


def test_october14_reproduces_old_failure_and_repairs_rotation():
    path = FIXTURES / "publisher-135.pdf"
    text = main._pdf_text(path)
    legacy = ScheduleParser(text, ALIASES, GYMS)
    assert legacy.parse() == []
    assert legacy.diagnostics["teams_extracted"] == 0
    assert "Chewblockas" in text
    _, events = parse(path.name)
    assert [(r["status"], r["opponent"]) for r in _rotation_rounds(events[0])] == [
        ("PLAY", "Block Busters"), ("PLAY", "Leisure Athlete"),
        ("PLAY", "Back Seat Sets"), ("SIT", ""), ("PLAY", "Serves You Right")]


@pytest.mark.parametrize("filename", ["september-16.pdf", "publisher-120.pdf", "publisher-122.pdf", "publisher-129.pdf", "publisher-132.pdf"])
def test_coordinates_preserve_all_previously_parsed_assignments(filename):
    path = FIXTURES / filename
    text = main._pdf_text(path)
    parser, events = parse(filename)
    aliases = [team["name"] for pool in events[0]["league_pools"] for team in pool["teams"]]
    old = ScheduleParser(text, aliases, GYMS).parse()
    new = ScheduleParser(text, aliases, GYMS, pdf_path=path).parse()
    assert len(old) == len(new) == parser.diagnostics["teams_extracted"]
    assert {e["uid"]: e for e in old} == {e["uid"]: e for e in new}


def test_reduced_week_recognizes_inline_number_and_absent_configured_team():
    parser, events = parse("publisher-125.pdf")
    assert events == []
    assert parser.diagnostics["teams_extracted"] == 10
    assert parser.diagnostics["failure_kind"] == "team_not_matched"
    assert parser.league_pools["H POOL"][3]["name"] == "Court Ordered Community Service"


@pytest.mark.parametrize("aliases", [["", " CHEWBLOCKAS "], ["irrelevant", "Chewblockas"], ["CHEWBLACCAS", "Chewblockas"]])
def test_alias_normalization_with_actual_pdf(aliases):
    assert len(parse("publisher-135.pdf", aliases)[1]) == 1


@pytest.mark.parametrize("text,reason", [
    ("", "text_extraction"),
    ("Wednesday October 14, 2026\nnot a table", "schedule_structure"),
    ("Gym October 14, 2026\nA POOL\n1 Other Team 7:00-8:30", "team_not_matched"),
    ("Gym October 14, 2026\nA POOL\n1 Target Team", "event_generation"),
])
def test_failure_categories(text, reason):
    parser = ScheduleParser(text, ["Target Team"], ["Gym"])
    assert parser.parse() == []
    assert parser.diagnostics["failure_kind"] == reason


def test_malformed_pdf_error_is_actionable(tmp_path):
    path = tmp_path / "bad.pdf"
    path.write_bytes(b"%PDF-1.4\nnot a PDF document")
    with pytest.raises(ValueError, match="PDF text extraction failed"):
        main._pdf_text(path)


def test_empty_extraction_error_is_actionable(monkeypatch):
    monkeypatch.setattr(main, "_extract_pdf_text", lambda _: "\f\n")
    with pytest.raises(ValueError, match="no readable text"):
        main._pdf_text("unused.pdf")


def test_fixture_hashes_match_provenance():
    for fixture in json.loads((FIXTURES / "manifest.json").read_text()):
        assert hashlib.sha256((FIXTURES / fixture["file"]).read_bytes()).hexdigest() == fixture["sha256"]


def test_coordinate_tables_ignore_pdf_object_order(monkeypatch):
    from pdfminer.high_level import extract_pages
    pages = list(extract_pages(str(FIXTURES / "publisher-135.pdf")))
    for page in pages:
        page._objs.reverse()
    monkeypatch.setattr("src.pdf_structure.extract_pages", lambda _: iter(pages))
    _, events = parse("publisher-135.pdf")
    assert (events[0]["gym"], events[0]["pool"], events[0]["pool_position"]) == ("KCS", "D POOL", "3")


def test_multiple_dates_keep_legacy_block_parsing(monkeypatch):
    def forbidden(*_):
        pytest.fail("Coordinate parser must not apply one date to multiple sessions")
    monkeypatch.setattr("src.pdf_structure.pool_tables", forbidden)
    text = "Gym October 14, 2026\nA POOL\n1 Target 7:00-8:00\nGym October 21, 2026\nA POOL\n1 Target 8:00-9:00"
    parser = ScheduleParser(text, ["Target"], ["Gym"], pdf_path="unused.pdf")
    assert [event["date"] for event in parser.parse()] == ["2026-10-14", "2026-10-21"]


def test_pipeline_reports_extraction_failure_and_preserves_known_good(monkeypatch, tmp_path):
    path = tmp_path / "malformed.pdf"
    path.write_bytes(b"%PDF-1.4\nnot a document")
    document = DownloadedPDF("https://example.com/bad.pdf", path, "bad-hash")
    fetcher = type("Fetcher", (), {"get_schedule_urls": lambda _: [document.url],
                                  "download": lambda _, url: document})()
    monkeypatch.setattr("src.settings.load", lambda: {"team_names": ALIASES, "gyms": GYMS,
                        "schedule_match_text": "Wednesday", "email_recipients": ["test@example.com"]})
    def forbidden(*args, **kwargs):
        pytest.fail("Malformed PDF must not trigger external operations")
    monkeypatch.setattr("src.calendar.CalendarManager", forbidden)
    monkeypatch.setattr("src.emailer.EmailSender", forbidden)
    state = ScheduleState(tmp_path / "state.json")
    state.data["completed"] = {"hash": "known-good"}
    history = HistoryStore(tmp_path / "history.sqlite3")
    assert not main.run(fetcher=fetcher, state=state, history_factory=lambda _: history)
    assert state.data["completed"]["hash"] == "known-good"
    assert state.data["last_failure"]["stage"] == "parse"
    assert "PDF text extraction failed" in state.data["last_failure"]["message"]


def test_actual_failed_pdf_recovers_without_duplicate_revision_or_notifications(monkeypatch, tmp_path):
    path = FIXTURES / "publisher-135.pdf"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    document = DownloadedPDF("https://example.com/current.pdf", path, digest)
    fetcher = type("Fetcher", (), {"get_schedule_urls": lambda _: [document.url],
                                  "download": lambda _, url: document})()
    monkeypatch.setattr("src.settings.load", lambda: {"team_names": ALIASES, "gyms": GYMS,
                        "schedule_match_text": "Wednesday", "email_recipients": ["test@example.com"]})
    # Any accidental use of real service constructors fails the test.
    def forbidden(*args, **kwargs):
        pytest.fail("Real calendar/email operation attempted")
    monkeypatch.setattr("src.calendar.CalendarManager", forbidden)
    monkeypatch.setattr("src.emailer.EmailSender", forbidden)
    calendar_events, email_events = [], []
    calendar = type("Calendar", (), {"add_or_update_events": lambda _, events: calendar_events.append(events)})()
    mailer = type("Mailer", (), {"send": lambda _, events: email_events.append(events)})()
    state = ScheduleState(tmp_path / "state.json")
    state.data.update(completed={"hash": "known-good"}, last_successful_parsed="old-parse")
    state.save()
    history = HistoryStore(tmp_path / "history.sqlite3")
    kwargs = dict(fetcher=fetcher, state=state, history_factory=lambda _: history,
                  calendar_factory=lambda: calendar, mailer_factory=lambda **_: mailer)
    # Reproduce two failing scans using the original text parser, then retry
    # the identical hash using the repaired default pipeline.
    for _ in range(2):
        assert not main.run(parser_class=lambda text: ScheduleParser(text, ALIASES, GYMS), **kwargs)
        assert state.data["completed"]["hash"] == "known-good"
        assert state.data["last_successful_parsed"] == "old-parse"
        assert not calendar_events and not email_events
    assert len(history.dashboard()["revisions"]) == 1
    assert main.run(**kwargs)
    assert main.run(**kwargs)
    assert len(calendar_events) == len(email_events) == 1
    assert calendar_events == email_events
    event = calendar_events[0][0]
    assert (event["date"], event["gym"], event["pool"], event["pool_position"]) == ("2026-10-14", "KCS", "D POOL", "3")
    assert (event["start"].hour, event["end"].hour, event["end"].minute) == (19, 20, 30)
    assert state.data["completed"]["hash"] == digest
    assert "last_failure" not in state.data
    assert len(history.dashboard()["revisions"]) == len(history.dashboard()["current_games"]) == 1
