from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from calendar_agent.models import EmailRecord
from calendar_agent.parser import parse_inclubgolf_email


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
