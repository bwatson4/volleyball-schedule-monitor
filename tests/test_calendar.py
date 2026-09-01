from datetime import datetime
from unittest.mock import MagicMock
import pytest
from src.calendar import CalendarManager

def event(uid="a"):
    return {"uid":uid,"summary":"match","description":"d","start":datetime(2026,1,1,19),"end":datetime(2026,1,1,20)}

def test_lookup_failure_is_propagated_not_treated_as_absent():
    calendar = MagicMock(); calendar.event_by_url.side_effect = OSError("network")
    with pytest.raises(OSError): CalendarManager(client=MagicMock(), calendar=calendar).add_or_update_event(event())
    calendar.add_event.assert_not_called()

def test_same_day_event_ids_are_distinct():
    from src.parser import ScheduleParser
    text = "Example Community Centre December 3, 2025\nA POOL\n1 Example Spikers 8:00-9:00\nB POOL\n1 Example Spikers 9:00-10:00"
    ids = [item["uid"] for item in ScheduleParser(text, team_names="Example Spikers", gyms=["Example Community Centre"]).parse()]
    assert len(ids) == len(set(ids))


class Remote:
    def __init__(self, data):
        self.data, self.saved, self.deleted = data, 0, 0
    def save(self): self.saved += 1
    def delete(self): self.deleted += 1


class FakeCalendar:
    url = "https://calendar.example/"
    def __init__(self, remote=()): self.remote, self.added = list(remote), []
    def date_search(self, **_kwargs): return self.remote
    def event_by_url(self, _url): return None
    def add_event(self, payload, href): self.added.append((payload, href))


def managed_remote(data):
    return Remote(CalendarManager._calendar_payload(data).to_ical())


def test_reconciliation_updates_mutable_fields_and_removes_only_marked_events():
    old = event("logical-1")
    old["source_team"] = "Team"; old.update(gym="Old Gym", pool="A POOL", pool_position="1", date="2026-01-01")
    obsolete = event("logical-2")
    obsolete["source_team"] = "Team"; obsolete.update(gym="Gym", pool="B POOL", pool_position="2", date="2026-01-01")
    unrelated = Remote(b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:personal\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    current = old | {"summary": "changed", "gym": "New Gym", "pool": "C POOL", "pool_position": "3"}
    known, stale = managed_remote(old), managed_remote(obsolete)
    calendar = FakeCalendar([known, stale, unrelated])
    CalendarManager(client=MagicMock(), calendar=calendar).add_or_update_events([current])
    assert known.saved == 1 and stale.deleted == 1 and unrelated.deleted == 0


def test_reconciliation_adds_and_partial_delete_failure_is_propagated_for_retry():
    new = event("logical-new")
    new["source_team"] = "Team"; new.update(gym="Gym", pool="A POOL", pool_position="1", date="2026-01-01")
    stale = managed_remote(new | {"uid": "obsolete"})
    stale.delete = MagicMock(side_effect=OSError("temporary"))
    calendar = FakeCalendar([stale])
    with pytest.raises(OSError):
        CalendarManager(client=MagicMock(), calendar=calendar).add_or_update_events([new])
    assert calendar.added  # add was safe and repeatable; state remains incomplete
