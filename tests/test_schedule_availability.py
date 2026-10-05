from pathlib import Path

import pytest

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
    monkeypatch.setattr(main, "_pdf_text", lambda _path: "Wednesday: no games are scheduled this week")
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


def test_explicit_no_games_document_is_healthy(monkeypatch, tmp_path):
    result, state, calendar, mailer = _run(monkeypatch, tmp_path, "Wednesday: no games are scheduled this week", [])
    assert result and state.data["schedule_availability"]["status"] == "no_games_available"
    assert "no games" in state.data["schedule_availability"]["publisher_message"]
    assert not calendar.calls and not mailer.calls and "candidate" not in state.data


def test_structured_zero_event_document_is_retained_as_a_parse_failure(monkeypatch, tmp_path):
    result, state, calendar, mailer = _run(monkeypatch, tmp_path,
        "Wednesday\nSeptember 23, 2026\nGym\nA POOL\n1 Another Team 7:00-8:30", [])
    assert not result and state.data["last_failure"]["stage"] == "parse"
    assert state.data["candidate"]["hash"] == "pending-hash" and state.data["candidate"]["unparsed"]
    assert state.data["schedule_availability"]["status"] == "available"
    assert not calendar.calls and not mailer.calls


def test_failed_hash_is_detected_once_and_successful_retry_completes(monkeypatch, tmp_path):
    monkeypatch.setattr("src.settings.load", _settings)
    path = tmp_path / "current.pdf"; path.touch()
    document = DownloadedPDF("https://example/current.pdf", path, "pending-hash")
    monkeypatch.setattr(main, "_pdf_text", lambda _path: "Wednesday\nSeptember 23, 2026\nGym\nA POOL\n1 Another Team 7:00-8:30")
    results = iter([[], [{"uid": "event"}]])
    parser = lambda _text: type("Parser", (), {"parse": lambda self: next(results)})()
    detected = []
    history = type("History", (), {"detect": lambda self, *args: detected.append(args),
        "record_events": lambda self, *args: None, "record_stage": lambda self, *args: None})()
    calendar, mailer, state = Recorder(), Recorder(), ScheduleState(tmp_path / "state.json")
    state.data["completed"] = {"hash": "sep-16-known-good", "source_url": "https://example/sep16.pdf"}; state.save()
    assert not main.run(fetcher=Fetcher(document), parser_class=parser, calendar_factory=lambda: calendar,
                        mailer_factory=lambda **_kwargs: mailer, state=state, history_factory=lambda _path: history)
    assert state.data["completed"]["hash"] == "sep-16-known-good"
    assert main.run(fetcher=Fetcher(document), parser_class=parser, calendar_factory=lambda: calendar,
                    mailer_factory=lambda **_kwargs: mailer, state=state, history_factory=lambda _path: history)
    assert len(detected) == 1 and calendar.calls == mailer.calls == 1
    assert state.data["completed"]["hash"] == "pending-hash" and "last_failure" not in state.data


@pytest.mark.parametrize("same_url", [False, True])
@pytest.mark.parametrize("raises", [False, True])
def test_fresh_discovery_supersedes_unresolved_pdf(monkeypatch, tmp_path, same_url, raises):
    monkeypatch.setattr("src.settings.load", _settings)
    a_path, b_path = tmp_path / "a.pdf", tmp_path / "b.pdf"
    a_path.touch(); b_path.touch()
    a = DownloadedPDF("https://example/a.pdf", a_path, "hash-a")
    b = DownloadedPDF(a.url if same_url else "https://example/b.pdf", b_path, "hash-b")
    operations, detected = [], []

    class CurrentFetcher(Fetcher):
        def get_schedule_urls(self):
            operations.append(("scan", self.document.digest))
            return super().get_schedule_urls()

        def download(self, url):
            operations.append(("download", self.document.digest))
            return super().download(url)

    monkeypatch.setattr(main, "_pdf_text", lambda path: "Wednesday " + Path(path).stem)

    class Parser:
        def __init__(self, text): self.text = text
        def parse(self):
            name = self.text.split()[-1]
            operations.append(("parse", "hash-" + name))
            if name == "a":
                if raises: raise ValueError("unreadable schedule structure")
                return []
            return [{"uid": "event"}]

    history = type("History", (), {"detect": lambda self, *args: detected.append(args),
        "record_events": lambda self, *args: None, "record_stage": lambda self, *args: None})()
    state = ScheduleState(tmp_path / "state.json")
    state.data.update(completed={"hash": "known-good"}, last_successful_parsed="old-parse",
                      last_successfully_completed_run="old-update")
    state.save()
    calendar, mailer = Recorder(), Recorder()
    fetcher = CurrentFetcher(a)
    kwargs = dict(fetcher=fetcher, parser_class=Parser, state=state,
                  calendar_factory=lambda: calendar, mailer_factory=lambda **_: mailer,
                  history_factory=lambda _: history)
    for _ in range(2):
        assert not main.run(**kwargs)
        assert state.data["completed"]["hash"] == "known-good"
        assert state.data["last_successful_parsed"] == "old-parse"
        assert state.data["last_successfully_completed_run"] == "old-update"
        assert state.data["last_failure"]["stage"] == "parse"
        assert not calendar.calls and not mailer.calls
    assert len(detected) == 1
    fetcher.document = b
    operations.clear()
    assert main.run(**kwargs)
    assert operations == [("scan", "hash-b"), ("download", "hash-b"), ("parse", "hash-b")]
    assert state.data["completed"]["hash"] == "hash-b"
    assert len(detected) == 2 and calendar.calls == mailer.calls == 1


def test_successful_scan_advances_during_repeated_parse_failure(monkeypatch, tmp_path):
    monkeypatch.setattr("src.settings.load", _settings)
    path = tmp_path / "a.pdf"; path.touch()
    document = DownloadedPDF("https://example/a.pdf", path, "hash-a")
    monkeypatch.setattr(main, "_pdf_text", lambda _: "Wednesday structured schedule")
    parser = lambda _: type("Parser", (), {"parse": lambda self: []})()
    state = ScheduleState(tmp_path / "state.json")
    for timestamp in ["2026-09-30T12:00:00+00:00", "2026-10-05T14:32:00+00:00"]:
        monkeypatch.setattr("src.state.now", lambda: timestamp)
        assert not main.run(fetcher=Fetcher(document), parser_class=parser, state=state)
        assert state.data["last_website_scan"] == timestamp
        assert state.data["last_schedule_document_found"] == timestamp
        assert state.data["last_failure"]["stage"] == "parse"
        assert "last_successful_parsed" not in state.data
        assert "last_successfully_completed_run" not in state.data
    history = {"revisions": [], "current_games": [], "analytics_games": [], "pool_observations": []}
    page = _history_view(state.data, history)
    assert "Last website scan" in page and "Last successful parse" in page
    assert "Last completed schedule update" in page and "Last Failure" in page
    monkeypatch.setattr("ui._state", lambda: state.data)
    monkeypatch.setattr("ui._history", lambda: history)
    monkeypatch.setattr("ui._ui_settings", _settings)
    assert "Monitor problem" in _page()


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
