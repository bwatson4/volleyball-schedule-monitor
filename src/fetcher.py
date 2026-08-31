"""Bounded schedule-link discovery and streaming PDF downloads."""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from config import HEADERS, HTTP_TIMEOUT_SECONDS, PDF_MAX_BYTES

LOG = logging.getLogger("schedule_monitor.fetcher")
MAX_CANDIDATES = 12


@dataclass
class DownloadedPDF:
    url: str
    path: Path
    digest: str


def _safe_http_url(url: str) -> bool:
    """Reject non-web and obviously local targets before requests follows them."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved)


class PDFFetcher:
    def __init__(self, pdf_dir, keyword="schedule", page_url="https://kvapack.ca/adult-indoor/", session=None, timeout=HTTP_TIMEOUT_SECONDS, max_bytes=PDF_MAX_BYTES):
        self.pdf_dir, self.keyword, self.page_url = Path(pdf_dir), keyword, page_url
        self.session, self.timeout, self.max_bytes = session or requests.Session(), timeout, max_bytes

    def get_schedule_urls(self) -> list[str]:
        """Return unique schedule-like anchors in page order, never trusting a suffix."""
        response = self.session.get(self.page_url, headers=HEADERS, timeout=self.timeout)
        try:
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
        finally:
            response.close()

        urls: list[str] = []
        for anchor in soup.find_all("a", href=True):
            # KVA's WordPress markup may put the visible phrase beside an emoji
            # link, so include a small parent context without raw-HTML matching.
            own_context = " ".join(filter(None, [anchor.get_text(" ", strip=True), anchor.get("aria-label"), anchor.get("title")]))
            own_lowered = own_context.casefold()
            if "standing" in own_lowered or "registration" in own_lowered:
                continue
            parent_text = anchor.parent.get_text(" ", strip=True) if anchor.parent and anchor.parent.name not in {"[document]", "html", "body"} else ""
            context = own_context if "schedule" in own_lowered else parent_text
            if "schedule" not in context.casefold():
                continue
            resolved = urljoin(self.page_url, anchor["href"])
            if not _safe_http_url(resolved):
                LOG.warning("Rejected unsafe schedule candidate URL: %s", resolved)
                continue
            if resolved not in urls:
                urls.append(resolved)
            if len(urls) >= MAX_CANDIDATES:
                LOG.warning("Schedule candidate limit reached (%d)", MAX_CANDIDATES)
                break
        LOG.info("KVA page reachable; found %d schedule-link candidate(s)", len(urls))
        return urls

    # Kept for callers that only need the first candidate.
    def get_pdf_url(self):
        urls = self.get_schedule_urls()
        return urls[0] if urls else None

    def download(self, url: str) -> DownloadedPDF:
        if not _safe_http_url(url):
            raise ValueError("unsafe schedule candidate URL")
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        response = self.session.get(url, headers=HEADERS, timeout=self.timeout, stream=True, allow_redirects=True)
        temporary = None
        try:
            response.raise_for_status()
            final_url = str(response.url)
            if not _safe_http_url(final_url):
                raise ValueError("unsafe schedule redirect target")
            length = response.headers.get("Content-Length")
            if length and int(length) > self.max_bytes:
                raise ValueError("schedule PDF exceeds configured size limit")
            digest, total, first = hashlib.sha256(), 0, b""
            fd, temporary = tempfile.mkstemp(prefix="schedule-", suffix=".pdf.tmp", dir=self.pdf_dir)
            with open(fd, "wb", closefd=True) as output:
                for chunk in response.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    if not first:
                        first = chunk[:8]
                    total += len(chunk)
                    if total > self.max_bytes:
                        raise ValueError("schedule PDF exceeds configured size limit")
                    digest.update(chunk)
                    output.write(chunk)
            # The signature is authoritative: WordPress/document hosts can send
            # an inaccurate Content-Type. HTML without a PDF signature is not.
            if not first.startswith(b"%PDF-"):
                content_type = response.headers.get("Content-Type", "unknown").lower()
                if "html" in content_type:
                    raise ValueError("schedule download is HTML, not a PDF")
                raise ValueError("schedule download does not have a PDF signature")
            final = self.pdf_dir / f"{digest.hexdigest()}.pdf"
            Path(temporary).replace(final)
            temporary = None
            LOG.info("Accepted schedule PDF: %s", final_url)
            return DownloadedPDF(final_url, final, digest.hexdigest())
        finally:
            if temporary:
                Path(temporary).unlink(missing_ok=True)
            response.close()
