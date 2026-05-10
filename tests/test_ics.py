from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from calendar_agent.ics import build_ics, write_ics_file
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


def test_build_reserved_ics():
    content = build_ics(_event())

    assert "METHOD:PUBLISH" in content
    assert "STATUS:CONFIRMED" in content
    assert "SUMMARY:InClubGolf Practice - Lake Nona in North Bay" in content
    assert "DTSTART:20260514T214000Z" in content
    assert "DTEND:20260514T220000Z" in content


def test_build_canceled_ics():
    content = build_ics(_event(status="Canceled"))

    assert "METHOD:CANCEL" in content
    assert "STATUS:CANCELLED" in content
    assert "SUMMARY:[CANCELED] InClubGolf Practice - Lake Nona in North Bay" in content


def test_write_ics_uses_event_datetime_filename(tmp_path):
    path = write_ics_file(_event(), tmp_path)

    assert path.name == "2026-05-14_1740.ics"
    assert path.exists()
