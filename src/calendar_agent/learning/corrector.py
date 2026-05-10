from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable, Literal
from zoneinfo import ZoneInfo

from calendar_agent.learning.store import LearningStore
from calendar_agent.models import EmailRecord, GolfEvent
from calendar_agent.parser import ParseAttempt, ParseTrace, make_event_uid

if TYPE_CHECKING:
    from calendar_agent.profiles import SourceProfile
    from calendar_agent.terminal_ui import TerminalUI


EASTERN = ZoneInfo("America/New_York")

CorrectionOutcome = Literal["synced", "ignored", "skipped", "stop"]


@dataclass
class CorrectionResult:
    outcome: CorrectionOutcome
    event: GolfEvent | None = None
    correction_saved: bool = False
    prompt_updated: bool = False


def run_correction_loop(
    email: EmailRecord,
    attempt: ParseAttempt,
    profile: SourceProfile | None,
    store: LearningStore | None,
    ui: TerminalUI,
    sync_fn: Callable[[GolfEvent], bool],
) -> CorrectionResult:
    """
    Interactive loop for a single failed email.
    sync_fn(event) -> True if sync succeeded.
    """
    ui.show_trace(attempt.trace)
    answer = ui.ask_is_event()

    if answer == "skip_all":
        return CorrectionResult(outcome="stop")

    if answer == "skip":
        return CorrectionResult(outcome="skipped")

    if answer == "no":
        if store is not None:
            if ui.ask_ignore_permanently():
                pattern = store.new_ignored(
                    subject_pattern=re.escape(email.subject),
                    reason="operator marked as non-event",
                )
                store.save_ignored(pattern)
                ui.info(f"Saved ignore pattern for: {email.subject!r}")
        return CorrectionResult(outcome="ignored")

    # answer == "yes" — walk through fields
    from calendar_agent.profiles import DEFAULT_DURATION_MINUTES
    duration = profile.duration_minutes if profile is not None else DEFAULT_DURATION_MINUTES

    correction = _collect_fields(attempt.trace, ui)
    if correction is None:
        return CorrectionResult(outcome="skipped")

    event = _build_event_from_correction(email, correction, duration_minutes=duration)

    while True:
        if not ui.ask_run_sync():
            return CorrectionResult(outcome="skipped")

        success = sync_fn(event)
        if success:
            break
        ui.info("Sync failed. You can edit fields and retry, or skip.")
        correction = _collect_fields(attempt.trace, ui, defaults=correction)
        if correction is None:
            return CorrectionResult(outcome="skipped")
        event = _build_event_from_correction(email, correction, duration_minutes=duration)

    correction_saved = False
    prompt_updated = False

    if store is not None and ui.ask_save_correction():
        example = store.new_example(
            subject=email.subject,
            body_preview=attempt.trace.body_preview,
            correction=correction,
        )
        store.save_example(example)
        correction_saved = True
        ui.info("Correction saved.")

        prompt_updated = _maybe_save_prompt_override(
            attempt.trace, correction, store, ui
        )

    return CorrectionResult(
        outcome="synced",
        event=event,
        correction_saved=correction_saved,
        prompt_updated=prompt_updated,
    )


def _collect_fields(
    trace: ParseTrace,
    ui: TerminalUI,
    defaults: dict | None = None,
) -> dict | None:
    llm = trace.llm_response or {}
    det = trace.deterministic_candidate or {}
    prior = defaults or {}

    def _default(key: str) -> str:
        return prior.get(key) or llm.get(key) or det.get(key) or ""

    ui.info("\nEnter corrected fields (press Enter to keep current value):")

    while True:
        event_type = ui.ask_field("event_type", _default("event_type"), ["Practice", "Lesson"])
        status = ui.ask_field("status", _default("status"), ["Reserved", "Canceled"])
        location = ui.ask_field("location", _default("location"))
        date = ui.ask_field("date (MM/DD/YYYY)", _default("date"))
        time_ = ui.ask_field("time (HH:MM AM/PM)", _default("time"))

        errors = _validate_fields(event_type, status, location, date, time_)
        if not errors:
            return {
                "event_type": _norm_event_type(event_type),
                "status": _norm_status(status),
                "location": location.strip(),
                "date": date.strip(),
                "time": time_.strip(),
            }

        ui.info(f"  Validation errors: {'; '.join(errors)}")
        ui.info("  Please re-enter the fields above.")


def _validate_fields(
    event_type: str, status: str, location: str, date: str, time_: str
) -> list[str]:
    errors = []
    if _norm_event_type(event_type) is None:
        errors.append("event_type must be Practice or Lesson")
    if _norm_status(status) is None:
        errors.append("status must be Reserved or Canceled")
    if not location.strip():
        errors.append("location is required")
    try:
        datetime.strptime(f"{date.strip()} {time_.strip()}", "%m/%d/%Y %I:%M %p")
    except ValueError:
        errors.append("date/time must be MM/DD/YYYY HH:MM AM/PM")
    return errors


def _build_event_from_correction(
    email: EmailRecord,
    correction: dict,
    duration_minutes: int = 30,
) -> GolfEvent:
    event_type = correction["event_type"]
    status = correction["status"]
    location = correction["location"]
    start_time = datetime.strptime(
        f"{correction['date']} {correction['time']}",
        "%m/%d/%Y %I:%M %p",
    ).replace(tzinfo=EASTERN)
    end_time = start_time + timedelta(minutes=duration_minutes)
    event_uid = make_event_uid(event_type, location, start_time)
    action = "canceled" if status == "Canceled" else "reserved"
    formatted = start_time.strftime("%A, %B %-d, %Y at %-I:%M %p %Z")
    description = "\n".join([
        f"InClubGolf {event_type.lower()} {action}.",
        f"Location: {location}",
        f"Time: {formatted}",
        f"Duration: {duration_minutes} minutes",
        "Created from interactive correction.",
    ])
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
    )


def _maybe_save_prompt_override(
    trace: ParseTrace,
    correction: dict,
    store: LearningStore,
    ui: TerminalUI,
) -> bool:
    """Ask if the user wants to add a prompt rule derived from this correction."""
    ui.info("\nOptional: add a prompt rule to help the LLM handle similar emails.")
    ui.info("Example: \"Practice Canceled without a date means the event was canceled, return is_event=false\"")
    raw = ui.ask_field("Rule (leave blank to skip)", None)
    if not raw.strip():
        return False
    override = store.new_override(
        content=raw.strip(),
        trigger_subject=trace.subject,
    )
    store.save_override(override)
    ui.info("Prompt rule saved.")
    return True


def _norm_event_type(value: str) -> str | None:
    v = value.strip().lower()
    if v == "practice":
        return "Practice"
    if v == "lesson":
        return "Lesson"
    return None


def _norm_status(value: str) -> str | None:
    v = value.strip().lower()
    if v == "reserved":
        return "Reserved"
    if v in {"canceled", "cancelled"}:
        return "Canceled"
    return None
