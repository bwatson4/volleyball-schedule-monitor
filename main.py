"""One-shot, resumable volleyball schedule monitor."""
from __future__ import annotations
import logging, sys, time
from pdfminer.high_level import extract_text as _extract_pdf_text
from src.env import load_env

def _pdf_text(path):
    # pdfminer separates pages with a form feed; the former page-by-page
    # extractor joined pages with newlines.
    return _extract_pdf_text(str(path)).replace("\f", "\n")

def run(fetcher=None, parser_class=None, calendar_factory=None, mailer_factory=None, state=None, history_factory=None):
    from config import HISTORY_FILE, KEYWORD, PAGE_URL, PDF_DIR, STATE_FILE
    from src.fetcher import PDFFetcher
    from src.parser import ScheduleParser
    from src.calendar import CalendarManager
    from src.emailer import EmailSender
    from src.state import ScheduleState
    from src.history import HistoryStore
    from src.settings import normalize_text
    logger, started = logging.getLogger("schedule_monitor"), time.monotonic()
    from src.settings import load as load_settings
    settings = load_settings()
    state = state or ScheduleState(STATE_FILE); state.run_started(); candidate = state.data.get("candidate", {})
    try:
        history = (history_factory or HistoryStore)(HISTORY_FILE)
    except Exception:
        # History is observability, while JSON state is the crash-recovery source
        # of truth. A history failure must not strand a retryable candidate.
        logger.exception("history database unavailable; continuing without analytics")
        history = None
    def history_write(method, *args):
        if history is None:
            return
        try:
            getattr(history, method)(*args)
        except Exception:
            logger.exception("history database write failed; core processing continues")
    def parse_schedule(pdf_text):
        parser = (parser_class or (lambda text: ScheduleParser(text, team_names=settings["team_names"], gyms=settings["gyms"] )))(pdf_text)
        return parser.parse()
    logger.info("run start; last completed=%s", state.data.get("last_successfully_completed_run"))
    try:
        # An incomplete candidate is processed from its durable copy first, even if the site is down.
        path = candidate.get("pdf_path") if candidate and not state.complete_if_ready() else None
        if path and not __import__("pathlib").Path(path).exists(): path = None
        parsed_events = None
        if not path:
            fetcher = fetcher or PDFFetcher(PDF_DIR, KEYWORD, PAGE_URL)
            state.website_scanned()
            urls = fetcher.get_schedule_urls()
            # An attempted scan is not proof of connectivity; only a returned
            # candidate list establishes website/download recovery.
            state.mark_website_success()
            if not urls:
                logger.info("No published schedule PDF found.")
                return True
            schedule_matches = []
            wanted_text = normalize_text(settings["schedule_match_text"])
            for url in urls:
                try:
                    downloaded = fetcher.download(url)
                    pdf_text = _pdf_text(downloaded.path)
                except Exception as exc:
                    logger.warning("Rejected schedule candidate %s: %s", url, exc)
                    continue
                if wanted_text in normalize_text(pdf_text):
                    schedule_matches.append((downloaded, pdf_text))
                else:
                    logger.info("Valid schedule candidate does not match schedule text %r: %s", settings["schedule_match_text"], downloaded.url)
            if not schedule_matches:
                logger.info("No published schedule PDF matched schedule text %r.", settings["schedule_match_text"])
                return True
            if len(schedule_matches) == 1:
                downloaded, pdf_text = schedule_matches[0]
                parsed_events = parse_schedule(pdf_text)
            else:
                secondary = [(item, parse_schedule(item[1])) for item in schedule_matches]
                secondary = [(item, events) for item, events in secondary if events]
                if len(secondary) != 1:
                    logger.error("%d PDFs match schedule text %r; team aliases did not identify exactly one candidate", len(schedule_matches), settings["schedule_match_text"])
                    return True
                (downloaded, pdf_text), parsed_events = secondary[0]
                logger.warning("Multiple PDFs match schedule text; selected %s using team aliases", downloaded.url)
            if not parsed_events:
                logger.info("Configured team not found in selected schedule PDF: %s", downloaded.url)
                return True
            if state.data.get("completed", {}).get("hash") == downloaded.digest:
                logger.info("schedule unchanged and complete; source URL may have changed")
                return True
            candidate = state.begin_candidate(downloaded.digest, downloaded.path, downloaded.url); path = str(downloaded.path)
            history_write("detect", candidate["hash"], candidate["detected_at"], candidate["source_url"])
            logger.info("schedule change detected hash=%s", candidate["hash"])
        if not candidate.get("parsed"):
            try:
                parsed_events = parsed_events if parsed_events is not None else parse_schedule(_pdf_text(path))
                if not parsed_events:
                    raise ValueError("configured team not found in schedule candidate")
                state.mark_stage("parsed"); candidate = state.data["candidate"]
                history_write("record_events", candidate["hash"], parsed_events, state.data["last_successful_parsed"])
                logger.info("parsing succeeded; events=%d", len(parsed_events))
            except Exception as exc: state.set_failure("parse", exc); logger.exception("parsing failed"); return False
        # Datetimes are retained only during this invocation; reparse before deferred calendar work.
        parsed_events = parsed_events if parsed_events is not None else parse_schedule(_pdf_text(path))
        if not candidate.get("calendar"):
            try:
                (calendar_factory or CalendarManager)().add_or_update_events(parsed_events)
                state.mark_stage("calendar"); candidate = state.data["candidate"]
                history_write("record_stage", candidate["hash"], "calendar", state.data["last_successful_calendar"])
                logger.info("calendar succeeded")
            except Exception as exc: state.set_failure("calendar", exc); logger.exception("calendar failed"); return False
        if not candidate.get("email"):
            try:
                (mailer_factory or (lambda **kwargs: EmailSender(to_addresses=settings["email_recipients"], **kwargs)))(pdf_path=path).send(parsed_events)
                state.mark_stage("email"); candidate = state.data["candidate"]
                history_write("record_stage", candidate["hash"], "email", state.data["last_successful_email"])
                logger.info("email succeeded")
            except Exception as exc: state.set_failure("email", exc); logger.exception("email failed"); return False
        completed = state.complete_if_ready()
        if completed:
            history_write("record_stage", candidate["hash"], "completed", state.data["last_successfully_completed_run"])
        logger.info("run finish; completed=%s duration=%.2fs", completed, time.monotonic()-started); return completed
    except Exception as exc:
        state.set_failure("website/download", exc); logger.exception("run failed"); return False

def main():
    try:
        load_env(); from utils import configure_logging; configure_logging()
        return 0 if run() else 1
    except Exception as exc:
        logging.basicConfig(level=logging.INFO); logging.getLogger("schedule_monitor").exception("fatal configuration failure: %s", exc); return 1

if __name__ == "__main__": sys.exit(main())
