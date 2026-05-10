from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from calendar_agent.google_calendar import GoogleCalendarClient, google_event_body
from calendar_agent.models import GolfEvent


def _event(status="Reserved"):
    start = datetime(2026, 5, 14, 17, 40, tzinfo=ZoneInfo("America/New_York"))
    return GolfEvent(
        source_uid="101",
        source_message_id="msg-101",
        event_uid="inclubgolf-test",
        event_type="Practice",
        status=status,
        location="Lake Nona in North Bay",
        description="Clean summary",
        start_time=start,
        end_time=start + timedelta(minutes=20),
    )


def test_google_body_marks_canceled_visible_not_deleted():
    body = google_event_body(_event(status="Canceled"))

    assert body["summary"] == "[CANCELED] InClubGolf Practice - Lake Nona in North Bay"
    assert "status" not in body
    assert body["extendedProperties"]["private"]["inclubgolf_event_uid"] == "inclubgolf-test"


def test_google_client_updates_existing_event():
    service = _MockGoogleService(existing=[{"id": "existing-id"}])
    client = GoogleCalendarClient(service=service, calendar_id="primary")

    event_id = client.upsert_event(_event())

    assert event_id == "existing-id"
    assert service.events_resource.updated is True
    assert service.events_resource.inserted is False


class _MockGoogleService:
    def __init__(self, existing):
        self.events_resource = _MockEvents(existing)

    def events(self):
        return self.events_resource


class _MockEvents:
    def __init__(self, existing):
        self.existing = existing
        self.updated = False
        self.inserted = False

    def list(self, **kwargs):
        self.list_kwargs = kwargs
        return self

    def update(self, **kwargs):
        self.updated = True
        self.update_kwargs = kwargs
        return self

    def insert(self, **kwargs):
        self.inserted = True
        self.insert_kwargs = kwargs
        return self

    def execute(self):
        if self.updated:
            return {"id": "existing-id"}
        if self.inserted:
            return {"id": "created-id"}
        return {"items": self.existing}
