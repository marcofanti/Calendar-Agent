from __future__ import annotations

import os
from pathlib import Path

from calendar_agent.debug import debug_log, exception_summary
from calendar_agent.email_client import ImapEmailClient
from calendar_agent.google_calendar import GoogleCalendarClient
from calendar_agent.ics import write_ics_file
from calendar_agent.learning import run_correction_loop
from calendar_agent.learning.store import LearningStore
from calendar_agent.llm import LlmClient, llm_from_env
from calendar_agent.models import EmailRecord, GolfEvent, RunResult
from calendar_agent.outlook_calendar import OutlookCalendarClient
from calendar_agent.parser import ParseAttempt, parse_email
from calendar_agent.profiles import MailAccount, SourceProfile, match_profile, profiles_config_from_env


def run_sync(
    email_client: ImapEmailClient,
    google_client: GoogleCalendarClient,
    outlook_client: OutlookCalendarClient | None,
    ics_output_dir: Path,
    dry_run: bool = False,
    llm_client: LlmClient | None = None,
    debug: bool = False,
    profiles: list[SourceProfile] | None = None,
    accounts: list[MailAccount] | None = None,
    ui=None,  # TerminalUI | None — avoid circular import at module level
) -> RunResult:
    result = RunResult()
    llm_client = llm_client if llm_client is not None else llm_from_env()

    if profiles is None:
        try:
            profiles, loaded_accounts = profiles_config_from_env()
            if accounts is None:
                accounts = loaded_accounts
        except Exception as exc:
            debug_log(f"profile load failed: {exception_summary(exc)}", debug)
            profiles = []
    if accounts is None:
        accounts = []

    # Build per-account client cache; None key = the default email_client argument.
    accounts_by_name: dict[str, MailAccount] = {a.name: a for a in accounts}
    per_account_clients: dict[str | None, object] = {None: email_client}

    def _get_client(account_name: str | None) -> object:
        if account_name not in per_account_clients:
            account = accounts_by_name.get(account_name)  # type: ignore[arg-type]
            if account is None:
                raise ValueError(
                    f"Profile references unknown mail_account {account_name!r}; "
                    "add it to the accounts: section in profiles.yaml"
                )
            per_account_clients[account_name] = _build_client_for_account(account, debug=debug)
        return per_account_clients[account_name]

    # Group by (mail_account, mailbox) so we issue one search per account+folder pair.
    from collections import defaultdict
    groups: dict[tuple[str | None, str | None], list[str]] = defaultdict(list)
    if profiles:
        for p in profiles:
            groups[(p.mail_account, p.mailbox)].append(p.from_search_term)
    else:
        groups[(None, None)].append("noreply@inclubgolf.com")

    seen_uids: set[str] = set()
    emails: list[EmailRecord] = []
    uid_to_client: dict[str, object] = {}  # maps uid → client, for move_to_trash

    for (account_name, mailbox), addrs in groups.items():
        try:
            client = _get_client(account_name)
        except Exception as exc:
            result.errors.append(f"account {account_name!r}: {exception_summary(exc)}")
            continue
        parts = []
        if account_name:
            parts.append(f"account={account_name!r}")
        if mailbox:
            parts.append(f"folder={mailbox!r}")
        debug_log(f"searching {' '.join(parts) or 'default'} for: {', '.join(addrs)}", debug)
        try:
            fetched = list(client.search_emails(addrs, folder=mailbox))
        except Exception as exc:
            # Auth or connectivity failure for this account — log and continue.
            # Use debug_log so secondary-account misconfigurations don't surface
            # as errors that break the summary (run with --debug to investigate).
            debug_log(
                f"account {account_name or 'default'}: search failed: {exception_summary(exc)}",
                debug,
            )
            continue
        for e in fetched:
            if e.uid not in seen_uids:
                seen_uids.add(e.uid)
                emails.append(e)
                uid_to_client[e.uid] = client

    result.emails_found = len(emails)
    debug_log(f"mailbox search returned {len(emails)} email(s)", debug)

    learning_dir = Path(os.getenv("AGENT_LEARNING_DIR", ".calendar-agent/learned"))
    max_prompt_chars = int(os.getenv("MAX_PROMPT_CHARS", "12000"))

    # Collect (email, attempt, profile, store, client) for failed parses — correction loop.
    pending: list[tuple[EmailRecord, ParseAttempt, SourceProfile | None, LearningStore | None, object]] = []

    for email in emails:
        debug_log(
            f"processing email uid={email.uid} folder={email.folder!r} subject={email.subject!r} body_chars={len(email.body)}",
            debug,
        )

        profile = match_profile(email, profiles) if profiles else None
        debug_log(f"email uid={email.uid}: matched profile={profile.name if profile else None}", debug)

        store: LearningStore | None = None
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
                pending.append((email, attempt, profile, store, uid_to_client.get(email.uid, email_client)))
            continue

        _sync_event(
            event=attempt.event,
            email=email,
            email_client=uid_to_client.get(email.uid, email_client),
            google_client=google_client,
            outlook_client=outlook_client,
            ics_output_dir=ics_output_dir,
            dry_run=dry_run,
            debug=debug,
            result=result,
        )

    # --- interactive correction loop ---
    if ui is not None and ui.is_interactive() and pending:
        _run_corrections(
            pending=pending,
            result=result,
            google_client=google_client,
            outlook_client=outlook_client,
            ics_output_dir=ics_output_dir,
            dry_run=dry_run,
            debug=debug,
            ui=ui,
        )

    return result


def _run_corrections(
    pending,
    result: RunResult,
    google_client,
    outlook_client,
    ics_output_dir,
    dry_run,
    debug,
    ui,
) -> None:
    ui.info(f"\n{len(pending)} email(s) failed to parse. Starting correction loop...")

    for email, attempt, profile, store, email_client_for_email in pending:
        def sync_fn(event: GolfEvent, _email=email, _client=email_client_for_email) -> bool:
            try:
                _sync_event(
                    event=event,
                    email=_email,
                    email_client=_client,
                    google_client=google_client,
                    outlook_client=outlook_client,
                    ics_output_dir=ics_output_dir,
                    dry_run=dry_run,
                    debug=debug,
                    result=result,
                )
                return True
            except Exception as exc:
                ui.info(f"Sync error: {exception_summary(exc)}")
                return False

        correction = run_correction_loop(
            email=email,
            attempt=attempt,
            profile=profile,
            store=store,
            ui=ui,
            sync_fn=sync_fn,
        )

        if correction.outcome == "synced":
            # Remove the skipped entry we added earlier
            skipped_key = f"{email.uid}: could not parse {email.subject!r}"
            if skipped_key in result.skipped:
                result.skipped.remove(skipped_key)
            if correction.correction_saved:
                result.corrections_saved += 1
            if correction.prompt_updated:
                result.prompts_updated += 1

        elif correction.outcome == "stop":
            ui.info("Stopping correction loop.")
            break


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
            resolved_dir = ics_output_dir.expanduser().resolve()
            ics_filename = f"{event.start_time.strftime('%Y-%m-%d_%H%M')}.ics"
            if not dry_run:
                ics_path = write_ics_file(event, ics_output_dir)
                debug_log(f"email uid={email.uid}: wrote ICS to {ics_path.resolve()}", debug)
            else:
                debug_log(f"dry-run: would write ICS to {resolved_dir / ics_filename}", debug)
            result.ics_success.add(event.event_uid)
            event_ok_for_cleanup = True
        except Exception as exc:
            summary = exception_summary(exc)
            debug_log(f"email uid={email.uid}: ICS generation failed: {summary}", debug)
            result.errors.append(f"{email.uid}: ICS generation failed: {summary}")

    if event_ok_for_cleanup:
        try:
            if not dry_run:
                if not email_client.move_to_trash(email.uid, folder=email.folder):
                    raise RuntimeError("IMAP move_to_trash returned false")
            else:
                debug_log(f"dry-run: would move email uid={email.uid} to trash", debug)
            result.trashed_uids.add(email.uid)
        except Exception as exc:
            summary = exception_summary(exc)
            debug_log(f"email uid={email.uid}: cleanup failed: {summary}", debug)
            result.errors.append(f"{email.uid}: cleanup failed: {summary}")


def _build_client_for_account(account: MailAccount, debug: bool = False) -> object:
    from calendar_agent.email_client import GmailApiEmailClient

    if account.provider == "gmail":
        return GmailApiEmailClient(
            debug=debug,
            credentials_file=account.credentials_file,
            token_file=account.token_file,
        )
    raise ValueError(
        f"Unsupported provider {account.provider!r} for account {account.name!r}. "
        "Only 'gmail' is supported in the accounts: section."
    )
