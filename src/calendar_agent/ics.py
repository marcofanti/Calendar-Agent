from __future__ import annotations

from datetime import timezone
from pathlib import Path

from calendar_agent.models import GolfEvent


def write_ics_file(event: GolfEvent, output_dir: Path) -> Path:
    output_dir = output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{event.start_time.strftime('%Y-%m-%d_%H%M')}.ics"
    path.write_text(build_ics(event), encoding="utf-8")
    return path


def build_ics(event: GolfEvent) -> str:
    method = "CANCEL" if event.is_canceled else "PUBLISH"
    status = "CANCELLED" if event.is_canceled else "CONFIRMED"
    sequence = "1" if event.is_canceled else "0"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//CalendarAgent//{event.source_label}//EN",
        f"METHOD:{method}",
    ]
    if event.calendar_name:
        lines.append(f"X-WR-CALNAME:{_escape(event.calendar_name)}")
    lines += [
        "BEGIN:VEVENT",
        f"UID:{_escape(event.event_uid)}",
        f"DTSTAMP:{_utc_stamp(event.start_time)}",
        f"DTSTART:{_utc_stamp(event.start_time)}",
        f"DTEND:{_utc_stamp(event.end_time)}",
        f"SUMMARY:{_escape(event.title)}",
        f"DESCRIPTION:{_escape(event.description)}",
        f"LOCATION:{_escape(event.location)}",
        f"STATUS:{status}",
        f"SEQUENCE:{sequence}",
        "END:VEVENT",
        "END:VCALENDAR",
        "",
    ]
    return "\r\n".join(lines)


def _utc_stamp(value) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace(";", "\\;")
        .replace(",", "\\,")
    )
