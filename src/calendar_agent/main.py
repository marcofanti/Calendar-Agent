from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from calendar_agent.debug import debug_enabled, debug_log
from calendar_agent.email_client import build_email_client_from_env
from calendar_agent.google_calendar import GoogleCalendarClient
from calendar_agent.outlook_calendar import OutlookCalendarClient
from calendar_agent.workflow import run_sync


class _DryRunGoogleClient:
    def upsert_event(self, event):
        return event.event_uid


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync InClubGolf emails to calendars.")
    parser.add_argument("--dry-run", action="store_true", help="Parse and report without changing calendars or mail.")
    parser.add_argument("--debug", action="store_true", help="Print provider, parsing, and per-email debug logs.")
    args = parser.parse_args()

    load_dotenv()
    dry_run = args.dry_run or os.getenv("DRY_RUN", "false").lower() in {"1", "true", "yes"}
    debug = args.debug or debug_enabled()
    ics_output_dir = Path(os.getenv("ICS_OUTPUT_DIR", "~/Downloads/inclubgolf"))
    debug_log(f"mode dry_run={dry_run}", debug)
    debug_log(f"ics_output_dir={ics_output_dir}", debug)

    email_client = build_email_client_from_env(debug=debug)
    if dry_run:
        google_client = _DryRunGoogleClient()
        outlook_client = None
    else:
        google_client = GoogleCalendarClient()
        outlook_client = OutlookCalendarClient()

    result = run_sync(
        email_client=email_client,
        google_client=google_client,
        outlook_client=outlook_client,
        ics_output_dir=ics_output_dir,
        dry_run=dry_run,
        debug=debug,
    )

    mode = "DRY RUN" if dry_run else "APPLIED"
    print(f"{mode}: InClubGolf sync complete")
    print(f"Emails found: {result.emails_found}")
    print(f"Events parsed: {result.events_parsed}")
    print(f"Emails not parsed: {result.not_parsed_count}")
    print(f"Google synced: {len(result.google_success)}")
    print(f"Outlook synced: {len(result.outlook_success)}")
    print(f"ICS files written: {len(result.ics_success)}")
    print(f"Emails moved to Trash: {len(result.trashed_uids)}")
    if result.skipped:
        print(f"Skipped / not calendar events: {len(result.skipped)}")
        for item in result.skipped:
            print(f"  - {item}")
    if result.errors:
        print(f"Errors: {len(result.errors)}")
        for item in result.errors:
            print(f"  - {item}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
