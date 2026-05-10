from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from calendar_agent.models import EmailRecord, GolfEvent
from calendar_agent.workflow import run_sync


def test_cleanup_happens_after_google_and_ics_success(tmp_path):
    email_client = _EmailClient()
    google_client = _GoogleClient()

    result = run_sync(
        email_client=email_client,
        google_client=google_client,
        outlook_client=None,
        ics_output_dir=tmp_path,
        llm_client=_LlmClient(),
    )

    assert len(result.google_success) == 1
    assert len(result.ics_success) == 1
    assert result.trashed_uids == {"101"}
    assert email_client.trashed == ["101"]


def test_cleanup_does_not_happen_when_google_fails(tmp_path):
    email_client = _EmailClient()
    google_client = _GoogleClient(should_fail=True)

    result = run_sync(
        email_client=email_client,
        google_client=google_client,
        outlook_client=None,
        ics_output_dir=tmp_path,
        llm_client=_LlmClient(),
    )

    assert not result.google_success
    assert not result.ics_success
    assert not result.trashed_uids
    assert email_client.trashed == []


def test_dry_run_does_not_mutate_but_counts_success(tmp_path):
    email_client = _EmailClient()
    google_client = _GoogleClient()

    result = run_sync(
        email_client=email_client,
        google_client=google_client,
        outlook_client=None,
        ics_output_dir=tmp_path,
        dry_run=True,
        llm_client=_LlmClient(),
    )

    assert len(result.google_success) == 1
    assert len(result.ics_success) == 1
    assert result.trashed_uids == {"101"}
    assert email_client.trashed == []
    assert list(Path(tmp_path).iterdir()) == []


def test_parse_failure_records_exception_type(tmp_path):
    result = run_sync(
        email_client=_EmailClient(),
        google_client=_GoogleClient(),
        outlook_client=None,
        ics_output_dir=tmp_path,
        dry_run=True,
        llm_client=_FailingLlmClient(),
    )

    assert result.errors == ["101: LLM call failed: RuntimeError: ollama unavailable"]


def test_not_parsed_count_reconciles_found_and_parsed(tmp_path):
    result = run_sync(
        email_client=_TwoEmailClient(),
        google_client=_GoogleClient(),
        outlook_client=None,
        ics_output_dir=tmp_path,
        dry_run=True,
        llm_client=_OneEventOneNonEventLlmClient(),
    )

    assert result.emails_found == 2
    assert result.events_parsed == 1
    assert result.not_parsed_count == 1
    assert result.skipped == ["102: could not parse 'Receipt'"]


class _EmailClient:
    def __init__(self):
        self.trashed = []

    def search_inclubgolf(self):
        return [
            EmailRecord(
                uid="101",
                message_id="msg-101",
                subject="Practice Confirmed in North Bay - Lake Nona",
                body=(
                    "You have successfully reserved a practice session at Lake Nona in North Bay "
                    "on 05/14/2026 at 05:40 PM."
                ),
            )
        ]

    def move_to_trash(self, uid):
        self.trashed.append(uid)
        return True


class _TwoEmailClient(_EmailClient):
    def search_inclubgolf(self):
        return [
            EmailRecord(
                uid="101",
                message_id="msg-101",
                subject="Practice Confirmed in North Bay - Lake Nona",
                body=(
                    "You have successfully reserved a practice session at Lake Nona in North Bay "
                    "on 05/14/2026 at 05:40 PM."
                ),
            ),
            EmailRecord(
                uid="102",
                message_id="msg-102",
                subject="Receipt",
                body="Thanks for your payment.",
            ),
        ]


class _GoogleClient:
    def __init__(self, should_fail=False):
        self.should_fail = should_fail

    def upsert_event(self, event):
        if self.should_fail:
            raise RuntimeError("boom")
        return "google-id"


class _LlmClient:
    def extract_event_json(self, prompt):
        return {
            "is_event": True,
            "event_type": "Practice",
            "status": "Reserved",
            "location": "Lake Nona in North Bay",
            "date": "05/14/2026",
            "time": "05:40 PM",
            "confidence": 1,
        }


class _FailingLlmClient:
    def extract_event_json(self, prompt):
        raise RuntimeError("ollama unavailable")


class _OneEventOneNonEventLlmClient:
    def extract_event_json(self, prompt):
        if "Receipt" in prompt:
            return {"is_event": False, "confidence": 0}
        return {
            "is_event": True,
            "event_type": "Practice",
            "status": "Reserved",
            "location": "Lake Nona in North Bay",
            "date": "05/14/2026",
            "time": "05:40 PM",
            "confidence": 1,
        }
