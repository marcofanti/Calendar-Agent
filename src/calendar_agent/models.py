from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


EventStatus = Literal["Reserved", "Canceled"]
EventType = str  # profile-defined; InClubGolf uses "Practice"/"Lesson"


@dataclass(slots=True)
class EmailRecord:
    uid: str
    message_id: str
    subject: str
    body: str
    folder: str = "INBOX"
    sender: str = ""
    recipient: str = ""


@dataclass(slots=True)
class GolfEvent:
    source_uid: str
    source_message_id: str
    event_uid: str
    event_type: EventType
    status: EventStatus
    location: str
    description: str
    start_time: datetime
    end_time: datetime
    cancel_url: str | None = None
    source_label: str = "InClubGolf"

    @property
    def is_canceled(self) -> bool:
        return self.status == "Canceled"

    @property
    def title(self) -> str:
        base = (
            f"{self.source_label} {self.event_type} - {self.location}"
            if self.location
            else f"{self.source_label} {self.event_type}"
        )
        if self.is_canceled:
            return f"[CANCELED] {base}"
        return base


@dataclass(slots=True)
class RunResult:
    emails_found: int = 0
    events_parsed: int = 0
    google_success: set[str] = field(default_factory=set)
    outlook_success: set[str] = field(default_factory=set)
    ics_success: set[str] = field(default_factory=set)
    trashed_uids: set[str] = field(default_factory=set)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    corrections_saved: int = 0
    prompts_updated: int = 0

    @property
    def not_parsed_count(self) -> int:
        return self.emails_found - self.events_parsed
