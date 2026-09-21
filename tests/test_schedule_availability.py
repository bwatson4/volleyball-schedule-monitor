from pathlib import Path

import main
from src.fetcher import DownloadedPDF
from src.state import ScheduleState
from ui import _history_view, _page


def _settings():
    return {"team_names": ["Team"], "schedule_match_text": "Wednesday", "gyms": ["Gym"], "email_recipients": ["a@example.com"]}


class Fetcher:
    def __init__(self, document): self.document = document
    def get_schedule_urls(self): return [self.document.url]
    def download(self, _url): return self.document


class Recorder:
    def __init__(self): self.calls = 0
    def add_or_update_events(self, _events): self.calls += 1
    def send(self, _events): self.calls += 1


def _run(monkeypatch, tmp_path, text, events):
    monkeypatch.setattr("src.settings.load", _settings)
    document = DownloadedPDF("https://example/current.pdf", Path("current.pdf"), "pending-hash")
    monkeypatch.setattr(main, "_pdf_text", lambda _path: text)
    parser = lambda _text: type("Parser", (), {"parse": lambda self: events})()
    calendar, mailer = Recorder(), Recorder()
    state = ScheduleState(tmp_path / "state.json")
    result = main.run(fetcher=Fetcher(document), parser_class=parser, calendar_factory=lambda: calendar,
                      mailer_factory=lambda **_kwargs: mailer, state=state)
    return result, state, calendar, mailer


def test_placeholder_is_healthy_and_keeps_publisher_message(monkeypatch, tmp_path):
    result, state, calendar, mailer = _run(monkeypatch, tmp_path,
        "Schedule will be posted the evening of Monday Sept 21st.", [])
    assert result and state.data["schedule_availability"]["status"] == "waiting_for_schedule"
    assert state.data["schedule_availability"]["publisher_message"] == "Schedule will be posted the evening of Monday Sept 21st."
    assert "last_failure" not in state.data and not calendar.calls and not mailer.calls


def test_waiting_for_schedule_advances_check_but_not_completed_update(monkeypatch, tmp_path):
    result, state, _calendar, _mailer = _run(monkeypatch, tmp_path, "Wednesday Team", [{"uid": "event"}])
    assert result
    previous_completion = "2026-09-16T14:22:00+00:00"
    state.data["last_successfully_completed_run"] = previous_completion
    state.save()
    document = DownloadedPDF("https://example/pending.pdf", Path("pending.pdf"), "new-pending-hash")
    monkeypatch.setattr(main, "_pdf_text", lambda _path: "Schedule will be posted soon.")
    parser = lambda _text: type("Parser", (), {"parse": lambda self: []})()
    assert main.run(fetcher=Fetcher(document), parser_class=parser, state=state)
    assert state.data["last_successful_check"] != previous_completion
    assert state.data["last_successfully_completed_run"] == previous_completion


def test_no_games_advances_check_but_not_completed_update(monkeypatch, tmp_path):
    result, state, _calendar, _mailer = _run(monkeypatch, tmp_path, "Wednesday Team", [{"uid": "event"}])
    assert result
    previous_completion = "2026-09-16T14:22:00+00:00"
    state.data["last_successfully_completed_run"] = previous_completion
    state.save()
    document = DownloadedPDF("https://example/pending.pdf", Path("pending.pdf"), "new-pending-hash")
    monkeypatch.setattr(main, "_pdf_text", lambda _path: "Wednesday document")
    parser = lambda _text: type("Parser", (), {"parse": lambda self: []})()
    assert main.run(fetcher=Fetcher(document), parser_class=parser, state=state)
    assert state.data["last_successful_check"] != previous_completion
    assert state.data["last_successfully_completed_run"] == previous_completion


def test_playable_schedule_remains_an_available_completed_schedule(monkeypatch, tmp_path):
    result, state, calendar, mailer = _run(monkeypatch, tmp_path, "Wednesday Team", [{"uid": "event"}])
    assert result and state.data["schedule_availability"]["status"] == "available"
    assert state.data["completed"]["hash"] == "pending-hash"
    assert calendar.calls == mailer.calls == 1


def test_playable_schedule_advances_completed_update(monkeypatch, tmp_path):
    result, state, _calendar, _mailer = _run(monkeypatch, tmp_path, "Wednesday Team", [{"uid": "event"}])
    assert result and state.data["last_successfully_completed_run"]


def test_readable_zero_game_schedule_is_healthy_without_notice(monkeypatch, tmp_path):
    result, state, calendar, mailer = _run(monkeypatch, tmp_path, "Wednesday league document", [])
    assert result and state.data["schedule_availability"]["status"] == "no_games_available"
    assert "publisher_message" not in state.data["schedule_availability"]
    assert not calendar.calls and not mailer.calls and "candidate" not in state.data


def test_pending_document_preserves_existing_completed_schedule(monkeypatch, tmp_path):
    result, state, calendar, mailer = _run(monkeypatch, tmp_path, "Wednesday schedule coming soon", [])
    state.data["completed"] = {"hash": "usable-hash", "source_url": "https://example/usable.pdf"}
    state.save()
    document = DownloadedPDF("https://example/pending.pdf", Path("pending.pdf"), "new-pending-hash")
    monkeypatch.setattr(main, "_pdf_text", lambda _path: "Wednesday schedule coming soon")
    parser = lambda _text: type("Parser", (), {"parse": lambda self: []})()
    calendar, mailer = Recorder(), Recorder()
    assert main.run(fetcher=Fetcher(document), parser_class=parser, calendar_factory=lambda: calendar,
                    mailer_factory=lambda **_kwargs: mailer, state=state)
    assert state.data["completed"]["hash"] == "usable-hash"
    assert not calendar.calls and not mailer.calls and "candidate" not in state.data


def test_pending_status_is_not_rendered_as_failure(monkeypatch):
    state = {"schedule_availability": {"status": "waiting_for_schedule", "publisher_message": "Schedule will be posted soon."}}
    history = {"revisions": [], "current_games": [], "analytics_games": [], "pool_observations": []}
    monkeypatch.setattr("ui._state", lambda: state)
    monkeypatch.setattr("ui._history", lambda: history)
    monkeypatch.setattr("ui._ui_settings", _settings)
    assert "Waiting for schedule" in _page() and "Monitor problem" not in _page()
    page = _history_view(state, history)
    assert "Last Failure" not in page and "Publisher note" in page and "Schedule will be posted soon." in page
