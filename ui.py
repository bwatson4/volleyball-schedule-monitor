"""Tiny LAN-only configuration/status UI; uses only Python's standard library."""
from __future__ import annotations
import html, os, threading
from collections import Counter
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs
from pathlib import Path
from src.env import load_env
from src.settings import load, save, settings_path
from src.state import ScheduleState

def _state():
    from config import STATE_FILE
    return ScheduleState(STATE_FILE).data

def _ui_settings():
    try:
        return load()
    except ValueError as exc:
        # A fresh installation has no schedule selector yet; keep the form
        # usable so the required value can be configured through the LAN UI.
        return {"team_names": [], "schedule_match_text": "", "gyms": [], "email_recipients": []}

def _history():
    try:
        from config import HISTORY_FILE
        if not HISTORY_FILE.exists():
            return {"revisions": [], "games": []}
        from src.history import HistoryStore
        return HistoryStore(HISTORY_FILE).dashboard()
    except Exception:
        return {"revisions": [], "games": []}

def _fmt(value):
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%b %-d, %Y %H:%M")
    except (ValueError, TypeError):
        return str(value)

def _time(value):
    try:
        return datetime.fromisoformat(value).strftime("%H:%M")
    except (ValueError, TypeError):
        return str(value or "")[-5:]

def _dashboard_model(history, current_time=None):
    """Pure dashboard data transformation, intentionally cheap for a Pi Zero."""
    current_time = current_time or datetime.now(timezone.utc).replace(tzinfo=None)
    games = list(history.get("games", []))
    def event_time(game):
        try: return datetime.fromisoformat(game["start_time"])
        except (ValueError, TypeError): return datetime.min
    games.sort(key=event_time)
    upcoming = [game for game in games if event_time(game) >= current_time]
    gyms, times, pools = Counter(g.get("gym") or "Unknown" for g in games), Counter(_time(g.get("start_time")) for g in games), Counter(g.get("pool") or "Unknown" for g in games)
    return {"games": games, "upcoming": upcoming, "next": upcoming[0] if upcoming else None,
            "elapsed": len(games) - len(upcoming), "gyms": gyms, "times": times, "pools": pools,
            "revisions": history.get("revisions", [])}

def _rows(counter):
    return " ".join(f"{html.escape(str(key))}: {value}" for key, value in counter.most_common()) or "—"

def _pool_chart(games):
    points = []
    for game in games:
        pool = game.get("pool", "")
        match = pool[:1].upper()
        if not ("A" <= match <= "Z"):
            continue
        # SVG's y axis increases downwards: A is placed at the top.
        points.append((game.get("detected_at", ""), ord(match) - ord("A"), pool))
    if not points:
        return "<p>No pool history yet.</p>"
    points.sort()
    width, height = 320, 120
    spread = max(1, len(points) - 1)
    coords = [(10 + i * (width - 20) / spread, 12 + rank * 14) for i, (_, rank, _) in enumerate(points)]
    polyline = " ".join(f"{x:.0f},{y:.0f}" for x, y in coords)
    labels = "".join(f'<text x="{x:.0f}" y="{min(height - 3, y + 12):.0f}" font-size="10">{html.escape(pool)}</text>' for (x, y), (_, _, pool) in zip(coords, points))
    return f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Pool Movement, A is highest" style="width:100%;max-width:{width}px;border:1px solid #ddd"><polyline fill="none" stroke="#1769aa" stroke-width="3" points="{polyline}"/>{labels}</svg><p>A ranks above B, B above C.</p>'

def _dashboard(state, history):
    data = _dashboard_model(history)
    esc = lambda value: html.escape(str(value or ""))
    revisions = data["revisions"]
    candidate = state.get("candidate", {})
    failure = state.get("last_failure")
    health = f"Last failure: {esc(failure.get('stage'))} at {_fmt(failure.get('at'))}: {esc(failure.get('message'))}" if failure else "Healthy — no recorded processing failure."
    next_game = data["next"]
    next_html = (f"<b>{esc(next_game.get('game_date'))}</b> {esc(_time(next_game.get('start_time')))}–{esc(_time(next_game.get('end_time')))}<br>{esc(next_game.get('gym'))} · {esc(next_game.get('pool'))} · Position {esc(next_game.get('pool_position'))}"
                 if next_game else "No future game is currently scheduled.")
    games_html = "".join(f"<tr><td>{esc(g.get('game_date'))}</td><td>{esc(_time(g.get('start_time')))}–{esc(_time(g.get('end_time')))}</td><td>{esc(g.get('gym'))}</td><td>{esc(g.get('pool'))}</td><td>{esc(g.get('pool_position'))}</td></tr>" for g in data["upcoming"]) or "<tr><td colspan=5>No future games.</td></tr>"
    revisions_html = "".join(f"<tr><td>{_fmt(r.get('detected_at'))}</td><td>{esc(r.get('source_url'))}</td><td>{_fmt(r.get('parsed_at'))}</td><td>{_fmt(r.get('calendar_at'))}</td><td>{_fmt(r.get('email_at'))}</td><td>{_fmt(r.get('completed_at'))}</td></tr>" for r in revisions) or "<tr><td colspan=6>No schedule revisions detected yet.</td></tr>"
    latest = revisions[0] if revisions else {}
    recent_count = sum(1 for r in revisions if r.get("detected_at", "") >= (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)).isoformat())
    time_values = [_time(game.get("start_time")) for game in data["games"]]
    movement_rows = "".join(f"<tr><td>{_fmt(g.get('detected_at'))}</td><td>{esc(g.get('pool'))}</td><td>{esc(g.get('pool_position'))}</td></tr>" for g in data["games"]) or "<tr><td colspan=3>No pool history yet.</td></tr>"
    return f'''<section><h2>Dashboard</h2><p class="{'err' if failure else 'ok'}">{health}</p><div class="cards"><div><b>Last website scan</b><br>{_fmt(state.get('last_website_scan'))}</div><div><b>Last schedule change</b><br>{_fmt(latest.get('detected_at'))}</div><div><b>Last parsed</b><br>{_fmt(state.get('last_successful_parsed'))}</div><div><b>Last calendar reconciliation</b><br>{_fmt(state.get('last_successful_calendar'))}</div><div><b>Last email</b><br>{_fmt(state.get('last_successful_email'))}</div><div><b>Last completed update</b><br>{_fmt(state.get('last_successfully_completed_run'))}</div></div><p><b>Schedule revisions, last 7 days:</b> {recent_count}</p><p><b>Current/last source PDF:</b> {esc((candidate or latest).get('source_url', '—'))}</p></section><section><h2>Next Game</h2><p>{next_html}</p><h3>Remaining Team Events</h3><table><tr><th>Date</th><th>Time</th><th>Gym</th><th>Pool</th><th>Position</th></tr>{games_html}</table></section><section><h2>Schedule Analytics</h2><p><b>Sessions:</b> {len(data['games'])}; <b>Elapsed:</b> {data['elapsed']}; <b>Upcoming:</b> {len(data['upcoming'])}</p><p><b>Most common gym:</b> {esc(data['gyms'].most_common(1)[0][0]) if data['gyms'] else '—'}; <b>Most common start time:</b> {esc(data['times'].most_common(1)[0][0]) if data['times'] else '—'}; <b>Earliest/latest:</b> {esc(min(time_values)) if time_values else '—'} / {esc(max(time_values)) if time_values else '—'}</p><p><b>Gym counts:</b> {_rows(data['gyms'])}<br><b>Time slots:</b> {_rows(data['times'])}<br><b>Pool counts:</b> {_rows(data['pools'])}</p></section><section><h2>Pool Movement</h2>{_pool_chart(data['games'])}<table><tr><th>Date/revision</th><th>Pool</th><th>Position</th></tr>{movement_rows}</table></section><section><h2>Schedule Revisions</h2><table><tr><th>Detected</th><th>Source</th><th>Parsed</th><th>Calendar synced</th><th>Email sent</th><th>Completed</th></tr>{revisions_html}</table></section>'''

def _page(message="", error=""):
    s = _ui_settings()
    if not s["schedule_match_text"]:
        error = error or "schedule match text is required"
    state = _state()
    def esc(x): return html.escape(str(x or ""))
    def remove_forms(values, action):
        return "".join(f'<li>{esc(x)} <form method="post" class="inline"><input type="hidden" name="action" value="{action}"><input type="hidden" name="value" value="{esc(x)}"><button type="submit">Remove</button></form></li>' for x in values)
    teams = remove_forms(s["team_names"], "remove_team_name")
    gyms = remove_forms(s["gyms"], "remove_gym")
    emails = remove_forms(s["email_recipients"], "remove_email")
    dashboard = _dashboard(state, _history())
    return f'''<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><title>Volleyball Monitor</title><style>body{{font:16px sans-serif;max-width:900px;margin:2em auto;padding:0 1em}}section{{border:1px solid #ccc;padding:1em;margin:1em 0;border-radius:8px}}input{{padding:.5em;margin:.25em 0}}button{{padding:.45em;margin:.2em}}.inline{{display:inline}}.ok{{color:green}}.err{{color:#b00}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.6em}}.cards div{{background:#f5f5f5;padding:.6em}}table{{width:100%;border-collapse:collapse;font-size:.9em}}td,th{{text-align:left;border-bottom:1px solid #ddd;padding:.35em;vertical-align:top}}</style><h1>Volleyball Monitor</h1>{f'<p class="ok">{esc(message)}</p>' if message else ''}{f'<p class="err">{esc(error)}</p>' if error else ''}{dashboard}<section><h2>Settings</h2><h3>Schedule Match Text</h3><p>This field is required before schedule processing can run.</p><form method="post"><input name="schedule_match_text" value="{esc(s['schedule_match_text'])}" required aria-required="true"><input type="hidden" name="action" value="save_schedule_match_text"><button type="submit">Save</button></form><h3>Team Names</h3><ul>{teams}</ul><form method="post"><input name="value" placeholder="Add team name"><input type="hidden" name="action" value="add_team_name"><button type="submit">Add team</button></form><h3>Gyms</h3><ul>{gyms}</ul><form method="post"><input name="value" placeholder="Add gym"><input type="hidden" name="action" value="add_gym"><button type="submit">Add gym</button></form><h3>Email Recipients</h3><ul>{emails}</ul><form method="post"><input name="value" type="email" placeholder="Add email"><input type="hidden" name="action" value="add_email"><button type="submit">Add email</button></form></section>'''

def apply_changes(current, data):
    """Apply one LAN UI form submission; validation happens in ``save``."""
    action = data.get("action", "")
    value = data.get("value", "")
    if action == "save_schedule_match_text":
        current["schedule_match_text"] = data["schedule_match_text"].strip()
    elif action in {"add_team_name", "add_gym", "add_email"}:
        if value.strip():
            current[{"add_team_name": "team_names", "add_gym": "gyms", "add_email": "email_recipients"}[action]].append(value.strip())
    elif action in {"remove_team_name", "remove_gym", "remove_email"}:
        key = {"remove_team_name": "team_names", "remove_gym": "gyms", "remove_email": "email_recipients"}[action]
        if key in {"team_names", "email_recipients"} and len(current[key]) <= 1:
            raise ValueError(f"at least one {'team name' if key == 'team_names' else 'email recipient'} is required")
        current[key].remove(value)
    return current

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try: body = _page()
        except Exception as exc: body = _page(error=str(exc))
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(body.encode())
    def do_POST(self):
        data = {k:v[0] for k,v in parse_qs(self.rfile.read(int(self.headers.get('Content-Length','0'))).decode()).items()}
        try:
            save(apply_changes(_ui_settings(), data)); body = _page(message="Settings saved.")
        except Exception as exc: body = _page(error=str(exc))
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(body.encode())
    def log_message(self, *_): pass

if __name__ == "__main__":
    load_env(); port = int(os.environ.get("UI_PORT", "8080")); ThreadingHTTPServer((os.environ.get("UI_HOST", "0.0.0.0"), port), Handler).serve_forever()
