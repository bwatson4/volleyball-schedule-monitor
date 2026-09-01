import json
from datetime import datetime

import pytest

from src.settings import load, save, validate
from ui import _dashboard_model, _page, apply_changes


def sample_settings():
    return {"team_names": ["Example Spikers"], "gyms": ["Example Gym"], "email_recipients": ["player@example.com"]}


def test_legacy_team_name_is_migrated_on_load_and_save(monkeypatch, tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"team_name": "Example Spikers", "gyms": ["Example Gym"], "email_recipients": ["player@example.com"]}))
    monkeypatch.setenv("SETTINGS_FILE", str(path))
    assert load()["team_names"] == ["Example Spikers"]
    save(load())
    stored = json.loads(path.read_text())
    assert stored["team_names"] == ["Example Spikers"] and "team_name" not in stored


def test_fresh_settings_require_schedule_match_text(monkeypatch, tmp_path):
    monkeypatch.setenv("SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.delenv("SCHEDULE_MATCH_TEXT", raising=False)
    with pytest.raises(ValueError, match="schedule match text is required"):
        load()


def test_ui_identifies_schedule_match_text_as_required(monkeypatch, tmp_path):
    monkeypatch.setenv("SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.delenv("SCHEDULE_MATCH_TEXT", raising=False)
    page = _page(view="settings")
    assert "This field is required before schedule processing can run." in page
    assert 'name="schedule_match_text"' in page and "required" in page
    assert 'value="save_schedule_match_text"' in page
    assert "Save Settings" not in page


def test_ui_adds_and_removes_team_aliases_and_rejects_zero():
    current = sample_settings()
    assert apply_changes(current, {"action": "add_team_name", "value": "Example Spikers 2"})["team_names"] == ["Example Spikers", "Example Spikers 2"]
    assert apply_changes(current, {"action": "remove_team_name", "value": "Example Spikers 2"})["team_names"] == ["Example Spikers"]
    with pytest.raises(ValueError, match="team name"):
        apply_changes(current, {"action": "remove_team_name", "value": "Example Spikers"})


def test_ui_section_actions_preserve_unrelated_settings():
    current = sample_settings()
    apply_changes(current, {"action": "save_schedule_match_text", "schedule_match_text": " Thursday "})
    apply_changes(current, {"action": "add_team_name", "value": " New Team "})
    apply_changes(current, {"action": "add_gym", "value": " New Gym "})
    apply_changes(current, {"action": "add_email", "value": "new@example.com"})
    assert current == {"team_names": ["Example Spikers", "New Team"], "schedule_match_text": "Thursday", "gyms": ["Example Gym", "New Gym"], "email_recipients": ["player@example.com", "new@example.com"]}
    apply_changes(current, {"action": "remove_gym", "value": "New Gym"})
    apply_changes(current, {"action": "remove_email", "value": "new@example.com"})
    assert current["schedule_match_text"] == "Thursday"
    assert current["team_names"] == ["Example Spikers", "New Team"]


def test_ui_rejects_removing_last_required_email():
    with pytest.raises(ValueError, match="email recipient"):
        apply_changes(sample_settings(), {"action": "remove_email", "value": "player@example.com"})


def test_ui_forms_have_unambiguous_add_and_remove_actions(monkeypatch, tmp_path):
    monkeypatch.setenv("SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setenv("SCHEDULE_MATCH_TEXT", "Wednesday")
    save({**sample_settings(), "schedule_match_text": "Wednesday"})
    page = _page(view="settings")
    assert 'formaction="?action=' not in page
    assert page.count('name="action" value="add_team_name"') == 1
    assert page.count('name="action" value="add_gym"') == 1
    assert page.count('name="action" value="add_email"') == 1
    assert page.count('name="action" value="remove_team_name"') == 1
    assert page.count('name="action" value="remove_gym"') == 1
    assert page.count('name="action" value="remove_email"') == 1


def test_dashboard_model_selects_next_game_and_aggregates_pool_gym_and_time():
    history = {"revisions": [], "games": [
        {"game_date": "2026-09-01", "start_time": "2026-09-01T19:00:00", "end_time": "2026-09-01T20:00:00", "gym": "A Gym", "pool": "B POOL", "pool_position": "2"},
        {"game_date": "2026-09-08", "start_time": "2026-09-08T19:00:00", "end_time": "2026-09-08T20:00:00", "gym": "A Gym", "pool": "A POOL", "pool_position": "1"},
    ]}
    data = _dashboard_model(history, datetime(2026, 9, 2))
    assert data["next"]["pool"] == "A POOL"
    assert data["gyms"]["A Gym"] == 2 and data["times"]["19:00"] == 2 and data["pools"]["A POOL"] == 1


def test_dashboard_model_uses_latest_current_revision_and_deduplicates_pool_movement():
    old = {"logical_id": "game", "game_date": "2026-09-08", "start_time": "2026-09-08T19:00:00", "end_time": "2026-09-08T20:00:00", "gym": "Old", "pool": "C POOL", "pool_position": "3", "detected_at": "2026-08-01T00:00:00+00:00"}
    latest = old | {"start_time": "2026-09-08T20:00:00", "end_time": "2026-09-08T21:00:00", "gym": "New", "pool": "B POOL", "pool_position": "2", "detected_at": "2026-08-15T00:00:00+00:00"}
    data = _dashboard_model({"current_games": [latest], "analytics_games": [latest], "pool_observations": [old, latest], "revisions": []}, datetime(2026, 9, 1))
    assert data["next"]["gym"] == "New" and len(data["current_upcoming"]) == 1
    assert [item["pool"] for item in data["pool_observations"]] == ["B POOL"]


def test_pool_movement_chart_labels_one_point_per_weekly_game():
    from ui import _pool_chart

    chart = _pool_chart([{"pool": "C POOL", "pool_position": "3", "detected_at": "2026-08-01T00:00:00+00:00"}])
    assert "One point per weekly game" in chart


def test_home_history_and_settings_views_have_navigation_active_state_and_escape(monkeypatch):
    monkeypatch.setattr("ui._state", lambda: {"last_website_scan": "2026-08-31T21:27:00+00:00"})
    monkeypatch.setattr("ui._ui_settings", lambda: {"team_names": ["<team>"], "schedule_match_text": "Wednesday", "gyms": [], "email_recipients": ["x@example.com"]})
    monkeypatch.setattr("ui._history", lambda: {"revisions": [{"detected_at": "2026-08-31T21:00:00+00:00", "source_url": "https://example/?x=<bad>", "parsed_at": None, "calendar_at": None, "email_at": None, "completed_at": None}], "current_games": [], "analytics_games": [], "pool_observations": [], "games": []})
    home, history, settings = _page(), _page(view="history"), _page(view="settings")
    assert "Next Game" in home and 'aria-current="page">Home' in home
    assert "Operational History" in history and 'aria-current="page">History' in history and "Open PDF" in history
    assert "Schedule Selection" in settings and 'aria-current="page">Settings' in settings
    assert "&lt;team&gt;" in settings and "&lt;bad&gt;" in history
