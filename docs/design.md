# Technical Design: Configurable Learning Calendar Agent

**Status:** Draft  
**Phase:** 1  
**Date:** 2026-05-10  
**PRD:** [PRD.md](./PRD.md)

---

## 1. Architecture Overview

### Current state

```
main.py
  └── workflow.py
        ├── email_client.py   (hardcoded InClubGolf search)
        ├── parser.py         (hardcoded InClubGolf prompt + regex)
        └── llm.py            (Ollama / Gemini client)
```

### Target state (Phase 1)

```
main.py
  └── workflow.py
        ├── email_client.py       (profile-driven search)
        ├── parser.py             (profile-aware, example-injected prompt)
        ├── llm.py                (unchanged)
        ├── profiles.py           (NEW: load/validate profiles.yaml)
        ├── learning/
        │     ├── store.py        (NEW: read/write examples, overrides, ignored)
        │     └── corrector.py    (NEW: HITL correction loop)
        └── terminal_ui.py        (NEW: interactive I/O abstraction)
```

---

## 2. Profile System

### 2.1 Configuration file

Default path: `profiles.yaml` in the project root. Overridable via `AGENT_PROFILES_FILE`.

```yaml
profiles:
  - name: inclubgolf
    from_pattern: "noreply@inclubgolf\\.com"
    subject_pattern: ".*"          # optional, default .*
    to_pattern: null               # optional
    body_pattern: null             # optional
    prompt_template: |
      Extract and validate one InClubGolf calendar event from this email.
      ...
      Rules:
      - All event times are America/New_York.
      - Treat "cancelled" and "canceled" as "Canceled".
      ...
```

### 2.2 Profile matching

Email is matched against the **first** profile whose `from_pattern` (and optional
`subject_pattern`, `to_pattern`, `body_pattern`) all match. Patterns are compiled once at
startup.

### 2.3 Profile dataclass

```python
@dataclass(frozen=True)
class SourceProfile:
    name: str
    from_pattern: re.Pattern
    subject_pattern: re.Pattern
    to_pattern: re.Pattern | None
    body_pattern: re.Pattern | None
    prompt_template: str
```

### 2.4 Fallback

If no profile matches an email, the email is silently skipped (counted in `skipped`, reason:
`"no matching profile"`). This preserves current behavior for emails not covered by any profile.

---

## 3. Learning Storage

### 3.1 Directory layout

```
.calendar-agent/
  learned/
    {profile_name}/
      examples.json         # few-shot corrections
      ignored.json          # emails confirmed as non-events
      prompt_overrides.yaml # versioned prompt additions
```

`.calendar-agent/` is gitignored by default. The operator can commit it selectively.

### 3.2 examples.json schema

```json
[
  {
    "id": "uuid4",
    "added_at": "2026-05-10T14:30:00Z",
    "subject": "Practice Canceled",
    "body_preview": "...",
    "correction": {
      "event_type": "Practice",
      "status": "Canceled",
      "date": "05/15/2026",
      "time": "10:00 AM",
      "location": "Lake Nona in North Bay"
    },
    "confirmed": true
  }
]
```

### 3.3 ignored.json schema

```json
[
  {
    "id": "uuid4",
    "added_at": "2026-05-10T14:30:00Z",
    "subject_pattern": "Practice Canceled",
    "reason": "operator marked as non-event"
  }
]
```

### 3.4 prompt_overrides.yaml schema

```yaml
overrides:
  - id: uuid4
    added_at: "2026-05-10T14:30:00Z"
    section: "rules"
    content: |
      - "Practice Canceled" without a date means the event was canceled but
        no new booking was created. Return is_event=false for these.
    applied_after_subject: "Practice Canceled"
```

### 3.5 Atomic writes

All writes use `write-then-rename` (write to `{file}.tmp`, then `os.replace`). This prevents
partial state if the process is killed mid-write.

---

## 4. Prompt Construction (Updated)

The LLM prompt is now assembled in layers for each email:

```
[1] Base template from profile.prompt_template
[2] Prompt overrides from prompt_overrides.yaml (appended as additional rules)
[3] Few-shot examples from examples.json (≤ MAX_PROMPT_CHARS total)
[4] deterministic_candidate JSON
[5] Email subject + body
```

Layer 3 is trimmed if total prompt would exceed `MAX_PROMPT_CHARS` (default 12,000).
Trimming drops examples from oldest first.

---

## 5. HITL Correction Loop

### 5.1 Entry condition

```
PROMPT_FOR_FAILURE=true
AND sys.stdin.isatty() AND sys.stdout.isatty()
AND email is in result.skipped (not result.errors)
```

If TTY check fails: log `"PROMPT_FOR_FAILURE=true but no TTY — skipping interactive mode"`.

### 5.2 Correction flow (per email)

```
┌─────────────────────────────────────────────────┐
│  Display trace                                  │
│  - Subject, body preview (500 chars)            │
│  - Deterministic parse result (or "none")       │
│  - LLM prompt sent (collapsed, expandable)      │
│  - Raw LLM JSON response                        │
│  - Failure reason (e.g. "is_event=false",       │
│    "missing fields: date, time")                │
└──────────────────────┬──────────────────────────┘
                       │
          "Is this a valid calendar event?"
          [y] [n] [s=skip] [S=skip all]
                       │
         ┌─────────────┴─────────────┐
        [n]                         [y]
         │                           │
  "Permanently ignore?"      Field walkthrough
  [y] → save to ignored.json  (event_type, status,
  [n] → skip this run          date, time, location)
                                     │
                              Re-parse with corrections
                              as few-shot context
                                     │
                              ┌──────┴──────┐
                           success        failure
                              │              │
                        "Run sync?"    "Retry / edit
                           [y/n]        fields?"
                              │
                        Full sync pipeline
                              │
                    "Save correction? [y/n]"
                              │
                    Persist to examples.json
                    + propose prompt change
```

### 5.3 Field walkthrough

For each field, display current value (from LLM response or deterministic parse) and prompt
for override. Empty input = accept current. Validation is applied after each entry.

```
event_type [Practice/Lesson] (LLM: "Practice"): 
status [Reserved/Canceled] (LLM: "Canceled"): 
date MM/DD/YYYY (LLM: missing): 05/15/2026
time HH:MM AM/PM (LLM: missing): 10:00 AM
location (LLM: missing): Lake Nona in North Bay
```

### 5.4 Prompt change proposal

After confirmed fix, a second LLM call is made:

```
System: You are a prompt engineer. Given the original prompt and the human correction,
        propose the minimal change to the "Rules" section that would have handled this case.
        Return JSON: {"proposed_addition": "...", "section": "rules"}
```

The proposed addition is displayed as a quoted block. Operator accepts or rejects.

---

## 6. TerminalUI Abstraction

All interactive I/O goes through `terminal_ui.py`:

```python
class TerminalUI:
    def is_interactive(self) -> bool: ...
    def show_trace(self, trace: ParseTrace) -> None: ...
    def ask_is_event(self) -> Literal["yes", "no", "skip", "skip_all"]: ...
    def ask_ignore_permanently(self) -> bool: ...
    def ask_field(self, name: str, current: str | None, choices: list[str] | None) -> str: ...
    def ask_run_sync(self) -> bool: ...
    def ask_save_correction(self) -> bool: ...
    def show_prompt_diff(self, proposal: str) -> None: ...
    def ask_apply_prompt_change(self) -> bool: ...
```

Tests use a `MockTerminalUI` that replays scripted responses.

---

## 7. RunResult Changes

`models.py` `RunResult` gets two new fields:

```python
@dataclass
class RunResult:
    ...
    corrections_saved: int = 0       # examples persisted this run
    prompts_updated: int = 0         # prompt overrides applied this run
```

---

## 8. Configuration Changes

### .env additions

```
# Learning agent
PROMPT_FOR_FAILURE=false
AGENT_PROFILES_FILE=profiles.yaml
AGENT_LEARNING_DIR=.calendar-agent/learned
MAX_PROMPT_CHARS=12000
```

### .env.local (new file, gitignored)

Same keys — operator overrides without touching `.env`.

---

## 9. Migration

The existing `search_inclubgolf()` method and InClubGolf-specific regexes remain in place.
`profiles.yaml` is seeded with the InClubGolf profile on first run (auto-generated if missing).
The parser falls back to the built-in InClubGolf behavior if no profile matches, so existing
users see zero change until they opt in via `AGENT_PROFILES_FILE`.

---

## 10. File Change Summary

| File | Change |
|------|--------|
| `src/calendar_agent/profiles.py` | NEW — load, parse, match profiles |
| `src/calendar_agent/learning/store.py` | NEW — atomic read/write of learned state |
| `src/calendar_agent/learning/corrector.py` | NEW — HITL correction loop |
| `src/calendar_agent/terminal_ui.py` | NEW — interactive I/O abstraction |
| `src/calendar_agent/parser.py` | MODIFY — accept profile, inject examples into prompt |
| `src/calendar_agent/workflow.py` | MODIFY — load profiles, pass to parser, invoke corrector |
| `src/calendar_agent/main.py` | MODIFY — load PROMPT_FOR_FAILURE, pass TerminalUI |
| `src/calendar_agent/models.py` | MODIFY — add corrections_saved, prompts_updated to RunResult |
| `profiles.yaml` | NEW — default profile config (InClubGolf) |
| `.env` | MODIFY — add new keys |
| `.env.local.example` | NEW — template for local overrides |
| `.gitignore` | MODIFY — add .calendar-agent/, .env.local |
| `tests/` | NEW — unit tests for all new modules |
