from unittest.mock import patch

from calendar_agent import main as main_module


def test_dry_run_does_not_construct_real_google_client(monkeypatch):
    monkeypatch.setenv("IMAP_USER", "user@example.com")
    monkeypatch.setenv("IMAP_PASSWORD", "password")

    with patch.object(main_module, "GoogleCalendarClient") as google_client:
        with patch.object(main_module, "OutlookCalendarClient") as outlook_client:
            with patch.object(main_module, "build_email_client_from_env") as email_client:
                with patch.object(main_module, "run_sync") as run_sync:
                    run_sync.return_value = _Result()

                    with patch("sys.argv", ["calendar-agent", "--dry-run"]):
                        main_module.main()

    google_client.assert_not_called()
    outlook_client.assert_not_called()


class _Result:
    emails_found = 0
    events_parsed = 0
    google_success = set()
    outlook_success = set()
    ics_success = set()
    trashed_uids = set()
    skipped = []
    errors = []

    @property
    def not_parsed_count(self):
        return self.emails_found - self.events_parsed
