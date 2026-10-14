"""Publisher-context selection using real Pi archive PDFs, without networking."""
import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import main
from src.fetcher import PDFFetcher
from src.history import HistoryStore
from src.state import ScheduleState
from tests.test_fetcher import response

ARCHIVE = Path(__file__).parent / "fixtures/pi-archive"
WED_OLD = ARCHIVE / "ef8ce401db876eef9e111f108b82996e10b30a64cfdea35e27aa0a2914c9fea9.pdf"
WED_NEW = ARCHIVE / "4b3823e479fbcc778d160739ccaf3b2f8723a321ad3068fbc6379a6ba7d94cf8.pdf"
MONDAY = ARCHIVE / "6d1e89814b8fb86a9e8de7f808de7a5b2e55a9ba978cc77061e7f0e8ccef781f.pdf"
LADIES = ARCHIVE / "9be05905e14c77bb12245178006f7d7183a68b8c6e99f26f4419cc11b62c83fb.pdf"


@pytest.mark.parametrize("order", [("Monday", "Tuesday", "Wednesday"), ("Wednesday", "Monday", "Tuesday"), ("Tuesday", "Wednesday", "Monday")])
def test_click_here_links_only_download_from_configured_league(tmp_path, order):
    html = "".join(f'<h2>{day} Night</h2><p>Schedule - <a href="/{day.lower()}">Click Here</a></p>' for day in order)
    session = MagicMock(); session.get.return_value = response(text=html)
    fetcher = PDFFetcher(tmp_path, keyword="wednesday night", page_url="https://publisher.example/leagues", session=session)
    assert fetcher.get_schedule_urls() == ["https://publisher.example/wednesday"]


def test_nested_subheading_and_unlabelled_sibling_section_are_bounded(tmp_path):
    html = '<section><h2>Wednesday Night</h2><h3>Schedule</h3><a href="/wed">Weekly Schedule</a></section><section><a href="/unknown">Weekly Schedule</a></section>'
    session = MagicMock(); session.get.return_value = response(text=html)
    assert PDFFetcher(tmp_path, keyword="wednesday night", session=session).get_schedule_urls() == ["https://kvapack.ca/wed"]


def test_no_cross_league_fallback_when_wednesday_link_is_missing(tmp_path):
    session = MagicMock(); session.get.return_value = response(text='<h2>Monday Night</h2><a href="/mon">Weekly Schedule</a>')
    assert PDFFetcher(tmp_path, keyword="wednesday night", session=session).get_schedule_urls() == []


def test_two_wednesday_league_sections_require_more_precise_identity(tmp_path):
    html = '<h2>Wednesday Night Competitive</h2><a href="/comp">Weekly Schedule</a><h2>Wednesday Night Recreational</h2><a href="/rec">Schedule</a>'
    session = MagicMock(); session.get.return_value = response(text=html)
    with pytest.raises(ValueError, match="multiple publisher league sections"):
        PDFFetcher(tmp_path, keyword="wednesday night", session=session).get_schedule_urls()
    assert PDFFetcher(tmp_path, keyword="wednesday night recreational", session=session).get_schedule_urls() == ["https://kvapack.ca/rec"]


def test_unrelated_updates_cannot_supersede_or_notify_then_new_wednesday_recovers(monkeypatch, tmp_path):
    class Website:
        def __init__(self):
            self.pdfs = {"/m": MONDAY, "/t": LADIES, "/w": WED_OLD}
            self.requests = []
            self.order = ["Monday", "Tuesday", "Wednesday"]
        def get(self, url, **kwargs):
            self.requests.append(url)
            if url == "https://publisher.example/leagues":
                html = "".join(f'<h2>{day} Night</h2><p>Schedule - <a href="/{day[0].lower()}">Click Here</a></p>' for day in self.order)
                return response(text=html, url=url)
            path = self.pdfs["/" + url.rsplit("/", 1)[-1]]
            return response(chunks=[path.read_bytes()], headers={"Content-Type": "application/pdf"}, url=url)
    site = Website()
    fetcher = PDFFetcher(tmp_path / "pdfs", keyword="wednesday night", page_url="https://publisher.example/leagues", session=site)
    monkeypatch.setattr("src.settings.load", lambda: {"team_names": ["Chewblockas", "Chewblaccas"],
        "gyms": ["Pacway", "KCS", "TCC", "OLPH"], "schedule_match_text": "Wednesday", "email_recipients": ["test@example.com"]})
    def forbidden(*args, **kwargs):
        pytest.fail("Real calendar/email/network attempted")
    monkeypatch.setattr("src.calendar.CalendarManager", forbidden)
    monkeypatch.setattr("src.emailer.EmailSender", forbidden)
    monkeypatch.setattr("socket.socket.connect", forbidden)
    calls = []
    sink = type("Sink", (), {"add_or_update_events": lambda _, ev: calls.append(ev), "send": lambda _, ev: calls.append(ev)})()
    state = ScheduleState(tmp_path / "state.json")
    history = HistoryStore(tmp_path / "history.sqlite3")
    kwargs = dict(fetcher=fetcher, state=state, history_factory=lambda _: history,
                  calendar_factory=lambda: sink, mailer_factory=lambda **_: sink)
    assert main.run(**kwargs)
    first_hash = state.data["completed"]["hash"]
    assert len(calls) == 2
    site.pdfs["/m"] = WED_NEW  # Even wrong-day link content resembling our schedule must never be requested.
    site.order = ["Tuesday", "Wednesday", "Monday"]
    assert main.run(**kwargs)
    assert state.data["completed"]["hash"] == first_hash and len(calls) == 2
    site.pdfs["/w"] = WED_NEW
    assert main.run(**kwargs)
    assert main.run(**kwargs)
    assert len(calls) == 4
    assert state.data["completed"]["hash"] == hashlib.sha256(WED_NEW.read_bytes()).hexdigest()
    assert len(history.dashboard()["revisions"]) == 2
    assert set(site.requests) == {"https://publisher.example/leagues", "https://publisher.example/w"}
    assert len(list((tmp_path / "pdfs").glob("*.pdf"))) == 2


def test_conflicting_document_label_rejected_even_with_selector_in_footer(monkeypatch, tmp_path):
    from src.fetcher import DownloadedPDF
    path = tmp_path / "wrong.pdf"; path.touch()
    document = DownloadedPDF("https://example.com/wrong", path, "wrong-hash")
    fetcher = type("Fetcher", (), {"get_schedule_urls": lambda _: [document.url], "download": lambda _, url: document})()
    monkeypatch.setattr(main, "_pdf_text", lambda _: "Monday\nOctober 5, 2026\nChewblockas\nWednesday league reminder")
    monkeypatch.setattr("src.settings.load", lambda: {"team_names": ["Chewblockas"], "gyms": ["KCS"], "schedule_match_text": "Wednesday", "email_recipients": ["test@example.com"]})
    state = ScheduleState(tmp_path / "state.json")
    state.data["completed"] = {"hash": "known-good"}
    history = HistoryStore(tmp_path / "history.sqlite3")
    assert not main.run(fetcher=fetcher, state=state, history_factory=lambda _: history)
    assert state.data["completed"]["hash"] == "known-good"
    assert not history.dashboard()["revisions"]


def test_multiple_distinct_wednesday_documents_are_not_selected_by_team_alias(monkeypatch, tmp_path):
    from src.fetcher import DownloadedPDF
    absent = Path(__file__).parent / "fixtures/pdfs/publisher-125.pdf"
    documents = [DownloadedPDF("https://example.com/one", WED_NEW, WED_NEW.stem),
                 DownloadedPDF("https://example.com/two", absent, "absent-hash")]
    fetcher = type("Fetcher", (), {"get_schedule_urls": lambda _: [d.url for d in documents],
                                  "download": lambda _, url: next(d for d in documents if d.url == url)})()
    monkeypatch.setattr("src.settings.load", lambda: {"team_names": ["Chewblockas"], "gyms": ["KCS"], "schedule_match_text": "Wednesday", "email_recipients": ["test@example.com"]})
    state = ScheduleState(tmp_path / "state.json")
    state.data["completed"] = {"hash": "known-good"}
    history = HistoryStore(tmp_path / "history.sqlite3")
    assert not main.run(fetcher=fetcher, state=state, history_factory=lambda _: history)
    assert state.data["completed"]["hash"] == "known-good"
    assert "ambiguous" in state.data["last_failure"]["message"]
    assert not history.dashboard()["revisions"]
