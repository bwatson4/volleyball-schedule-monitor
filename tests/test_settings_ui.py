import json

import pytest

from src.settings import load, save, validate
from ui import _page, apply_changes


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
    page = _page()
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
    page = _page()
    assert 'formaction="?action=' not in page
    assert page.count('name="action" value="add_team_name"') == 1
    assert page.count('name="action" value="add_gym"') == 1
    assert page.count('name="action" value="add_email"') == 1
    assert page.count('name="action" value="remove_team_name"') == 1
    assert page.count('name="action" value="remove_gym"') == 1
    assert page.count('name="action" value="remove_email"') == 1
