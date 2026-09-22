"""One-shot, resumable volleyball schedule monitor."""
from __future__ import annotations
import logging, re, sys, time
from pdfminer.high_level import extract_text as _extract_pdf_text
from src.env import load_env

_PUBLISHER_NOTICE = re.compile(
    r"(?:schedule\s+(?:will\s+be|to\s+be)\s+posted|schedule\s+coming(?:\s+soon)?)",
    re.IGNORECASE,
)
_NO_GAMES_NOTICE = re.compile(r"\b(?:no\s+(?:games?|matches?)\s+(?:are\s+)?(?:scheduled|available)|league\s+is\s+not\s+playing)\b", re.IGNORECASE)

def _pdf_text(path):
    # pdfminer separates pages with a form feed; the former page-by-page
    # extractor joined pages with newlines.
    return _extract_pdf_text(str(path)).replace("\f", "\n")

def _publisher_notice(text):
    """Return the readable line containing an obvious schedule-publisher notice."""
    for line in (line.strip() for line in text.splitlines()):
        if _PUBLISHER_NOTICE.search(line):
            return line
    match = _PUBLISHER_NOTICE.search(text)
    return text[max(0, text.rfind("\n", 0, match.start()) + 1):text.find("\n", match.end()) if "\n" in text[match.end():] else len(text)].strip() if match else None

def _no_games_notice(text):
    """Return an explicit publisher statement that no games are intentional."""
    for line in (line.strip() for line in text.splitlines()):
        if _NO_GAMES_NOTICE.search(line):
            return line
    return None

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
        events = parser.parse()
        diagnostics = getattr(parser, "diagnostics", None)
        if diagnostics:
            logger.info("schedule parser diagnostics: %s", diagnostics)
        return events
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
                state.schedule_discovery_failed(RuntimeError("website reachable but no schedule link was discovered"))
                return False
            schedule_matches, readable_candidates = [], []
            wanted_text = normalize_text(settings["schedule_match_text"])
            for url in urls:
                try:
                    downloaded = fetcher.download(url)
                    pdf_text = _pdf_text(downloaded.path)
                    readable_candidates.append((downloaded, pdf_text))
                except Exception as exc:
                    logger.warning("Rejected schedule candidate %s: %s", url, exc)
                    continue
                if wanted_text in normalize_text(pdf_text):
                    schedule_matches.append((downloaded, pdf_text))
                else:
                    logger.info("Valid schedule candidate does not match schedule text %r: %s", settings["schedule_match_text"], downloaded.url)
            if not schedule_matches:
                notices = [(item, _publisher_notice(text)) for item, text in readable_candidates]
                notices = [(item, notice) for item, notice in notices if notice]
                if len(notices) == 1:
                    downloaded, notice = notices[0]
                    state.schedule_unavailable("waiting_for_schedule", downloaded.url, notice)
                    logger.info("current schedule document is waiting for game data: %s", downloaded.url)
                    return True
                if len(readable_candidates) == 1:
                    downloaded, pdf_text = readable_candidates[0]
                    try:
                        parsed_events = parse_schedule(pdf_text)
                    except Exception as exc:
                        state.set_failure("parse", exc); logger.exception("parsing failed"); return False
                    if not parsed_events:
                        notice = _publisher_notice(pdf_text)
                        no_games = _no_games_notice(pdf_text)
                        if notice or no_games:
                            state.schedule_unavailable("waiting_for_schedule" if notice else "no_games_available", downloaded.url, notice or no_games)
                            logger.info("readable schedule document explicitly has no games")
                            return True
                logger.info("No published schedule PDF matched schedule text %r.", settings["schedule_match_text"])
                state.schedule_discovery_failed(RuntimeError("website reachable but no current schedule document matched the configured league"))
                return False
            # At this point the response was a validated PDF whose text names
            # the configured league.  Parsing is a distinct later stage.
            state.schedule_document_found()
            if len(schedule_matches) == 1:
                downloaded, pdf_text = schedule_matches[0]
            else:
                try:
                    secondary = [(item, parse_schedule(item[1])) for item in schedule_matches]
                except Exception as exc:
                    state.set_failure("parse", exc); logger.exception("parsing failed"); return False
                secondary = [(item, events) for item, events in secondary if events]
                if len(secondary) != 1:
                    if not secondary:
                        downloaded, pdf_text = schedule_matches[0]
                        parsed_events = []
                    else:
                        logger.error("%d PDFs match schedule text %r; team aliases did not identify exactly one candidate", len(schedule_matches), settings["schedule_match_text"])
                        state.schedule_discovery_failed(RuntimeError("multiple current schedule documents matched; unable to select one"))
                        return False
                (downloaded, pdf_text), parsed_events = secondary[0]
                logger.warning("Multiple PDFs match schedule text; selected %s using team aliases", downloaded.url)
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
                    text = _pdf_text(path)
                    notice = _publisher_notice(text)
                    no_games = _no_games_notice(text)
                    if notice or no_games:
                        state.discard_candidate()
                        state.schedule_unavailable("waiting_for_schedule" if notice else "no_games_available", candidate.get("source_url"), notice or no_games)
                        logger.info("current schedule candidate explicitly has no games")
                        return True
                    message = "structured schedule document produced zero events for configured team aliases"
                    state.parse_unavailable(message)
                    logger.error("%s; hash=%s source=%s aliases=%s", message, candidate.get("hash"), candidate.get("source_url"), settings["team_names"])
                    return False
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
