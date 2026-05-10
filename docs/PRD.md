# PRD: Configurable Learning Calendar Agent

**Status:** Draft  
**Phase:** 1  
**Date:** 2026-05-10

---

## 1. Problem

The current agent is hardcoded to a single email sender (InClubGolf) with a fixed parsing
strategy. When it fails to parse a valid email, the operator has no path to fix it short of
editing Python source code. There is no memory: every run starts from scratch, and recurring
failures repeat silently forever.

The specific trigger: `'Practice Canceled'` emails are being skipped because the agent's
heuristics assume every calendar email contains a date, time, and location. Some notification
emails (cancellations, reminders) may omit one or more fields, or the LLM prompt may simply
not handle an edge case. Today there is no recovery path.

---

## 2. Goals

| # | Goal |
|---|------|
| G1 | Make the agent configurable for any email source without code changes |
| G2 | Give a human operator the ability to correct a failed parse interactively |
| G3 | Persist those corrections so the agent improves across runs |
| G4 | Re-process the corrected email immediately and sync it to the calendar |
| G5 | Never block an unattended (non-interactive) run |

---

## 3. Non-Goals (Phase 1)

- Admin UI for managing learned examples
- Undoing or editing a saved correction (Phase 2)
- Learning from `errors` (exceptions / network failures) — only `skipped` emails
- Multi-user or shared learning stores
- Confidence tracking or automatic prompt regression testing

---

## 4. Users

**Primary user:** The operator running the CLI (`uv run calendar-agent`). Technical, but not
necessarily a prompt engineer. Knows what a valid golf booking email looks like.

**System actor:** The agent itself, which proposes re-parses and confirms fixes.

---

## 5. User Stories

### US-1 — Configurable email sources
> As an operator, I want to define which emails the agent processes using a config file
> (from, to, subject pattern, body pattern) so I can extend the agent to new senders without
> touching Python code.

### US-2 — Interactive correction
> As an operator, when an email is skipped, I want the agent to pause, show me the full parse
> trace (email content, deterministic result, LLM prompt, LLM response, failure reason), and
> ask me whether it's a valid calendar event.

### US-3 — Human-guided re-parse
> As an operator, if I confirm the email is valid, I want to walk through the fields
> interactively (event type, status, date, time, location) so the agent can use my input to
> construct a corrected parse.

### US-4 — Immediate re-processing
> As an operator, after a correction is confirmed, I want the agent to immediately re-attempt
> the full sync (calendar upsert, ICS write) in a loop until it succeeds or I say skip.

### US-5 — Persistent learning
> As an operator, I want the agent to save the confirmed correction as a few-shot example so
> that future runs handle similar emails automatically.

### US-6 — Prompt evolution
> As an operator, I want the agent to propose a specific change to the base prompt template
> (as a diff) when a correction reveals a systematic gap, and apply it if I approve.

### US-7 — Safe unattended mode
> As an operator, when the agent runs non-interactively (cron, pipe, no TTY), I want
> `PROMPT_FOR_FAILURE=true` to be silently ignored and all failures logged as usual.

### US-8 — Learning confirmation
> As an operator, after an email is successfully re-processed, I want the agent to ask me
> to confirm the fix is correct before persisting it, so I don't accidentally save a bad
> correction.

### US-9 — Irrelevance detection
> As an operator, I want the agent to also learn from emails I mark as "not a calendar
> event" so it stops surfacing them as failures in future runs.

---

## 6. Functional Requirements

### FR-1: Profile configuration
- The agent MUST load one or more source profiles from a YAML file (`profiles.yaml` by
  default, path configurable via `AGENT_PROFILES_FILE` in `.env`).
- Each profile MUST define: `name`, `from_pattern`, `subject_pattern`, and a `prompt_template`.
- `to_pattern` and `body_pattern` are optional.
- All patterns are Python `re` expressions.
- The existing InClubGolf behavior MUST be expressible as a profile with no behavior change.

### FR-2: PROMPT_FOR_FAILURE flag
- Added to `.env` (default `false`) and `.env.local`.
- When `true` AND the process has an interactive TTY, the agent enters the correction loop
  for each skipped email.
- When `true` AND no TTY (non-interactive), the flag is ignored; a warning is logged.

### FR-3: Correction loop
- Display: email subject, body preview (500 chars), deterministic parse result, LLM prompt
  sent, raw LLM response, specific failure reason.
- Prompt: `Is this a valid calendar event? [y/n/skip all]`
- If `n`: prompt `Mark as permanently ignored? [y/n]` — if yes, save to ignore list.
- If `y`: walk through fields interactively.
- After each re-parse attempt: loop until success or user chooses skip.

### FR-4: Field correction walkthrough
For each required field (`event_type`, `status`, `date`, `time`, `location`):
- Show what the LLM returned (or "missing").
- Prompt the user to confirm or override.
- Validated against the same rules as the production parser.

### FR-5: Prompt update proposal
- After a successful human-guided correction, the agent sends a second LLM call asking it
  to propose a minimal addition to the base prompt that would have handled this case.
- Display the proposal as a diff.
- Prompt: `Apply this prompt change? [y/n]`
- Changes are appended as versioned sections in the profile's `prompt_overrides.yaml`.

### FR-6: Learning storage
- Confirmed corrections are saved as few-shot examples in
  `.calendar-agent/learned/{profile_name}/examples.json`.
- Ignored email patterns are saved to
  `.calendar-agent/learned/{profile_name}/ignored.json`.
- Prompt overrides are saved to
  `.calendar-agent/learned/{profile_name}/prompt_overrides.yaml`.
- All writes are atomic (write-then-rename).

### FR-7: Example injection
- On each run, the agent loads learned examples for the active profile and injects them
  into the LLM prompt as few-shot examples before the target email.
- If total prompt length exceeds `MAX_PROMPT_CHARS` (configurable, default 12000), older
  examples are dropped from the prompt (but not deleted from storage).

### FR-8: Re-processing after correction
- After a correction is applied and confirmed, the agent immediately runs the full sync
  pipeline for that email (calendar upsert, ICS, trash).
- The correction loop resumes with the next skipped email.

### FR-9: Correction confirmation
- After a successful re-process, prompt: `Save this correction for future runs? [y/n]`
- Only on `y` does the agent persist the example and/or prompt change.

---

## 7. Non-Functional Requirements

| NFR | Requirement |
|-----|-------------|
| NFR-1 | Learning storage writes are atomic; no partial state on crash |
| NFR-2 | `PROMPT_FOR_FAILURE=false` (default) — zero behavior change from current |
| NFR-3 | Profile loading failure (bad YAML, missing file) must not silently swallow emails — surface as startup error |
| NFR-4 | All interactive I/O goes through a single `TerminalUI` abstraction (testable with mock) |
| NFR-5 | No new mandatory dependencies for Phase 1 (stdlib + existing deps only) |

---

## 8. Success Metrics

| Metric | Target |
|--------|--------|
| Zero operator skips | The operator can process a run with no manual skips |
| Fix confirmation rate | After a correction, operator confirms the fix (not just skips) |
| Recurrence rate | A fixed failure does not appear in the skipped list on the next run |

---

## 9. Out of Scope (Future Phases)

- **Phase 2:** Admin CLI (`calendar-agent --manage-learning`) to list, delete, or edit saved
  examples and prompt overrides.
- **Phase 2:** Undo last correction.
- **Phase 2:** Confidence-weighted example pruning.
- **Phase 3:** Fully autonomous learning (no human confirmation required, confidence ≥ 0.95).
