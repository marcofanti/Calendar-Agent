from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from calendar_agent.debug import debug_log, exception_summary
from calendar_agent.llm import LlmClient, llm_from_env
from calendar_agent.models import EmailRecord, GolfEvent


EASTERN = ZoneInfo("America/New_York")
FROM_ADDRESS = "noreply@inclubgolf.com"
DATE_TIME_RE = re.compile(
    r"\bat\s+(?P<location>.+?)\s+on\s+"
    r"(?P<date>\d{1,2}/\d{1,2}/\d{4})\s+at\s+"
    r"(?P<time>\d{1,2}:\d{2}\s*[AP]\.?M\.?)",
    re.IGNORECASE | re.DOTALL,
)
LOOSE_DATE_TIME_RE = re.compile(
    r"\b(?:on\s+)?(?P<date>\d{1,2}/\d{1,2}/\d{4})\s+at\s+"
    r"(?P<time>\d{1,2}:\d{2}\s*[AP]\.?M\.?)",
    re.IGNORECASE,
)
SUBJECT_LOCATION_RE = re.compile(
    r"\b(?:Practice|Lesson)\s+(?:Confirmed|Canceled|Cancelled)\s+in\s+(?P<area>.+?)\s+-\s+(?P<place>.+)$",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def parse_inclubgolf_email(
    email: EmailRecord,
    llm_client: LlmClient | None = None,
    debug: bool = False,
) -> GolfEvent | None:
    text = _normalize_text(f"{email.subject}\n{email.body}")
    deterministic_candidate = _parse_deterministically(email.subject, email.body)
    debug_log(
        f"parser uid={email.uid}: subject={email.subject!r} body_preview={_preview(email.body)!r}",
        debug,
    )
    debug_log(
        f"parser uid={email.uid}: deterministic_candidate={_candidate_for_prompt(deterministic_candidate)}",
        debug,
    )
    parsed = _parse_with_llm(
        email=email,
        deterministic_candidate=deterministic_candidate,
        llm_client=llm_client if llm_client is not None else llm_from_env(),
        debug=debug,
    )
    if parsed is None:
        return None

    event_type = parsed["event_type"]
    status = parsed["status"]
    location = _normalize_location(parsed["location"])
    start_time = parsed["start_time"]
    end_time = start_time + timedelta(minutes=20)
    cancel_url = _extract_cancel_url(text)
    event_uid = make_event_uid(event_type, location, start_time)
    description = _build_description(event_type, status, location, start_time, cancel_url)

    return GolfEvent(
        source_uid=email.uid,
        source_message_id=email.message_id,
        event_uid=event_uid,
        event_type=event_type,
        status=status,
        location=location,
        description=description,
        start_time=start_time,
        end_time=end_time,
        cancel_url=cancel_url,
    )


def make_event_uid(event_type: str, location: str, start_time: datetime) -> str:
    material = "|".join(
        [
            event_type.strip().lower(),
            _normalize_location(location).lower(),
            start_time.astimezone(EASTERN).strftime("%Y-%m-%dT%H:%M"),
        ]
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]
    return f"inclubgolf-{digest}"


def _parse_deterministically(subject: str, body: str) -> dict | None:
    subject_text = _normalize_text(subject)
    body_text = _normalize_text(body)
    combined_text = _normalize_text(f"{subject_text} {body_text}")
    match = DATE_TIME_RE.search(body_text)

    event_type = _extract_event_type(combined_text)
    status = _extract_status(combined_text)
    if event_type is None or status is None:
        return None

    if match:
        location = match.group("location")
        date_value = match.group("date")
        time_value = match.group("time")
    else:
        loose_match = LOOSE_DATE_TIME_RE.search(body_text)
        subject_location = SUBJECT_LOCATION_RE.search(subject_text)
        if not loose_match or not subject_location:
            return None
        location = f"{subject_location.group('place')} in {subject_location.group('area')}"
        date_value = loose_match.group("date")
        time_value = loose_match.group("time")

    start_time = datetime.strptime(
        f"{date_value} {time_value.replace('.', '')}",
        "%m/%d/%Y %I:%M %p",
    ).replace(tzinfo=EASTERN)

    return {
        "event_type": event_type,
        "status": status,
        "location": location,
        "start_time": start_time,
    }


def _parse_with_llm(
    email: EmailRecord,
    deterministic_candidate: dict | None,
    llm_client: LlmClient,
    debug: bool = False,
) -> dict | None:
    prompt = _build_llm_prompt(email, deterministic_candidate)
    debug_log(
        f"parser uid={email.uid}: sending LLM prompt with subject/body and candidate; prompt_chars={len(prompt)}",
        debug,
    )
    data = llm_client.extract_event_json(prompt)
    debug_log(f"parser uid={email.uid}: llm_response={data}", debug)
    if data.get("is_event") is False:
        debug_log(f"parser uid={email.uid}: rejected because is_event=false", debug)
        return None

    event_type = _valid_event_type(data.get("event_type"))
    status = _valid_status(data.get("status"))
    if event_type is None or status is None:
        debug_log(
            f"parser uid={email.uid}: invalid event_type/status event_type={data.get('event_type')!r} status={data.get('status')!r}",
            debug,
        )
        return None

    missing = [field for field in ("location", "date", "time") if not data.get(field)]
    if missing:
        debug_log(f"parser uid={email.uid}: missing required field(s): {', '.join(missing)}", debug)
        return None

    try:
        start_time = datetime.strptime(
            f"{data['date']} {data['time']}",
            "%m/%d/%Y %I:%M %p",
        ).replace(tzinfo=EASTERN)
    except Exception as exc:
        debug_log(
            f"parser uid={email.uid}: invalid date/time date={data.get('date')!r} time={data.get('time')!r}: {exception_summary(exc)}",
            debug,
        )
        return None

    return {
        "event_type": event_type,
        "status": status,
        "location": str(data["location"]),
        "start_time": start_time,
    }


def _build_llm_prompt(email: EmailRecord, deterministic_candidate: dict | None) -> str:
    candidate = _candidate_for_prompt(deterministic_candidate)

    return f"""
Extract and validate one InClubGolf calendar event from this email.

Use the deterministic_candidate as a proposed parse. Confirm it if correct; correct it if the
email text shows different event details. This LLM response is required even when the candidate
looks complete.

Return one JSON object only:
{{
  "is_event": true,
  "event_type": "Practice" or "Lesson",
  "status": "Reserved" or "Canceled",
  "location": "clean location string",
  "date": "MM/DD/YYYY",
  "time": "HH:MM AM/PM",
  "confidence": number between 0 and 1
}}

If this is not a lesson/practice reservation or cancellation, return:
{{"is_event": false, "confidence": 0}}

Rules:
- All event times are America/New_York.
- Do not invent missing date, time, type, status, or location.
- Treat "cancelled" and "canceled" as "Canceled".
- Treat confirmed/reserved bookings as "Reserved".
- Keep the location in the form used by the email, for example "Lake Nona in North Bay".

deterministic_candidate:
{json.dumps(candidate, indent=2)}

Subject:
{email.subject}

Body:
{email.body}
""".strip()


def _extract_event_type(text: str) -> str | None:
    if re.search(r"\bpractice\b", text, re.IGNORECASE):
        return "Practice"
    if re.search(r"\blesson\b", text, re.IGNORECASE):
        return "Lesson"
    return None


def _extract_status(text: str) -> str | None:
    if re.search(r"\bcancell?ed\b", text, re.IGNORECASE):
        return "Canceled"
    if re.search(r"\bconfirmed\b|\breserved\b", text, re.IGNORECASE):
        return "Reserved"
    return None


def _valid_event_type(value) -> str | None:
    if isinstance(value, str) and value.strip().lower() == "practice":
        return "Practice"
    if isinstance(value, str) and value.strip().lower() == "lesson":
        return "Lesson"
    return None


def _valid_status(value) -> str | None:
    if isinstance(value, str) and value.strip().lower() in {"canceled", "cancelled"}:
        return "Canceled"
    if isinstance(value, str) and value.strip().lower() == "reserved":
        return "Reserved"
    return None


def _extract_cancel_url(text: str) -> str | None:
    for match in URL_RE.finditer(text):
        url = match.group(0).rstrip(").,")
        if "Scheduler/Cancel" in url:
            return url
    return None


def _build_description(
    event_type: str,
    status: str,
    location: str,
    start_time: datetime,
    cancel_url: str | None,
) -> str:
    action = "canceled" if status == "Canceled" else "reserved"
    formatted_time = start_time.astimezone(EASTERN).strftime("%A, %B %-d, %Y at %-I:%M %p %Z")
    lines = [
        f"InClubGolf {event_type.lower()} {action}.",
        f"Location: {location}",
        f"Time: {formatted_time}",
        "Duration: 20 minutes",
    ]
    if cancel_url:
        lines.append(f"Cancellation link: {cancel_url}")
    return "\n".join(lines)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _normalize_location(location: str) -> str:
    clean = _normalize_text(location)
    clean = re.sub(r"^(?:a|your)\s+", "", clean, flags=re.IGNORECASE)
    clean = clean.strip(" .")
    return clean


def _candidate_for_prompt(deterministic_candidate: dict | None) -> dict | None:
    if deterministic_candidate is None:
        return None
    return {
        "event_type": deterministic_candidate["event_type"],
        "status": deterministic_candidate["status"],
        "location": deterministic_candidate["location"],
        "start_time_eastern": deterministic_candidate["start_time"].strftime("%Y-%m-%d %H:%M"),
    }


def _preview(value: str, max_chars: int = 500) -> str:
    clean = _normalize_text(value)
    if len(clean) <= max_chars:
        return clean
    return clean[:max_chars] + "..."
