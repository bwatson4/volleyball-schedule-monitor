"""Lazy, bounded CalDAV calendar work.  Network failures are always propagated."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from caldav import DAVClient
from icalendar import Alarm, Calendar, Event
from config import CALDAV_TIMEOUT_SECONDS, CALENDAR_INDEX, CALENDAR_NAME, ICLOUD_APP_PASSWORD, ICLOUD_USERNAME

class CalendarManager:
    MARKER_NAME = "X-VOLLEYBALL-SCHEDULE-MONITOR"
    MARKER_VALUE = "1"
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
        event.add(CalendarManager.MARKER_NAME, CalendarManager.MARKER_VALUE)
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
        events = [events] if isinstance(events, dict) else list(events)
        self._connect()
        desired = {event["uid"]: event for event in events}
        # Only events carrying our explicit marker are in scope.  The date
        # search intentionally starts today: historical personal events and
        # past volleyball records are never candidates for deletion.
        existing = {uid: item for uid, item in self._managed_future_events(events).items()}
        for event in desired.values():
            current = existing.pop(event["uid"], None)
            if current is not None:
                current.data = self._calendar_payload(event).to_ical(); current.save()
            else:
                self.add_or_update_event(event)
        for obsolete in existing.values():
            obsolete.delete()

    def _managed_future_events(self, desired: list[dict]) -> dict[str, object]:
        if not desired:
            return {}
        start = datetime.now(timezone.utc).replace(tzinfo=None)
        # Search a full appliance planning horizon, not just the newest input
        # event, so a removed late-season session is still reconciled.
        end = start + timedelta(days=730)
        found = self.calendar.date_search(start=start, end=end, expand=True)
        managed: dict[str, object] = {}
        for remote in found:
            try:
                component = Calendar.from_ical(remote.data)
                vevent = next(item for item in component.walk() if item.name == "VEVENT")
                uid = str(vevent.get("UID", ""))
                marked = str(vevent.get(self.MARKER_NAME, "")) == self.MARKER_VALUE
                # One release used this namespace in UIDs before custom
                # properties were introduced. It is safe to migrate those
                # deterministic application resources, but never any other
                # calendar event.
                legacy = uid.startswith("volleyball-")
                if uid and (marked or legacy):
                    managed[uid] = remote
            except Exception:
                # A malformed third-party response is not evidence an event is
                # ours.  Never risk deleting it.
                continue
        return managed
