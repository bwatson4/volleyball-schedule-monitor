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


def test_ui_adds_and_removes_team_aliases_and_rejects_zero():
    current = sample_settings()
    assert apply_changes(current, {"new_team_name": "Example Spikers 2"})["team_names"] == ["Example Spikers", "Example Spikers 2"]
    assert apply_changes(current, {"remove_team_name": "Example Spikers 2"})["team_names"] == ["Example Spikers"]
    with pytest.raises(ValueError, match="team name"):
        validate(apply_changes(current, {"remove_team_name": "Example Spikers"}))
