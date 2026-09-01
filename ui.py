"""Tiny LAN-only status/configuration UI using only the standard library."""
from __future__ import annotations

import html
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from src.env import load_env
from src.settings import load, save
from src.state import ScheduleState

VIEWS = {"home", "history", "settings"}

def _state():
    from config import STATE_FILE
    return ScheduleState(STATE_FILE).data

def _ui_settings():
    try:
        return load()
    except ValueError:
        return {"team_names": [], "schedule_match_text": "", "gyms": [], "email_recipients": []}

def _empty_history():
    return {"revisions": [], "games": [], "current_games": [], "analytics_games": [], "pool_observations": []}

def _history():
    try:
        from config import HISTORY_FILE
        if not HISTORY_FILE.exists(): return _empty_history()
        from src.history import HistoryStore
        return HistoryStore(HISTORY_FILE).dashboard()
    except Exception:
        return _empty_history()

def _esc(value): return html.escape(str(value or ""), quote=True)

def _fmt(value):
    if not value: return "—"
    try: return datetime.fromisoformat(value).astimezone().strftime("%b %-d, %Y %H:%M")
    except (ValueError, TypeError): return str(value)

def _time(value):
    try: return datetime.fromisoformat(value).strftime("%H:%M")
    except (ValueError, TypeError): return str(value or "")[-5:]

def _event_time(game):
    try: return datetime.fromisoformat(game["start_time"])
    except (ValueError, TypeError): return datetime.min

def _dashboard_model(history, current_time=None):
    """Separate current schedule, deduplicated analytics and observations."""
    current_time = current_time or datetime.now(timezone.utc).replace(tzinfo=None)
    current_games = sorted(history.get("current_games", history.get("games", [])), key=_event_time)
    analytics_games = sorted(history.get("analytics_games", history.get("games", [])), key=_event_time)
    current_upcoming = [game for game in current_games if _event_time(game) >= current_time]
    return {"current_games": current_games, "current_upcoming": current_upcoming,
            "next": current_upcoming[0] if current_upcoming else None,
            "analytics_games": analytics_games,
            "gyms": Counter(game.get("gym") or "Unknown" for game in analytics_games),
            "times": Counter(_time(game.get("start_time")) for game in analytics_games),
            "pools": Counter(game.get("pool") or "Unknown" for game in analytics_games),
            "revisions": history.get("revisions", []),
            "pool_observations": history.get("pool_observations", history.get("games", []))}

def _bar_summary(title, values):
    if not values: return f'<section class="card"><h3>{_esc(title)}</h3><p class="muted">No schedule data yet.</p></section>'
    maximum = max(values.values())
    rows = "".join(f'<div class="bar-row"><span>{_esc(label)}</span><i><b style="width:{count * 100 / maximum:.0f}%"></b></i><strong>{count}</strong></div>' for label, count in values.most_common())
    return f'<section class="card"><h3>{_esc(title)}</h3>{rows}</section>'

def _pool_chart(observations):
    points = []
    for game in observations:
        pool = game.get("pool", ""); letter = pool[:1].upper()
        if "A" <= letter <= "Z": points.append((game.get("detected_at", ""), ord(letter) - ord("A"), pool, game.get("pool_position", "")))
    if not points: return '<p class="muted">Pool Movement appears after the first successfully parsed schedule revision.</p>'
    points.sort(); width, height, padding = 760, 260, 42
    max_rank, spread = max(rank for _, rank, _, _ in points), max(1, len(points) - 1)
    scale = (height - padding * 2) / max(1, max_rank)
    coords = [(padding + index * (width - padding * 2) / spread, padding + rank * scale) for index, (_, rank, _, _) in enumerate(points)]
    guides = "".join(f'<line x1="{padding}" y1="{padding + rank * scale:.0f}" x2="{width-padding}" y2="{padding + rank * scale:.0f}" class="chart-guide"/><text x="8" y="{padding + rank * scale + 4:.0f}" class="chart-axis">{chr(ord("A") + rank)}</text>' for rank in range(min(max_rank, 7) + 1))
    dots = "".join(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="5" class="chart-dot"><title>{_esc(_fmt(detected))}: {_esc(pool)} position {_esc(position)}</title></circle>' for (x, y), (detected, _, pool, position) in zip(coords, points))
    labels = "".join(f'<text x="{x:.0f}" y="{height-10}" text-anchor="middle" class="chart-label">{_esc(_fmt(detected).split(",")[0])}</text>' for (x, _), (detected, _, _, _) in zip(coords, points))
    line = " ".join(f"{x:.0f},{y:.0f}" for x, y in coords)
    svg = f'''<svg viewBox="0 0 {width} {height}" style="min-width:0" role="img" aria-label="Pool Movement, A is highest">
<title>Pool Movement, A ranks above B</title>{guides}<polyline fill="none" class="chart-line" points="{line}"/>{dots}{labels}</svg>'''
    return f'<div class="svg-wrap">{svg}</div><p class="muted">A ranks above B, B above C. Each point is from a parsed revision.</p>'

def _schedule_rows(games):
    rows = "".join(f'<tr><td>{_esc(game.get("game_date"))}</td><td>{_esc(_time(game.get("start_time")))}–{_esc(_time(game.get("end_time")))}</td><td>{_esc(game.get("gym"))}</td><td>{_esc(game.get("pool"))}</td><td>{_esc(game.get("pool_position"))}</td></tr>' for game in games)
    return rows or '<tr><td colspan="5" class="muted">No future games are in the latest parsed schedule.</td></tr>'

def _home_view(state, history):
    data = _dashboard_model(history); next_game = data["next"]
    current = next_game or (data["current_games"][-1] if data["current_games"] else None)
    latest = data["revisions"][0] if data["revisions"] else {}
    if next_game:
        next_content = f'<p class="date">{_esc(next_game.get("game_date"))}</p><p class="next-time">{_esc(_time(next_game.get("start_time")))}–{_esc(_time(next_game.get("end_time")))}</p><p class="venue">{_esc(next_game.get("gym"))}</p><p>Pool <b>{_esc(next_game.get("pool"))}</b> · Position <b>{_esc(next_game.get("pool_position"))}</b></p>'
    else: next_content = '<p class="muted">No future game is in the latest parsed schedule.</p>'
    status = f'<dl><dt>Current/latest pool</dt><dd>{_esc(current.get("pool")) if current else "—"}</dd><dt>Current pool position</dt><dd>{_esc(current.get("pool_position")) if current else "—"}</dd><dt>Next game</dt><dd>{_esc(current.get("game_date")) if current else "—"} {_esc(_time(current.get("start_time"))) if current else ""}</dd><dt>Next gym</dt><dd>{_esc(current.get("gym")) if current else "—"}</dd><dt>Latest revision</dt><dd>{_esc(_fmt(latest.get("detected_at")))}</dd></dl>'
    return f'''<div class="home-lead"><section class="card next-card"><h2>Next Game</h2>{next_content}</section><section class="card status-card"><h2>Current Status</h2>{status}</section></div><section class="card"><h2>Current Schedule</h2><p class="muted">Only the latest successfully parsed revision is shown.</p><div class="table-wrap"><table><thead><tr><th>Date</th><th>Time</th><th>Gym</th><th>Pool</th><th>Position</th></tr></thead><tbody>{_schedule_rows(data["current_upcoming"])}</tbody></table></div></section><div class="analytics-grid">{_bar_summary("Gym Breakdown", data["gyms"])}{_bar_summary("Time Slot Breakdown", data["times"])}{_bar_summary("Pool Appearances", data["pools"])}<section class="card"><h3>Schedule Analytics</h3><p class="muted">These summaries deduplicate logical sessions across revisions.</p><p><b>Most common start:</b> {_esc(data["times"].most_common(1)[0][0]) if data["times"] else "—"}</p></section></div><section class="card movement-card"><h2>Pool Movement</h2>{_pool_chart(data["pool_observations"])}</section>'''

def _history_view(state, history):
    revisions, failure = history.get("revisions", []), state.get("last_failure")
    recent = sum(1 for revision in revisions if revision.get("detected_at", "") >= (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)).isoformat())
    values = [("Last website scan", state.get("last_website_scan")), ("Last schedule change", revisions[0].get("detected_at") if revisions else None), ("Last successful parse", state.get("last_successful_parsed")), ("Last calendar reconciliation", state.get("last_successful_calendar")), ("Last email", state.get("last_successful_email")), ("Last completed update", state.get("last_successfully_completed_run")), ("Revisions in last 7 days", recent), ("Current/last source PDF", revisions[0].get("source_url") if revisions else None)]
    def metric(label, value):
        shown = f'<a href="{_esc(value)}" title="{_esc(value)}">Open PDF</a>' if label == "Current/last source PDF" and value else _esc(value if label == "Revisions in last 7 days" else _fmt(value))
        return f'<div><span>{_esc(label)}</span><b>{shown}</b></div>'
    metrics = "".join(metric(label, value) for label, value in values)
    failure_html = f'<section id="failure" class="failure"><h2>Last Failure</h2><p><b>{_esc(failure.get("stage"))}</b> · {_esc(_fmt(failure.get("at")))}</p><p>{_esc(failure.get("message"))}</p></section>' if failure else '<p class="healthy-note">No processing failure is recorded.</p>'
    rows = "".join(f'<tr><td>{_esc(_fmt(revision.get("detected_at")))}</td><td><a href="{_esc(revision.get("source_url"))}" title="{_esc(revision.get("source_url"))}">Open PDF</a></td><td>{_esc(_fmt(revision.get("parsed_at")))}</td><td>{_esc(_fmt(revision.get("calendar_at")))}</td><td>{_esc(_fmt(revision.get("email_at")))}</td><td>{_esc(_fmt(revision.get("completed_at")))}</td></tr>' for revision in revisions) or '<tr><td colspan="6" class="muted">No schedule revisions detected yet.</td></tr>'
    return f'''<section class="card"><h2>Operational History</h2><div class="metric-grid">{metrics}</div>{failure_html}</section><section class="card"><h2>Schedule Revisions</h2><div class="table-wrap"><table><thead><tr><th>Detected</th><th>Source</th><th>Parsed</th><th>Calendar synced</th><th>Email sent</th><th>Completed</th></tr></thead><tbody>{rows}</tbody></table></div></section>'''

def _settings_view(settings):
    def removals(values, action):
        return "".join(f'<li>{_esc(value)} <form method="post" class="inline"><input type="hidden" name="view" value="settings"><input type="hidden" name="action" value="{action}"><input type="hidden" name="value" value="{_esc(value)}"><button type="submit">Remove</button></form></li>' for value in values)
    hidden = '<input type="hidden" name="view" value="settings">'
    return f'''<div class="settings-grid"><section class="card"><h2>Schedule Selection</h2><p>This field is required before schedule processing can run.</p><form method="post">{hidden}<input name="schedule_match_text" value="{_esc(settings["schedule_match_text"])}" required aria-required="true"><input type="hidden" name="action" value="save_schedule_match_text"><button type="submit">Save</button></form></section><section class="card"><h2>Team Names</h2><ul>{removals(settings["team_names"], "remove_team_name")}</ul><form method="post">{hidden}<input name="value" placeholder="Add team name"><input type="hidden" name="action" value="add_team_name"><button type="submit">Add team</button></form></section><section class="card"><h2>Gyms</h2><ul>{removals(settings["gyms"], "remove_gym")}</ul><form method="post">{hidden}<input name="value" placeholder="Add gym"><input type="hidden" name="action" value="add_gym"><button type="submit">Add gym</button></form></section><section class="card"><h2>Email Recipients</h2><ul>{removals(settings["email_recipients"], "remove_email")}</ul><form method="post">{hidden}<input name="value" type="email" placeholder="Add email"><input type="hidden" name="action" value="add_email"><button type="submit">Add email</button></form></section></div>'''

def _page(view="home", message="", error=""):
    view = view if view in VIEWS else "home"
    settings, state, history = _ui_settings(), _state(), _history()
    if view == "settings" and not settings["schedule_match_text"]: error = error or "schedule match text is required"
    failure = state.get("last_failure"); health = "Healthy" if not failure else f'Failure: {failure.get("stage", "processing")}'; status_class = "healthy" if not failure else "unhealthy"
    navigation = "".join(f'<a href="/?view={name}" class="{"active" if view == name else ""}" {"aria-current=\"page\"" if view == name else ""}>{name.title()}</a>' for name in ("home", "history", "settings"))
    content = _home_view(state, history) if view == "home" else _history_view(state, history) if view == "history" else _settings_view(settings)
    notices = f'<p class="notice">{_esc(message)}</p>' if message else ''
    notices += f'<p class="notice error">{_esc(error)}</p>' if error else ''
    failure_link = ' <a href="/?view=history#failure">View details</a>' if failure else ''
    return f'''<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Volleyball Monitor</title><style>:root{{font-family:system-ui,sans-serif;color:#17212b;background:#f4f7f8}}*{{box-sizing:border-box}}body{{margin:0}}a{{color:#075b93}}a:focus-visible,button:focus-visible,input:focus-visible{{outline:3px solid #f0a202;outline-offset:2px}}.shell{{max-width:1200px;margin:auto;padding:1.25rem}}header{{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:1rem;margin-bottom:1rem}}h1{{font-size:clamp(1.5rem,3vw,2rem);margin:0}}h2{{margin:.1rem 0 .75rem;font-size:1.2rem}}h3{{margin:.1rem 0 .75rem;font-size:1rem}}p{{line-height:1.45}}.health{{font-size:.92rem}}.health::before{{content:"●";margin-right:.35rem}}.healthy{{color:#167044}}.unhealthy{{color:#a72e20}}nav{{display:flex;gap:.25rem;border-bottom:1px solid #cad5da;margin-bottom:1.25rem}}nav a{{padding:.65rem .9rem;text-decoration:none;color:#33434c;border-radius:.4rem .4rem 0 0}}nav a.active{{background:#e3f0f6;color:#063f67;font-weight:700;border-bottom:3px solid #0875b8}}.card{{background:#fff;border:1px solid #d6e0e4;border-radius:.7rem;padding:1rem;box-shadow:0 1px 2px #15212b0d;margin-bottom:1rem}}.home-lead{{display:grid;grid-template-columns:2fr 1fr;gap:1rem}}.next-card{{min-height:15rem}}.date{{font-size:1.15rem;font-weight:700;margin:.2rem 0}}.next-time{{font-size:clamp(1.8rem,4vw,2.8rem);font-weight:750;margin:.1rem 0}}.venue{{font-size:1.15rem}}dl{{margin:0;display:grid;gap:.2rem}}dt{{font-size:.78rem;text-transform:uppercase;color:#5a6871}}dd{{margin:0 0 .55rem;font-weight:650}}.table-wrap{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;min-width:600px}}th,td{{text-align:left;padding:.65rem;border-bottom:1px solid #e1e8eb;vertical-align:top}}th{{font-size:.78rem;text-transform:uppercase;letter-spacing:.03em;color:#53616a}}.analytics-grid,.settings-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1rem}}.analytics-grid .card,.settings-grid .card{{margin:0}}.movement-card{{margin-top:1rem}}.bar-row{{display:grid;grid-template-columns:minmax(5rem,1fr) 2fr 2rem;gap:.5rem;align-items:center;margin:.45rem 0;font-size:.9rem}}.bar-row i{{height:.6rem;background:#e6edf0;border-radius:1rem;overflow:hidden}}.bar-row b{{display:block;height:100%;background:#1f7aac;border-radius:1rem}}.svg-wrap{{width:100%;overflow-x:auto}}svg{{display:block;width:100%;min-width:520px;height:auto}}.chart-guide{{stroke:#dbe5e9;stroke-width:1}}.chart-axis,.chart-label{{font-size:11px;fill:#53616a}}.chart-line{{stroke:#0875b8;stroke-width:4}}.chart-dot{{fill:#f0a202;stroke:#825500;stroke-width:1}}.metric-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.7rem}}.metric-grid div{{background:#f4f7f8;padding:.7rem;border-radius:.45rem}}.metric-grid span{{display:block;font-size:.76rem;color:#5a6871}}.metric-grid b{{display:block;margin-top:.3rem;font-size:.95rem;overflow-wrap:anywhere}}.failure{{border-left:4px solid #ba3527;padding:.75rem;background:#fff5f4;margin-top:1rem}}.healthy-note,.muted{{color:#5a6871}}.notice{{padding:.7rem;background:#e6f4eb;border-radius:.45rem}}.notice.error{{background:#fff0ef;color:#8e251b}}form{{margin:.65rem 0}}input,button{{font:inherit;min-height:2.7rem;padding:.5rem;border-radius:.35rem}}input{{border:1px solid #aebdc4;width:min(100%,28rem)}}button{{border:1px solid #075b93;background:#0875b8;color:#fff;cursor:pointer}}.inline{{display:inline;margin-left:.35rem}}.inline button{{min-height:2.2rem;padding:.25rem .5rem;background:#fff;color:#075b93}}ul{{padding-left:1.2rem}}@media (max-width:700px){{.shell{{padding:.8rem}}.home-lead,.analytics-grid,.settings-grid{{grid-template-columns:1fr}}.metric-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}header{{align-items:flex-start}}nav a{{padding:.65rem .6rem}}.next-card{{min-height:0}}}}</style></head><body><main class="shell"><header><div><h1>Volleyball Monitor</h1><div class="health {status_class}" aria-live="polite">{_esc(health)} · Last checked {_esc(_fmt(state.get("last_website_scan")))}{failure_link}</div></div></header><nav aria-label="Primary navigation">{navigation}</nav>{notices}{content}</main></body></html>'''

def apply_changes(current, data):
    """Apply one independent settings form submission; ``save`` validates it."""
    action, value = data.get("action", ""), data.get("value", "")
    if action == "save_schedule_match_text": current["schedule_match_text"] = data["schedule_match_text"].strip()
    elif action in {"add_team_name", "add_gym", "add_email"}:
        if value.strip(): current[{"add_team_name": "team_names", "add_gym": "gyms", "add_email": "email_recipients"}[action]].append(value.strip())
    elif action in {"remove_team_name", "remove_gym", "remove_email"}:
        key = {"remove_team_name": "team_names", "remove_gym": "gyms", "remove_email": "email_recipients"}[action]
        if key in {"team_names", "email_recipients"} and len(current[key]) <= 1: raise ValueError(f"at least one {'team name' if key == 'team_names' else 'email recipient'} is required")
        current[key].remove(value)
    return current

class Handler(BaseHTTPRequestHandler):
    def _send(self, body):
        encoded = body.encode("utf-8"); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(encoded))); self.end_headers(); self.wfile.write(encoded)
    def do_GET(self):
        view = parse_qs(urlparse(self.path).query).get("view", ["home"])[0]
        try: self._send(_page(view=view))
        except Exception as exc: self._send(_page(view="home", error=str(exc)))
    def do_POST(self):
        data = {key: values[0] for key, values in parse_qs(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()).items()}; view = data.get("view", "settings")
        try: save(apply_changes(_ui_settings(), data)); self._send(_page(view=view, message="Settings saved."))
        except Exception as exc: self._send(_page(view=view, error=str(exc)))
    def log_message(self, *_): pass

if __name__ == "__main__":
    load_env(); ThreadingHTTPServer((os.environ.get("UI_HOST", "0.0.0.0"), int(os.environ.get("UI_PORT", "8080"))), Handler).serve_forever()
