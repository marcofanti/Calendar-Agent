from __future__ import annotations

import os
from pathlib import Path

from calendar_agent.debug import debug_log, exception_summary
from calendar_agent.email_client import ImapEmailClient
from calendar_agent.google_calendar import GoogleCalendarClient
from calendar_agent.ics import write_ics_file
from calendar_agent.learning.store import LearningStore
from calendar_agent.llm import LlmClient, llm_from_env
from calendar_agent.models import EmailRecord, GolfEvent, RunResult
from calendar_agent.outlook_calendar import OutlookCalendarClient
from calendar_agent.parser import ParseAttempt, parse_email
from calendar_agent.profiles import SourceProfile, match_profile, profiles_from_env


def run_sync(
    email_client: ImapEmailClient,
    google_client: GoogleCalendarClient,
    outlook_client: OutlookCalendarClient | None,
    ics_output_dir: Path,
    dry_run: bool = False,
    llm_client: LlmClient | None = None,
    debug: bool = False,
    profiles: list[SourceProfile] | None = None,
) -> RunResult:
    result = RunResult()
    debug_log("searching mailbox for InClubGolf emails", debug)
    emails = email_client.search_inclubgolf()
    result.emails_found = len(emails)
    debug_log(f"mailbox search returned {len(emails)} email(s)", debug)
    llm_client = llm_client if llm_client is not None else llm_from_env()

    if profiles is None:
        try:
            profiles = profiles_from_env()
        except Exception as exc:
            debug_log(f"profile load failed: {exception_summary(exc)}", debug)
            profiles = []

    learning_dir = Path(os.getenv("AGENT_LEARNING_DIR", ".calendar-agent/learned"))
    max_prompt_chars = int(os.getenv("MAX_PROMPT_CHARS", "12000"))

    for email in emails:
        debug_log(
            f"processing email uid={email.uid} folder={email.folder!r} subject={email.subject!r} body_chars={len(email.body)}",
            debug,
        )

        profile = match_profile(email, profiles) if profiles else None
        debug_log(f"email uid={email.uid}: matched profile={profile.name if profile else None}", debug)

        prompt_context = ""
        if profile is not None:
            store = LearningStore(profile.name, base_dir=learning_dir)
            if store.is_ignored(email.subject):
                debug_log(f"email uid={email.uid}: subject matches ignored pattern, skipping", debug)
                result.skipped.append(f"{email.uid}: ignored (subject matches learned ignore list)")
                continue
            prompt_context = store.build_prompt_context(max_chars=max_prompt_chars)

        try:
            attempt = parse_email(
                email,
                llm_client=llm_client,
                debug=debug,
                profile=profile,
                prompt_context=prompt_context,
            )
        except Exception as exc:
            summary = exception_summary(exc)
            debug_log(f"email uid={email.uid}: LLM parse failed: {summary}", debug)
            result.errors.append(f"{email.uid}: LLM parse failed: {summary}")
            continue

        if attempt.event is None:
            reason = attempt.trace.failure_reason or "unknown"
            debug_log(f"email uid={email.uid}: parse failed — {reason}", debug)
            if reason.startswith("LLM call failed"):
                result.errors.append(f"{email.uid}: {reason}")
            else:
                result.skipped.append(f"{email.uid}: could not parse {email.subject!r}")
            continue

        _sync_event(
            event=attempt.event,
            email=email,
            email_client=email_client,
            google_client=google_client,
            outlook_client=outlook_client,
            ics_output_dir=ics_output_dir,
            dry_run=dry_run,
            debug=debug,
            result=result,
        )

    return result


def _sync_event(
    event: GolfEvent,
    email: EmailRecord,
    email_client: ImapEmailClient,
    google_client: GoogleCalendarClient,
    outlook_client: OutlookCalendarClient | None,
    ics_output_dir: Path,
    dry_run: bool,
    debug: bool,
    result: RunResult,
) -> None:
    result.events_parsed += 1
    event_ok_for_cleanup = False
    debug_log(
        f"email uid={email.uid}: parsed {event.status} {event.event_type} "
        f"{event.start_time.isoformat()} location={event.location!r} event_uid={event.event_uid}",
        debug,
    )

    try:
        if not dry_run:
            google_client.upsert_event(event)
        else:
            debug_log(f"dry-run: would upsert Google event {event.event_uid}", debug)
        result.google_success.add(event.event_uid)
    except Exception as exc:
        summary = exception_summary(exc)
        debug_log(f"email uid={email.uid}: Google sync failed: {summary}", debug)
        result.errors.append(f"{email.uid}: Google sync failed: {summary}")
        return

    outlook_synced = False
    if outlook_client is not None and outlook_client.available():
        try:
            if not dry_run:
                outlook_synced = outlook_client.upsert_event(event)
            else:
                debug_log(f"dry-run: would upsert Outlook event {event.event_uid}", debug)
                outlook_synced = True
        except Exception as exc:
            summary = exception_summary(exc)
            debug_log(f"email uid={email.uid}: Outlook sync failed, using ICS fallback: {summary}", debug)
            result.errors.append(f"{email.uid}: Outlook sync failed, using ICS fallback: {summary}")

    if outlook_synced:
        result.outlook_success.add(event.event_uid)
        event_ok_for_cleanup = True
    else:
        try:
            if not dry_run:
                write_ics_file(event, ics_output_dir)
            else:
                debug_log(f"dry-run: would write ICS for {event.event_uid} to {ics_output_dir}", debug)
            result.ics_success.add(event.event_uid)
            event_ok_for_cleanup = True
        except Exception as exc:
            summary = exception_summary(exc)
            debug_log(f"email uid={email.uid}: ICS generation failed: {summary}", debug)
            result.errors.append(f"{email.uid}: ICS generation failed: {summary}")

    if event_ok_for_cleanup:
        try:
            if not dry_run:
                if not email_client.move_to_trash(email.uid):
                    raise RuntimeError("IMAP move_to_trash returned false")
            else:
                debug_log(f"dry-run: would move email uid={email.uid} to trash", debug)
            result.trashed_uids.add(email.uid)
        except Exception as exc:
            summary = exception_summary(exc)
            debug_log(f"email uid={email.uid}: cleanup failed: {summary}", debug)
            result.errors.append(f"{email.uid}: cleanup failed: {summary}")
