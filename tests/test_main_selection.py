from pathlib import Path

import main
from src.fetcher import DownloadedPDF
from src.state import ScheduleState


def settings():
    return {"team_names": ["Example Team", "Example Team 2"], "schedule_match_text": "Wednesday", "gyms": ["Example Gym"], "email_recipients": ["player@example.com"]}


class Fetcher:
    def __init__(self, documents): self.documents = documents
    def get_schedule_urls(self): return list(self.documents)
    def download(self, url): return self.documents[url]


def event_parser(text):
    class Parser:
        def parse(self):
            return [{"uid": "stable-event", "summary": "Example Team Volleyball"}] if "Example Team" in text else []
    return Parser()


class Recorder:
    def __init__(self): self.calls = []
    def add_or_update_events(self, events): self.calls.append(events)
    def send(self, events): self.calls.append(events)


def configure(monkeypatch, documents, text_by_path):
    monkeypatch.setattr("src.settings.load", settings)
    monkeypatch.setattr(main, "_pdf_text", lambda path: text_by_path[str(path)])
    return Fetcher(documents)


def test_only_configured_league_pdf_is_processed(monkeypatch, tmp_path):
    monday = DownloadedPDF("https://x/monday", Path("monday"), "monday-hash")
    wednesday = DownloadedPDF("https://x/wednesday", Path("wednesday"), "wednesday-hash")
    fetcher = configure(monkeypatch, {monday.url: monday, wednesday.url: wednesday}, {"monday": "Monday Example Team", "wednesday": "Wednesday Example Team"})
    calendar, mailer = Recorder(), Recorder()
    assert main.run(fetcher=fetcher, parser_class=event_parser, calendar_factory=lambda: calendar, mailer_factory=lambda **_: mailer, state=ScheduleState(tmp_path / "state.json"))
    assert len(calendar.calls) == len(mailer.calls) == 1


def test_same_selected_hash_at_a_new_url_is_a_noop(monkeypatch, tmp_path):
    selected = DownloadedPDF("https://new-host.example/schedule", Path("wednesday"), "same-hash")
    fetcher = configure(monkeypatch, {selected.url: selected}, {"wednesday": "Wednesday Example Team"})
    state = ScheduleState(tmp_path / "state.json")
    state.data["completed"] = {"hash": "same-hash"}; state.save()
    calendar, mailer = Recorder(), Recorder()
    assert main.run(fetcher=fetcher, parser_class=event_parser, calendar_factory=lambda: calendar, mailer_factory=lambda **_: mailer, state=state)
    assert not calendar.calls and not mailer.calls


def test_revised_selected_schedule_hash_is_processed(monkeypatch, tmp_path):
    selected = DownloadedPDF("https://x/wednesday-revised", Path("wednesday"), "new-hash")
    fetcher = configure(monkeypatch, {selected.url: selected}, {"wednesday": "  wednesday\nExample Team"})
    state = ScheduleState(tmp_path / "state.json")
    state.data["completed"] = {"hash": "old-hash"}; state.save()
    calendar, mailer = Recorder(), Recorder()
    assert main.run(fetcher=fetcher, parser_class=event_parser, calendar_factory=lambda: calendar, mailer_factory=lambda **_: mailer, state=state)
    assert len(calendar.calls) == len(mailer.calls) == 1


def test_no_matching_league_or_ambiguous_secondary_match_is_a_clean_noop(monkeypatch, tmp_path):
    monday = DownloadedPDF("https://x/monday", Path("monday"), "a")
    fetcher = configure(monkeypatch, {monday.url: monday}, {"monday": "Monday Example Team"})
    assert main.run(fetcher=fetcher, parser_class=event_parser, state=ScheduleState(tmp_path / "none.json"))
    first = DownloadedPDF("https://x/wed-a", Path("wed-a"), "a")
    second = DownloadedPDF("https://x/wed-b", Path("wed-b"), "b")
    fetcher = configure(monkeypatch, {first.url: first, second.url: second}, {"wed-a": "Wednesday Example Team", "wed-b": "Wednesday Example Team 2"})
    assert main.run(fetcher=fetcher, parser_class=event_parser, state=ScheduleState(tmp_path / "ambiguous.json"))
