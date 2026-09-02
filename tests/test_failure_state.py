from pathlib import Path

import main
from src.fetcher import DownloadedPDF
from src.state import ScheduleState


def settings():
    return {"team_names": ["Team"], "schedule_match_text": "Wednesday", "gyms": ["Gym"], "email_recipients": ["a@example.com"]}


class Fetcher:
    def __init__(self, documents):
        self.documents = documents
        self.downloads = []

    def get_schedule_urls(self):
        return list(self.documents)

    def download(self, url):
        self.downloads.append(url)
        return self.documents[url]


class FailingFetcher:
    def get_schedule_urls(self):
        raise OSError("DNS unavailable")


class Sink:
    def __init__(self, failure=None):
        self.failure = failure
        self.calls = 0

    def add_or_update_events(self, _events):
        self.calls += 1
        if self.failure:
            raise self.failure

    def send(self, _events):
        self.calls += 1
        if self.failure:
            raise self.failure


def parser(events=True, failure=None):
    def factory(_text):
        class Parser:
            def parse(self):
                if failure:
                    raise failure
                return [{"uid": "event"}] if events else []
        return Parser()
    return factory


def setup(monkeypatch, tmp_path, documents):
    monkeypatch.setattr("src.settings.load", settings)
    monkeypatch.setattr(main, "_pdf_text", lambda _path: "Wednesday Team")
    return Fetcher(documents), ScheduleState(tmp_path / "state.json")


def selected_document(tmp_path):
    path = tmp_path / "schedule.pdf"
    path.touch()
    return DownloadedPDF("https://example/schedule", path, "digest")


def failure(state, stage):
    state.set_failure(stage, RuntimeError(f"{stage} failed"))


def test_website_failure_clears_after_empty_link_result(monkeypatch, tmp_path):
    fetcher, state = setup(monkeypatch, tmp_path, {})
    failure(state, "website/download")
    assert main.run(fetcher=fetcher, state=state)
    assert "last_failure" not in state.data
    assert state.data["last_website_scan"]
    assert state.data["last_attempted_run"]


def test_website_failure_clears_during_candidate_processing(monkeypatch, tmp_path):
    document = selected_document(tmp_path)
    fetcher, state = setup(monkeypatch, tmp_path, {document.url: document})
    failure(state, "website/download")
    assert main.run(fetcher=fetcher, parser_class=parser(events=False), state=state)
    assert fetcher.downloads == [document.url]
    assert "last_failure" not in state.data


def test_website_failure_clears_during_nonmatching_candidate_processing(monkeypatch, tmp_path):
    document = selected_document(tmp_path)
    fetcher, state = setup(monkeypatch, tmp_path, {document.url: document})
    monkeypatch.setattr(main, "_pdf_text", lambda _path: "Monday Team")
    failure(state, "website/download")
    assert main.run(fetcher=fetcher, state=state)
    assert fetcher.downloads == [document.url]
    assert "last_failure" not in state.data


def test_new_website_failure_replaces_previous_failure(monkeypatch, tmp_path):
    _fetcher, state = setup(monkeypatch, tmp_path, {})
    failure(state, "website/download")
    first = state.data["last_failure"]
    assert not main.run(fetcher=FailingFetcher(), state=state)
    latest = state.data["last_failure"]
    assert latest["stage"] == "website/download"
    assert latest["message"] == "DNS unavailable"
    assert latest != first


def test_parse_failure_is_not_cleared_by_website_success(monkeypatch, tmp_path):
    fetcher, state = setup(monkeypatch, tmp_path, {})
    failure(state, "parse")
    assert main.run(fetcher=fetcher, state=state)
    assert state.data["last_failure"]["stage"] == "parse"


def test_parse_failure_clears_after_successful_parse(monkeypatch, tmp_path):
    document = selected_document(tmp_path)
    fetcher, state = setup(monkeypatch, tmp_path, {document.url: document})
    failure(state, "parse")
    assert not main.run(fetcher=fetcher, parser_class=parser(), calendar_factory=lambda: Sink(RuntimeError("calendar down")), state=state)
    # Calendar failure replaces the old parse failure only after parsing has
    # completed, proving the parse recovery was recorded first.
    assert state.data["last_failure"]["stage"] == "calendar"


def test_calendar_failure_remains_until_calendar_succeeds(monkeypatch, tmp_path):
    document = selected_document(tmp_path)
    fetcher, state = setup(monkeypatch, tmp_path, {document.url: document})
    failure(state, "calendar")
    assert not main.run(fetcher=fetcher, parser_class=parser(), calendar_factory=lambda: Sink(RuntimeError("calendar down")), state=state)
    assert state.data["last_failure"]["stage"] == "calendar"
    assert main.run(parser_class=parser(), calendar_factory=Sink, mailer_factory=lambda **_: Sink(), state=state)
    assert "last_failure" not in state.data


def test_email_failure_remains_until_email_succeeds(monkeypatch, tmp_path):
    document = selected_document(tmp_path)
    fetcher, state = setup(monkeypatch, tmp_path, {document.url: document})
    failure(state, "email")
    assert not main.run(fetcher=fetcher, parser_class=parser(), calendar_factory=Sink, mailer_factory=lambda **_: Sink(RuntimeError("email down")), state=state)
    assert state.data["last_failure"]["stage"] == "email"
    assert main.run(parser_class=parser(), calendar_factory=Sink, mailer_factory=lambda **_: Sink(), state=state)
    assert "last_failure" not in state.data


def test_candidate_completion_clears_any_final_failure(monkeypatch, tmp_path):
    document = selected_document(tmp_path)
    fetcher, state = setup(monkeypatch, tmp_path, {document.url: document})
    failure(state, "website/download")
    assert main.run(fetcher=fetcher, parser_class=parser(), calendar_factory=Sink, mailer_factory=lambda **_: Sink(), state=state)
    assert "last_failure" not in state.data
    assert state.data["last_successfully_completed_run"]
