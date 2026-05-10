import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from calendar_agent.models import EmailRecord
from calendar_agent.parser import parse_email, parse_inclubgolf_email
from calendar_agent.profiles import SourceProfile


def test_parse_practice_confirmation_example():
    llm = _FakeLlm(
        {
            "is_event": True,
            "event_type": "Practice",
            "status": "Reserved",
            "location": "Lake Nona in North Bay",
            "date": "05/14/2026",
            "time": "05:40 PM",
            "confidence": 1,
        }
    )
    email = EmailRecord(
        uid="101",
        message_id="msg-101",
        subject="Practice Confirmed in North Bay - Lake Nona",
        body=(
            "You have successfully reserved a practice session at Lake Nona in North Bay "
            "on 05/14/2026 at 05:40 PM.\n\n"
            "If you need to cancel, click this link: "
            "https://members.inclubgolf.com/Scheduler/Cancel?reservationId=253676\n\n"
            "All entries are 20 minutes long"
        ),
    )

    event = parse_inclubgolf_email(email, llm_client=llm)

    assert event is not None
    assert llm.call_count == 1
    assert "deterministic_candidate" in llm.prompts[0]
    assert event.event_type == "Practice"
    assert event.status == "Reserved"
    assert event.location == "Lake Nona in North Bay"
    assert event.start_time == datetime(2026, 5, 14, 17, 40, tzinfo=ZoneInfo("America/New_York"))
    assert event.end_time == event.start_time + timedelta(minutes=30)
    assert event.cancel_url == "https://members.inclubgolf.com/Scheduler/Cancel?reservationId=253676"
    assert event.title == "InClubGolf Practice - Lake Nona in North Bay"


def test_parse_cancellation_uses_same_event_uid_as_confirmation():
    body_reserved = (
        "You have successfully reserved a practice session at Lake Nona in North Bay "
        "on 05/14/2026 at 05:40 PM."
    )
    body_canceled = (
        "You have successfully canceled your Practice at Lake Nona in North Bay "
        "on 05/14/2026 at 05:40 PM."
    )
    reserved = parse_inclubgolf_email(
        EmailRecord(uid="1", message_id="r", subject="Practice Confirmed", body=body_reserved),
        llm_client=_FakeLlm(
            {
                "is_event": True,
                "event_type": "Practice",
                "status": "Reserved",
                "location": "Lake Nona in North Bay",
                "date": "05/14/2026",
                "time": "05:40 PM",
                "confidence": 1,
            }
        ),
    )
    canceled = parse_inclubgolf_email(
        EmailRecord(uid="2", message_id="c", subject="Practice Canceled", body=body_canceled),
        llm_client=_FakeLlm(
            {
                "is_event": True,
                "event_type": "Practice",
                "status": "Canceled",
                "location": "Lake Nona in North Bay",
                "date": "05/14/2026",
                "time": "05:40 PM",
                "confidence": 1,
            }
        ),
    )

    assert reserved is not None
    assert canceled is not None
    assert reserved.event_uid == canceled.event_uid
    assert canceled.title == "[CANCELED] InClubGolf Practice - Lake Nona in North Bay"


def test_llm_is_called_even_when_deterministic_parser_has_no_candidate():
    llm = _FakeLlm({"is_event": False, "confidence": 0})
    event = parse_inclubgolf_email(
        EmailRecord(uid="3", message_id="other", subject="Receipt", body="Thanks for your payment."),
        llm_client=llm,
    )

    assert event is None
    assert llm.call_count == 1
    assert "deterministic_candidate:\nnull" in llm.prompts[0]


def test_deterministic_candidate_overrides_llm_is_event_false():
    """When LLM returns is_event=false but deterministic candidate is complete, use candidate."""
    llm = _FakeLlm({"is_event": False, "confidence": 0})
    email = EmailRecord(
        uid="9",
        message_id="msg-9",
        subject="Practice Canceled",
        body="You have successfully canceled your Practice at Lake Nona in North Bay on 05/15/2026 at 08:40 AM.",
    )

    event = parse_inclubgolf_email(email, llm_client=llm)

    assert event is not None
    assert event.event_type == "Practice"
    assert event.status == "Canceled"
    assert event.location == "Lake Nona in North Bay"
    assert event.start_time == datetime(2026, 5, 15, 8, 40, tzinfo=ZoneInfo("America/New_York"))


def test_cancellation_subject_location_can_seed_candidate():
    llm = _FakeLlm(
        {
            "is_event": True,
            "event_type": "Practice",
            "status": "Canceled",
            "location": "Lake Nona in North Bay",
            "date": "05/14/2026",
            "time": "05:40 PM",
            "confidence": 1,
        }
    )

    event = parse_inclubgolf_email(
        EmailRecord(
            uid="4",
            message_id="cancel",
            subject="Practice Canceled in North Bay - Lake Nona",
            body="Your practice reservation on 05/14/2026 at 05:40 PM has been canceled.",
        ),
        llm_client=llm,
    )

    assert event is not None
    assert event.status == "Canceled"
    assert '"location": "Lake Nona in North Bay"' in llm.prompts[0]


def test_courtreserve_parses_start_and_end_time():
    profile = SourceProfile(
        name="courtreserve",
        from_pattern=re.compile("notifications_at_courtreserve"),
        subject_pattern=re.compile(".*"),
        to_pattern=None,
        body_pattern=None,
        prompt_template="Extract a CourtReserve booking.",
        duration_minutes=90,
    )
    llm = _FakeLlm({
        "is_event": True,
        "event_type": "Singles Live Ball (3.5+) FR 6:00P (Spring 2026)",
        "status": "Reserved",
        "location": "",
        "date": "4/24/2026",
        "start_time": "6:00 PM",
        "end_time": "7:30 PM",
        "confidence": 0.98,
    })
    email = EmailRecord(
        uid="200",
        message_id="msg-200",
        subject="Court Booking Confirmed",
        body="Singles Live Ball (3.5+) FR 6:00P (Spring 2026)\n4/24/2026\n6:00 PM - 7:30 PM",
        sender="notifications_at_courtreserve_com_2r6q26xw5k3936_566ad789@icloud.com",
    )

    attempt = parse_email(email, llm_client=llm, profile=profile)
    event = attempt.event

    assert event is not None
    assert event.event_type == "Singles Live Ball (3.5+) FR 6:00P (Spring 2026)"
    assert event.status == "Reserved"
    assert event.start_time == datetime(2026, 4, 24, 18, 0, tzinfo=ZoneInfo("America/New_York"))
    assert event.end_time == datetime(2026, 4, 24, 19, 30, tzinfo=ZoneInfo("America/New_York"))
    assert (event.end_time - event.start_time) == timedelta(minutes=90)


def test_courtreserve_end_time_overrides_profile_duration():
    profile = SourceProfile(
        name="courtreserve",
        from_pattern=re.compile("courtreserve"),
        subject_pattern=re.compile(".*"),
        to_pattern=None,
        body_pattern=None,
        prompt_template="Extract a CourtReserve booking.",
        duration_minutes=90,
    )
    llm = _FakeLlm({
        "is_event": True,
        "event_type": "Singles Training (3.0-3.5) MO 7:30P (Spring 2026)",
        "status": "Reserved",
        "location": "",
        "date": "4/20/2026",
        "start_time": "7:30 PM",
        "end_time": "9:00 PM",
        "confidence": 0.97,
    })
    email = EmailRecord(
        uid="201",
        message_id="msg-201",
        subject="Court Booking Confirmed",
        body="Singles Training (3.0-3.5) MO 7:30P (Spring 2026)\n4/20/2026\n7:30 PM - 9:00 PM",
        sender="notifications_at_courtreserve_com_2r6q26xw5k3936_566ad789@icloud.com",
    )

    attempt = parse_email(email, llm_client=llm, profile=profile)
    event = attempt.event

    assert event is not None
    assert event.start_time == datetime(2026, 4, 20, 19, 30, tzinfo=ZoneInfo("America/New_York"))
    assert event.end_time == datetime(2026, 4, 20, 21, 0, tzinfo=ZoneInfo("America/New_York"))


class _FakeLlm:
    def __init__(self, response):
        self.response = response
        self.prompts = []

    @property
    def call_count(self):
        return len(self.prompts)

    def extract_event_json(self, prompt):
        self.prompts.append(prompt)
        return self.response
