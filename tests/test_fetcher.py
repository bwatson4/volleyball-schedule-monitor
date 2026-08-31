from unittest.mock import MagicMock

import pytest
import requests

from src.fetcher import PDFFetcher


def response(chunks=(), headers=None, url="https://kvapack.ca/uploads/schedule", text="", error=None):
    value = MagicMock()
    value.headers = headers or {}
    value.iter_content.return_value = chunks
    value.url = url
    value.text = text
    value.raise_for_status.side_effect = error
    return value


def test_schedule_links_are_semantic_resolved_and_deduplicated(tmp_path):
    page = response(text='''
      <a href="/uploads/monday">Schedule - Click Here</a>
      <a href="/uploads/monday">Schedule - Click Here</a>
      <a href="https://cdn.example.org/tuesday?download=1">Tuesday schedule</a>
      <a href="/uploads/standings.pdf">League Standings - Click Here</a>
      <a href="javascript:alert(1)">Schedule</a>
    ''')
    session = MagicMock(); session.get.return_value = page
    urls = PDFFetcher(tmp_path, page_url="https://kvapack.ca/adult-indoor/", session=session).get_schedule_urls()
    assert urls == ["https://kvapack.ca/uploads/monday", "https://cdn.example.org/tuesday?download=1"]
    page.close.assert_called_once()


def test_schedule_link_context_can_identify_wordpress_adjacent_anchor(tmp_path):
    page = response(text='<p><a href="/uploads/wed"><img alt="calendar"></a> Schedule - Click Here</p>')
    session = MagicMock(); session.get.return_value = page
    assert PDFFetcher(tmp_path, session=session).get_schedule_urls() == ["https://kvapack.ca/uploads/wed"]


def test_download_rejects_html_and_oversized_content_but_accepts_pdf_with_poor_content_type(tmp_path):
    session = MagicMock()
    session.get.return_value = response([b"<html>no</html>"], {"Content-Type": "text/html"})
    with pytest.raises(ValueError, match="HTML"):
        PDFFetcher(tmp_path, session=session).download("https://kvapack.ca/uploads/x")
    session.get.return_value = response([b"%PDF-1.4", b"x" * 20], {"Content-Type": "application/pdf"})
    with pytest.raises(ValueError, match="size"):
        PDFFetcher(tmp_path, session=session, max_bytes=10).download("https://kvapack.ca/uploads/x")
    session.get.return_value = response([b"%PDF-1.4 valid"], {"Content-Type": "text/plain"}, url="https://cdn.example.org/document?id=4")
    item = PDFFetcher(tmp_path, session=session).download("https://kvapack.ca/uploads/x")
    assert item.path.read_bytes().startswith(b"%PDF-") and item.url.endswith("id=4")


def test_broken_candidate_and_http_page_failure_are_bounded(tmp_path):
    session = MagicMock()
    session.get.return_value = response(error=requests.Timeout("offline"))
    with pytest.raises(requests.Timeout):
        PDFFetcher(tmp_path, session=session).get_schedule_urls()
    session.get.return_value = response(error=requests.HTTPError("404"))
    with pytest.raises(requests.HTTPError):
        PDFFetcher(tmp_path, session=session).download("https://kvapack.ca/uploads/missing")


@pytest.mark.parametrize("url", ["file:///tmp/x", "javascript:alert(1)", "http://localhost/x", "http://127.0.0.1/x", "http://169.254.1.1/x"])
def test_unsafe_candidate_targets_are_rejected_before_request(tmp_path, url):
    session = MagicMock()
    with pytest.raises(ValueError, match="unsafe"):
        PDFFetcher(tmp_path, session=session).download(url)
    session.get.assert_not_called()
