"""Lazy, bounded CalDAV calendar work.  Network failures are always propagated."""
from __future__ import annotations
from datetime import timedelta
from caldav import DAVClient
from icalendar import Alarm, Calendar, Event
from config import CALDAV_TIMEOUT_SECONDS, CALENDAR_INDEX, CALENDAR_NAME, ICLOUD_APP_PASSWORD, ICLOUD_USERNAME

class CalendarManager:
    def __init__(self, username=ICLOUD_USERNAME, password=ICLOUD_APP_PASSWORD, calendar_index=CALENDAR_INDEX, calendar_name=CALENDAR_NAME, client=None, calendar=None):
        self.username, self.password = username, password
        self.calendar_index, self.calendar_name, self.client, self.calendar = calendar_index, calendar_name, client, calendar

    def _connect(self):
        if self.calendar is not None: return
        # caldav uses requests internally; this is the library's supported timeout kwarg.
        self.client = self.client or DAVClient(url="https://caldav.icloud.com/", username=self.username, password=self.password, timeout=CALDAV_TIMEOUT_SECONDS)
        calendars = self.client.principal().calendars()
        if self.calendar_name:
            matches = [calendar for calendar in calendars if getattr(calendar, "name", None) == self.calendar_name]
            if not matches: raise LookupError(f"Calendar named {self.calendar_name!r} was not found")
            self.calendar = matches[0]
        else:
            if len(calendars) <= self.calendar_index: raise IndexError("Calendar index out of range")
            self.calendar = calendars[self.calendar_index]

    @staticmethod
    def _calendar_payload(data):
        cal, event = Calendar(), Event()
        for key in ("uid", "summary", "description"): event.add(key, data[key])
        event.add("dtstart", data["start"]); event.add("dtend", data["end"])
        alarm = Alarm(); alarm.add("action", "DISPLAY"); alarm.add("description", f"Upcoming: {data['summary']}"); alarm.add("trigger", timedelta(minutes=-30))
        event.add_component(alarm); cal.add_component(event); return cal

    def add_or_update_event(self, data):
        self._connect()
        # Stable resource name means retries address exactly the same server object.
        url = str(self.calendar.url).rstrip("/") + "/" + data["uid"] + ".ics"
        existing = self.calendar.event_by_url(url)  # exceptions are intentional: unknown != absent
        payload = self._calendar_payload(data).to_ical()
        if existing:
            existing.data = payload; existing.save()
        else:
            self.calendar.add_event(payload, href=data["uid"] + ".ics")

    def add_or_update_events(self, events):
        for event in ([events] if isinstance(events, dict) else events): self.add_or_update_event(event)
