from pathlib import Path

import main
from src.fetcher import DownloadedPDF
from src.state import ScheduleState


class BrokenHistory:
    def __init__(self, *_args): pass
    def __getattr__(self, _name):
        return lambda *_args: (_ for _ in ()).throw(OSError("history unavailable"))


def test_history_write_failure_does_not_block_resumable_core_work(monkeypatch, tmp_path):
    monkeypatch.setattr("src.settings.load", lambda: {"team_names": ["Team"], "schedule_match_text": "Wednesday", "gyms": ["Gym"], "email_recipients": ["a@b.com"]})
    downloaded = DownloadedPDF("https://example/schedule", Path("schedule"), "new-hash")
    fetcher = type("Fetcher", (), {"get_schedule_urls": lambda self: [downloaded.url], "download": lambda self, _url: downloaded})()
    monkeypatch.setattr(main, "_pdf_text", lambda _path: "Wednesday Team")
    parser = lambda _text: type("Parser", (), {"parse": lambda self: [{"uid": "x", "source_team": "Team", "date": "2026-09-01", "summary": "Team", "description": "", "start": __import__("datetime").datetime(2026, 9, 1, 19), "end": __import__("datetime").datetime(2026, 9, 1, 20)}]})()
    sink = type("Sink", (), {"add_or_update_events": lambda self, _events: None, "send": lambda self, _events: None})()
    assert main.run(fetcher=fetcher, parser_class=parser, calendar_factory=lambda: sink,
                    mailer_factory=lambda **_kwargs: sink, state=ScheduleState(tmp_path / "state.json"), history_factory=BrokenHistory)
