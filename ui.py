"""Tiny LAN-only configuration/status UI; uses only Python's standard library."""
from __future__ import annotations
import html, os, threading
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

def _page(message="", error=""):
    s = _ui_settings()
    if not s["schedule_match_text"]:
        error = error or "schedule match text is required"
    state = _state()
    def esc(x): return html.escape(str(x or ""))
    teams = "".join(f'<li>{esc(x)} <button name="value" value="{esc(x)}" formaction="?action=remove_team_name">Remove</button></li>' for x in s["team_names"])
    gyms = "".join(f'<li>{esc(x)} <button name="value" value="{esc(x)}" formaction="?action=remove_gym">Remove</button></li>' for x in s["gyms"])
    emails = "".join(f'<li>{esc(x)} <button name="value" value="{esc(x)}" formaction="?action=remove_email">Remove</button></li>' for x in s["email_recipients"])
    status = "<br>".join(f"<b>{esc(k.replace('_',' ').title())}:</b> {esc(v)}" for k,v in state.items() if k != "candidate")
    return f'''<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><title>Volleyball Monitor</title><style>body{{font:16px sans-serif;max-width:720px;margin:2em auto;padding:0 1em}}section{{border:1px solid #ccc;padding:1em;margin:1em 0;border-radius:8px}}input{{padding:.5em;margin:.25em 0}}button{{padding:.45em;margin:.2em}}.ok{{color:green}}.err{{color:#b00}}</style><h1>Volleyball Monitor</h1>{f'<p class="ok">{esc(message)}</p>' if message else ''}{f'<p class="err">{esc(error)}</p>' if error else ''}<section><h2>Monitor Status</h2>{status or 'No scan has run yet.'}</section><section><h2>Schedule Match Text</h2><p>This field is required before schedule processing can run.</p><form method="post"><input name="schedule_match_text" value="{esc(s['schedule_match_text'])}" required aria-required="true"><button name="action" value="save_schedule_match_text">Save</button></form></section><section><h2>Team Names</h2><form method="post"><ul>{teams}</ul><input name="value" placeholder="Add team name"><button name="action" value="add_team_name">Add team</button></form></section><section><h2>Gyms</h2><form method="post"><ul>{gyms}</ul><input name="value" placeholder="Add gym"><button name="action" value="add_gym">Add gym</button></form></section><section><h2>Email Recipients</h2><form method="post"><ul>{emails}</ul><input name="value" type="email" placeholder="Add email"><button name="action" value="add_email">Add email</button></form></section>'''

def apply_changes(current, data):
    """Apply one LAN UI form submission; validation happens in ``save``."""
    action = data.get("action", "")
    value = data.get("value", "")
    if action == "save_schedule_match_text" or (not action and "schedule_match_text" in data):
        current["schedule_match_text"] = data["schedule_match_text"].strip()
    elif action in {"add_team_name", "add_gym", "add_email"}:
        if value.strip():
            current[{"add_team_name": "team_names", "add_gym": "gyms", "add_email": "email_recipients"}[action]].append(value.strip())
    elif action in {"remove_team_name", "remove_gym", "remove_email"}:
        key = {"remove_team_name": "team_names", "remove_gym": "gyms", "remove_email": "email_recipients"}[action]
        if key in {"team_names", "email_recipients"} and len(current[key]) <= 1:
            raise ValueError(f"at least one {'team name' if key == 'team_names' else 'email recipient'} is required")
        current[key].remove(value)
    elif data.get("remove_team_name"):
        if len(current["team_names"]) <= 1: raise ValueError("at least one team name is required")
        current["team_names"].remove(data["remove_team_name"])
    elif data.get("remove_gym"):
        current["gyms"].remove(data["remove_gym"])
    elif data.get("remove_email"):
        if len(current["email_recipients"]) <= 1: raise ValueError("at least one email recipient is required")
        current["email_recipients"].remove(data["remove_email"])
    else:
        for field, key in (("new_team_name", "team_names"), ("new_gym", "gyms"), ("new_email", "email_recipients")):
            if data.get(field, "").strip(): current[key].append(data[field].strip())
    return current

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try: body = _page()
        except Exception as exc: body = _page(error=str(exc))
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(body.encode())
    def do_POST(self):
        data = {k:v[0] for k,v in parse_qs(self.rfile.read(int(self.headers.get('Content-Length','0'))).decode()).items()}
        query = {k: v[0] for k, v in parse_qs(self.path.partition("?")[2]).items()}
        query.update(data)
        data = query
        try:
            save(apply_changes(_ui_settings(), data)); body = _page(message="Settings saved.")
        except Exception as exc: body = _page(error=str(exc))
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(body.encode())
    def log_message(self, *_): pass

if __name__ == "__main__":
    load_env(); port = int(os.environ.get("UI_PORT", "8080")); ThreadingHTTPServer((os.environ.get("UI_HOST", "0.0.0.0"), port), Handler).serve_forever()
