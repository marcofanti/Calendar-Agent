from __future__ import annotations

import os

from calendar_agent.google_auth import GOOGLE_APP_SCOPES, get_google_credentials
from calendar_agent.models import GolfEvent


def get_google_calendar_service():
    from googleapiclient.discovery import build

    creds = get_google_credentials(GOOGLE_APP_SCOPES)
    return build("calendar", "v3", credentials=creds)


class GoogleCalendarClient:
    def __init__(self, service=None, calendar_id: str | None = None):
        self.service = service if service is not None else get_google_calendar_service()
        self.calendar_id = calendar_id or os.getenv("GOOGLE_CALENDAR_ID", "primary")

    def upsert_event(self, event: GolfEvent) -> str:
        existing = (
            self.service.events()
            .list(
                calendarId=self.calendar_id,
                privateExtendedProperty=f"inclubgolf_event_uid={event.event_uid}",
                singleEvents=True,
            )
            .execute()
            .get("items", [])
        )
        body = google_event_body(event)
        if existing:
            event_id = existing[0]["id"]
            self.service.events().update(
                calendarId=self.calendar_id,
                eventId=event_id,
                body=body,
            ).execute()
            return event_id

        created = (
            self.service.events()
            .insert(calendarId=self.calendar_id, body=body)
            .execute()
        )
        return created.get("id", event.event_uid)


def google_event_body(event: GolfEvent) -> dict:
    return {
        "summary": event.title,
        "location": event.location,
        "description": (
            f"{event.description}\n\n"
            "Synced by CalendarAgent\n"
            f"InClubGolf event UID: {event.event_uid}\n"
            f"Source message ID: {event.source_message_id}"
        ),
        "start": {
            "dateTime": event.start_time.isoformat(),
            "timeZone": "America/New_York",
        },
        "end": {
            "dateTime": event.end_time.isoformat(),
            "timeZone": "America/New_York",
        },
        "extendedProperties": {
            "private": {
                "inclubgolf_event_uid": event.event_uid,
                "source_message_id": event.source_message_id,
            }
        },
    }
