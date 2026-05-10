from __future__ import annotations

import os

from calendar_agent.models import GolfEvent


class OutlookCalendarClient:
    def __init__(self, account=None):
        self.account = account if account is not None else self._account_from_env()

    def available(self) -> bool:
        return self.account is not None

    def upsert_event(self, event: GolfEvent) -> bool:
        if self.account is None:
            return False

        schedule = self.account.schedule()
        calendar = schedule.get_default_calendar()
        query = calendar.new_query("subject").contains(event.event_uid)
        existing = list(calendar.get_events(query=query))

        subject = f"{event.title} [{event.event_uid}]"
        body = (
            f"{event.description}\n\n"
            "Synced by CalendarAgent\n"
            f"InClubGolf event UID: {event.event_uid}\n"
            f"Source message ID: {event.source_message_id}"
        )

        if existing:
            ms_event = existing[0]
        else:
            ms_event = calendar.new_event()

        ms_event.subject = subject
        ms_event.location = event.location
        ms_event.body = body
        ms_event.start = event.start_time
        ms_event.end = event.end_time
        ms_event.save()
        return True

    def _account_from_env(self):
        client_id = os.getenv("O365_CLIENT_ID")
        client_secret = os.getenv("O365_CLIENT_SECRET")
        if not client_id or not client_secret:
            return None

        from O365 import Account

        account = Account((client_id, client_secret))
        if not account.is_authenticated:
            scopes = ["basic", "calendar_all"]
            if not account.authenticate(scopes=scopes):
                return None
        return account
