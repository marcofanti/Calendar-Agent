"""Tests for the learning system: store, corrector, and end-to-end correction flow."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from calendar_agent.learning.corrector import run_correction_loop
from calendar_agent.learning.store import LearningStore
from calendar_agent.models import EmailRecord, GolfEvent
from calendar_agent.parser import ParseAttempt, ParseTrace
from calendar_agent.terminal_ui import MockTerminalUI


EASTERN = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# LearningStore
# ---------------------------------------------------------------------------

def test_store_round_trip_example(tmp_path):
    store = LearningStore("test", base_dir=tmp_path)
    ex = store.new_example("Subject", "body...", {"event_type": "Practice"})
    store.save_example(ex)

    loaded = store.load_examples()
    assert len(loaded) == 1
    assert loaded[0].subject == "Subject"
    assert loaded[0].correction == {"event_type": "Practice"}


def test_store_atomic_write_does_not_leave_tmp(tmp_path):
    store = LearningStore("test", base_dir=tmp_path)
    ex = store.new_example("S", "b", {})
    store.save_example(ex)

    tmp_files = list(tmp_path.rglob("*.tmp"))
    assert tmp_files == [], f"leftover .tmp files: {tmp_files}"


def test_store_ignore_pattern_matches(tmp_path):
    store = LearningStore("test", base_dir=tmp_path)
    pat = store.new_ignored(re.escape("Practice Canceled"), "not an event")
    store.save_ignored(pat)

    assert store.is_ignored("Practice Canceled")
    assert not store.is_ignored("Practice Confirmed in North Bay - Lake Nona")


def test_store_prompt_context_includes_examples_and_overrides(tmp_path):
    store = LearningStore("test", base_dir=tmp_path)
    store.save_example(store.new_example("S1", "b", {"event_type": "Lesson"}))
    store.save_override(store.new_override("Some extra rule", "S1"))

    ctx = store.build_prompt_context()
    assert "Some extra rule" in ctx
    assert "Lesson" in ctx


def test_store_prompt_context_trims_old_examples(tmp_path):
    store = LearningStore("test", base_dir=tmp_path)
    big_body = "x" * 3000
    for i in range(5):
        store.save_example(store.new_example(f"Subject {i}", big_body, {}))

    ctx = store.build_prompt_context(max_chars=5000)
    assert len(ctx) <= 5000


# ---------------------------------------------------------------------------
# Corrector — field collection and full loop
# ---------------------------------------------------------------------------

def _make_trace(subject="Practice Canceled", failure="LLM returned is_event=false") -> ParseTrace:
    return ParseTrace(
        uid="555",
        subject=subject,
        body="Your practice session has been canceled.",
        body_preview="Your practice session has been canceled.",
        deterministic_candidate=None,
        prompt="...",
        llm_response={"is_event": False, "confidence": 0},
        failure_reason=failure,
    )


def _make_email(subject="Practice Canceled") -> EmailRecord:
    return EmailRecord(
        uid="555",
        message_id="msg-555",
        subject=subject,
        body="Your practice session has been canceled.",
        sender="noreply@inclubgolf.com",
    )


def _make_attempt(subject="Practice Canceled") -> ParseAttempt:
    return ParseAttempt(event=None, trace=_make_trace(subject))


def test_correction_loop_skip(tmp_path):
    ui = MockTerminalUI(responses=[
        "",      # show_trace: don't show full prompt
        "s",     # ask_is_event: skip
    ])
    store = LearningStore("inclubgolf", base_dir=tmp_path)
    synced = []

    result = run_correction_loop(
        email=_make_email(),
        attempt=_make_attempt(),
        profile=None,
        store=store,
        ui=ui,
        sync_fn=lambda e: synced.append(e) or True,
    )

    assert result.outcome == "skipped"
    assert synced == []


def test_correction_loop_skip_all(tmp_path):
    ui = MockTerminalUI(responses=[
        "",   # show_trace
        "S",  # skip all
    ])
    result = run_correction_loop(
        email=_make_email(),
        attempt=_make_attempt(),
        profile=None,
        store=LearningStore("inclubgolf", base_dir=tmp_path),
        ui=ui,
        sync_fn=lambda e: True,
    )
    assert result.outcome == "stop"


def test_correction_loop_mark_ignored(tmp_path):
    ui = MockTerminalUI(responses=[
        "",   # show_trace
        "n",  # not a calendar event
        "y",  # permanently ignore
    ])
    store = LearningStore("inclubgolf", base_dir=tmp_path)

    result = run_correction_loop(
        email=_make_email(),
        attempt=_make_attempt(),
        profile=None,
        store=store,
        ui=ui,
        sync_fn=lambda e: True,
    )

    assert result.outcome == "ignored"
    assert store.is_ignored("Practice Canceled")


def test_correction_loop_full_success_saves_example(tmp_path):
    ui = MockTerminalUI(responses=[
        "",          # show_trace
        "y",         # is a calendar event
        # field walkthrough
        "Practice",  # event_type
        "Canceled",  # status
        "Lake Nona in North Bay",  # location
        "05/15/2026",  # date
        "10:00 AM",    # time
        # ask_run_sync
        "y",
        # ask_save_correction
        "y",
        # prompt rule
        "",          # no rule
    ])
    store = LearningStore("inclubgolf", base_dir=tmp_path)
    synced: list[GolfEvent] = []

    result = run_correction_loop(
        email=_make_email(),
        attempt=_make_attempt(),
        profile=None,
        store=store,
        ui=ui,
        sync_fn=lambda e: synced.append(e) or True,
    )

    assert result.outcome == "synced"
    assert result.correction_saved is True
    assert len(synced) == 1
    assert synced[0].event_type == "Practice"
    assert synced[0].status == "Canceled"
    assert synced[0].location == "Lake Nona in North Bay"

    examples = store.load_examples()
    assert len(examples) == 1
    assert examples[0].subject == "Practice Canceled"


def test_correction_loop_validation_retry(tmp_path):
    ui = MockTerminalUI(responses=[
        "",           # show_trace
        "y",          # is a calendar event
        # first attempt — bad values
        "BadType",    # invalid event_type
        "Canceled",
        "Lake Nona",
        "05/15/2026",
        "10:00 AM",
        # second attempt — correct values
        "Practice",
        "Canceled",
        "Lake Nona in North Bay",
        "05/15/2026",
        "10:00 AM",
        "y",          # run sync
        "n",          # don't save
    ])

    result = run_correction_loop(
        email=_make_email(),
        attempt=_make_attempt(),
        profile=None,
        store=LearningStore("inclubgolf", base_dir=tmp_path),
        ui=ui,
        sync_fn=lambda e: True,
    )

    assert result.outcome == "synced"
    assert result.correction_saved is False


# ---------------------------------------------------------------------------
# End-to-end: correction loop wired into workflow
# ---------------------------------------------------------------------------

def test_workflow_correction_removes_skipped_entry(tmp_path):
    from calendar_agent.workflow import run_sync

    ui = MockTerminalUI(responses=[
        "",           # show_trace
        "y",          # is event
        "Practice",
        "Canceled",
        "Lake Nona",
        "05/15/2026",
        "10:00 AM",
        "y",          # run sync
        "n",          # don't save
    ])

    result = run_sync(
        email_client=_FailEmailClient(),
        google_client=_GoogleClient(),
        outlook_client=None,
        ics_output_dir=tmp_path,
        dry_run=True,
        llm_client=_NonEventLlmClient(),
        profiles=[],
        ui=ui,
    )

    assert result.events_parsed == 1
    assert result.skipped == []
    assert result.google_success


def test_workflow_no_ui_skips_correction_loop(tmp_path):
    from calendar_agent.workflow import run_sync

    result = run_sync(
        email_client=_FailEmailClient(),
        google_client=_GoogleClient(),
        outlook_client=None,
        ics_output_dir=tmp_path,
        dry_run=True,
        llm_client=_NonEventLlmClient(),
        profiles=[],
        ui=None,
    )

    assert len(result.skipped) == 1
    assert result.events_parsed == 0


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

class _FailEmailClient:
    def __init__(self):
        self.trashed = []

    def search_emails(self, from_addresses):
        return [EmailRecord(
            uid="555",
            message_id="msg-555",
            subject="Practice Canceled",
            body="Your practice was canceled.",
            sender="noreply@inclubgolf.com",
        )]

    def search_inclubgolf(self):
        return self.search_emails(["noreply@inclubgolf.com"])

    def move_to_trash(self, uid):
        self.trashed.append(uid)
        return True


class _GoogleClient:
    def upsert_event(self, event):
        return "google-id"


class _NonEventLlmClient:
    def extract_event_json(self, prompt):
        return {"is_event": False, "confidence": 0}
