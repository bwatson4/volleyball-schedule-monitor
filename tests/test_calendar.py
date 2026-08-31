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
