import hashlib
import socket

import pytest

import main
from src.fetcher import DownloadedPDF
from src.history import HistoryStore
from src.parser import ScheduleParser
from src.state import ScheduleState
from tests.pdf_samples import samples


@pytest.mark.parametrize("kind,status,healthy", [
    ("unrelated", "available", False), ("empty", None, False), ("image_only", None, False),
    ("team_absent", "team_not_scheduled", True), ("no_games", "no_games_available", True),
    ("waiting", "waiting_for_schedule", True), ("incomplete", "available", False),
    ("malformed", None, False),
])
def test_real_generic_pdfs_never_fabricate_events_or_change_known_good(monkeypatch, tmp_path, kind, status, healthy):
    paths = samples(tmp_path / "pdfs")
    paths["malformed"].write_bytes(b"%PDF-1.4 malformed")
    path = paths[kind]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    settings = {"team_names": ["Configured Team"], "gyms": ["Example Gym"],
                "schedule_match_text": "Wednesday", "email_recipients": ["test@example.com"]}
    monkeypatch.setattr("src.settings.load", lambda: settings)
    def forbidden(*_args, **_kwargs):
        pytest.fail("No network/calendar/email operation is permitted in this test")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr("src.calendar.CalendarManager", forbidden)
    monkeypatch.setattr("src.emailer.EmailSender", forbidden)
    document = DownloadedPDF("https://example.com/current.pdf", path, digest)
    fetcher = type("Fetcher", (), {"get_schedule_urls": lambda _: [document.url], "download": lambda _, url: document})()
    state = ScheduleState(tmp_path / "state.json")
    state.data.update(completed={"hash": "known-good"}, last_successful_parsed="old-parse",
                      last_successfully_completed_run="old-completion")
    state.save()
    history = HistoryStore(tmp_path / "history.sqlite3")
    for _ in range(2):
        assert main.run(fetcher=fetcher, state=state, history_factory=lambda _: history) is healthy
        assert state.data["completed"]["hash"] == "known-good"
        assert state.data["last_successful_parsed"] == "old-parse"
        assert state.data["last_successfully_completed_run"] == "old-completion"
        assert state.data.get("schedule_availability", {}).get("status") == status
        assert not history.dashboard()["games"]
    assert len(history.dashboard()["revisions"]) <= 1
    if healthy:
        assert "last_failure" not in state.data
    if kind == "team_absent":
        assert not state.data["candidate"]["parsed"]
        assert state.data["candidate"]["classification"] == "team_not_scheduled"
    try:
        text = main._pdf_text(path)
    except ValueError:
        assert kind in ("empty", "image_only", "malformed")
    else:
        parser = ScheduleParser(text, settings["team_names"], settings["gyms"], pdf_path=path)
        assert parser.parse() == []
        assert parser.diagnostics["structure_validated"] is (kind == "team_absent")


def test_real_reduced_week_is_healthy_team_absent_observation(monkeypatch, tmp_path):
    from pathlib import Path
    path = Path(__file__).parent / "fixtures/pdfs/publisher-125.pdf"
    document = DownloadedPDF("https://example.com/current.pdf", path, hashlib.sha256(path.read_bytes()).hexdigest())
    monkeypatch.setattr("src.settings.load", lambda: {"team_names": ["Chewblockas", "Chewblaccas"],
        "gyms": ["KCS"], "schedule_match_text": "Wednesday", "email_recipients": ["test@example.com"]})
    def forbidden(*_args, **_kwargs):
        pytest.fail("Team absence must not trigger calendar or email")
    monkeypatch.setattr("src.calendar.CalendarManager", forbidden)
    monkeypatch.setattr("src.emailer.EmailSender", forbidden)
    fetcher = type("Fetcher", (), {"get_schedule_urls": lambda _: [document.url], "download": lambda _, url: document})()
    state = ScheduleState(tmp_path / "state.json")
    state.data["completed"] = {"hash": "known-good"}
    state.set_failure("parse", "old team-absent failure")
    history = HistoryStore(tmp_path / "history.sqlite3")
    for _ in range(2):
        assert main.run(fetcher=fetcher, state=state, history_factory=lambda _: history)
        assert state.data["schedule_availability"]["status"] == "team_not_scheduled"
        assert state.data["schedule_availability"]["schedule_date"] == "2026-09-30"
        assert state.data["completed"]["hash"] == "known-good"
        assert "last_failure" not in state.data
    assert len(history.dashboard()["revisions"]) == 1
    assert history.dashboard()["games"] == []


def test_previously_parsed_candidate_cannot_send_empty_reparse(monkeypatch, tmp_path):
    paths = samples(tmp_path / "pdfs")
    path = paths["team_absent"]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    document = DownloadedPDF("https://example.com/current.pdf", path, digest)
    monkeypatch.setattr("src.settings.load", lambda: {"team_names": ["Configured Team"], "gyms": ["Example Gym"],
        "schedule_match_text": "Wednesday", "email_recipients": ["test@example.com"]})
    def forbidden(*_args, **_kwargs):
        pytest.fail("An empty reparse must never reach calendar/email")
    monkeypatch.setattr("src.calendar.CalendarManager", forbidden)
    monkeypatch.setattr("src.emailer.EmailSender", forbidden)
    fetcher = type("Fetcher", (), {"get_schedule_urls": lambda _: [document.url], "download": lambda _, url: document})()
    state = ScheduleState(tmp_path / "state.json")
    state.data["completed"] = {"hash": "known-good"}
    state.begin_candidate(digest, path, document.url)
    state.mark_stage("parsed")
    history = HistoryStore(tmp_path / "history.sqlite3")
    assert not main.run(fetcher=fetcher, state=state, history_factory=lambda _: history)
    assert state.data["completed"]["hash"] == "known-good"
    assert state.data["last_failure"]["stage"] == "parse"
    assert "previously parsed candidate" in state.data["last_failure"]["message"]


def test_team_absent_candidate_can_recover_after_alias_correction(monkeypatch, tmp_path):
    paths = samples(tmp_path / "pdfs")
    path = paths["team_absent"]
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    document = DownloadedPDF("https://example.com/current.pdf", path, digest)
    settings = {"team_names": ["Configured Team"], "gyms": ["Example Gym"],
                "schedule_match_text": "Wednesday", "email_recipients": ["test@example.com"]}
    monkeypatch.setattr("src.settings.load", lambda: settings)
    calls = []
    sink = type("Sink", (), {"add_or_update_events": lambda _, events: calls.append(events),
                             "send": lambda _, events: calls.append(events)})()
    fetcher = type("Fetcher", (), {"get_schedule_urls": lambda _: [document.url], "download": lambda _, url: document})()
    state = ScheduleState(tmp_path / "state.json")
    state.data["completed"] = {"hash": "known-good"}
    history = HistoryStore(tmp_path / "history.sqlite3")
    kwargs = dict(fetcher=fetcher, state=state, history_factory=lambda _: history,
                  calendar_factory=lambda: sink, mailer_factory=lambda **_: sink)
    assert main.run(**kwargs)
    assert not calls
    settings["team_names"] = ["Other Team"]
    assert main.run(**kwargs)
    assert main.run(**kwargs)
    assert len(calls) == 2
    assert state.data["completed"]["hash"] == digest
    assert "classification" not in state.data["candidate"]
    assert len(history.dashboard()["revisions"]) == len(history.dashboard()["games"]) == 1
