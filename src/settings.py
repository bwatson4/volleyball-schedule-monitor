"""Small durable settings store shared by the scanner and local UI."""
from __future__ import annotations
import json, os, re
from pathlib import Path
from utils import atomic_json_write

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

def settings_path() -> Path:
    explicit = os.environ.get("SETTINGS_FILE")
    return Path(explicit) if explicit else Path(os.environ.get("RUNTIME_DIR", str(Path(__file__).resolve().parents[1] / "runtime"))) / "settings.json"

def normalize_team(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()

normalize_text = normalize_team

def _clean_list(values, label):
    if not isinstance(values, list): raise ValueError(f"{label} must be a list")
    result = []
    for value in values:
        value = str(value).strip()
        if not value: raise ValueError(f"{label} cannot contain empty entries")
        if value not in result: result.append(value)
    return result

def validate(data):
    if not isinstance(data, dict): raise ValueError("settings must be an object")
    # Version-1 settings used one ``team_name``. Accept it on load/save so an
    # installed Pi upgrades without manual intervention.
    aliases = data.get("team_names")
    if aliases is None and "team_name" in data:
        aliases = [data["team_name"]]
    team_names = _clean_list(aliases or [], "team names")
    if not team_names: raise ValueError("at least one team name is required")
    schedule_match_text = str(data.get("schedule_match_text", os.environ.get("SCHEDULE_MATCH_TEXT", ""))).strip()
    if not schedule_match_text: raise ValueError("schedule match text is required")
    gyms = _clean_list(data.get("gyms", []), "gyms")
    recipients = _clean_list(data.get("email_recipients", []), "email recipients")
    if not recipients: raise ValueError("at least one email recipient is required")
    if any(not EMAIL_RE.match(x) for x in recipients): raise ValueError("one or more email addresses are invalid")
    return {"team_names": [re.sub(r"\s+", " ", team) for team in team_names], "schedule_match_text": re.sub(r"\s+", " ", schedule_match_text), "gyms": gyms, "email_recipients": recipients}

def load():
    path = settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # Older installed files omitted this selector; retain their historical
        # behavior while requiring it for new installations.
        if "schedule_match_text" not in data and "SCHEDULE_MATCH_TEXT" not in os.environ:
            data["schedule_match_text"] = "Wednesday"
        return validate(data)
    except FileNotFoundError:
        # Keep public defaults generic; the installer creates a writable copy
        # from examples/settings.json.example for normal Pi deployments.
        return validate({"team_names": [x.strip() for x in os.environ.get("TEAM_NAMES", os.environ.get("TEAM_NAME", "Example Volleyball Team")).split(",") if x.strip()], "schedule_match_text": os.environ.get("SCHEDULE_MATCH_TEXT", ""), "gyms": [x.strip() for x in os.environ.get("GYMS", "Example Community Centre").split(",") if x.strip()], "email_recipients": [x.strip() for x in os.environ.get("EMAIL_RECIPIENTS", "").split(",") if x.strip()]})
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"unable to load settings: {exc}") from exc

def save(data):
    clean = validate(data); atomic_json_write(settings_path(), clean); return clean
