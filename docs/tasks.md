# Task List: Phase 1 — Configurable Learning Calendar Agent

**PRD:** [PRD.md](./PRD.md)  
**Design:** [design.md](./design.md)  
**Date:** 2026-05-10

---

## Phase breakdown

| Phase | Name | Description |
|-------|------|-------------|
| 1a | Foundation | Profile system + storage layer (no behavior change) |
| 1b | Learning loop | HITL correction, re-processing, persistence |
| 1c | Prompt evolution | LLM-proposed prompt changes |
| 1d | Hardening | Tests, migration, .env cleanup |

---

## Phase 1a — Foundation

### T-01 — Add .env.local support
- Add `.env.local` to `.gitignore`
- Create `.env.local.example` with all new keys commented out
- Update `main.py` to `load_dotenv(".env.local", override=True)` after `load_dotenv()`
- **Acceptance:** Running with `.env.local` overrides `.env` values

### T-02 — Define SourceProfile model
- Create `src/calendar_agent/profiles.py`
- `SourceProfile` frozen dataclass: `name`, `from_pattern`, `subject_pattern`,
  `to_pattern`, `body_pattern`, `prompt_template`
- `load_profiles(path: Path) -> list[SourceProfile]` — parse YAML, compile regex, validate
- `match_profile(email: EmailRecord, profiles: list[SourceProfile]) -> SourceProfile | None`
- Raise `ProfileConfigError` (startup error) on bad YAML or invalid regex
- **Acceptance:** Unit tests cover: valid profile, missing optional fields, bad regex, no match

### T-03 — Create default profiles.yaml
- Seed with InClubGolf profile using the existing prompt from `_build_llm_prompt()`
- Include comments explaining each field
- **Acceptance:** Existing test suite passes unchanged with profiles.yaml loaded

### T-04 — Define learning storage layout
- Create `src/calendar_agent/learning/__init__.py`
- Create `src/calendar_agent/learning/store.py`
- `LearningStore(profile_name: str, base_dir: Path)`
- Methods: `load_examples()`, `save_example(ex)`, `load_ignored()`, `save_ignored(pat)`,
  `load_prompt_overrides()`, `save_prompt_override(ov)`
- All writes atomic via `write-then-rename`
- Auto-create directory on first write
- **Acceptance:** Unit tests cover: round-trip save/load, atomic write (simulate crash), missing dir

### T-05 — Update .env with new keys
- Add `PROMPT_FOR_FAILURE=false`, `AGENT_PROFILES_FILE=profiles.yaml`,
  `AGENT_LEARNING_DIR=.calendar-agent/learned`, `MAX_PROMPT_CHARS=12000`
- Update README with description of each key
- **Acceptance:** Agent starts without errors with new keys present or absent

### T-06 — Update parser to accept profile + inject examples
- Add `profile: SourceProfile | None` and `examples: list[dict]` params to
  `parse_inclubgolf_email()` (rename to `parse_email()`)
- Build prompt from `profile.prompt_template` if profile present, else fall back to
  existing hardcoded template
- Inject examples as few-shot block before the target email
- Trim oldest examples if prompt exceeds `MAX_PROMPT_CHARS`
- **Acceptance:** Parser behaves identically when profile=None and examples=[]

### T-07 — Update workflow to load profiles and pass to parser
- Load profiles at start of `run_sync()`
- Match each email to a profile
- Pass profile + loaded examples to `parse_email()`
- Log `"no matching profile"` skip reason
- Add `profile_name` to `RunResult.skipped` entries
- **Acceptance:** Existing e2e smoke test passes; "no profile" emails appear in skipped

---

## Phase 1b — Learning Loop

### T-08 — Create TerminalUI abstraction
- `src/calendar_agent/terminal_ui.py`
- `TerminalUI` class with all interactive methods (see design.md §6)
- `is_interactive()` — `sys.stdin.isatty() and sys.stdout.isatty()`
- `MockTerminalUI` for tests — accepts scripted response list
- **Acceptance:** Unit tests cover: all prompts, mock replay, TTY detection

### T-09 — Create ParseTrace model
- `src/calendar_agent/models.py` — add `ParseTrace` dataclass
- Fields: `email`, `deterministic_result`, `llm_prompt`, `llm_response`, `failure_reason`
- Parser returns `ParseTrace` alongside `GolfEvent | None`
- **Acceptance:** Trace carries all data needed for display; existing callers not broken

### T-10 — Implement field walkthrough
- `src/calendar_agent/learning/corrector.py`
- `run_field_walkthrough(trace, ui: TerminalUI) -> dict | None`
- Walk through `event_type`, `status`, `date`, `time`, `location`
- Show LLM value (or "missing"), accept override, validate
- Return corrected field dict or None (user aborted)
- **Acceptance:** Unit tests: full walkthrough, partial abort, validation rejection

### T-11 — Implement correction loop
- `run_correction_loop(email, trace, profile, store, ui, sync_fn) -> CorrectionResult`
- Displays trace, asks is_event, branches to ignore or walkthrough
- On walkthrough success: calls `sync_fn` (injectable for tests)
- Loops re-parse until success or user skips
- Returns: `corrected | ignored | skipped`
- **Acceptance:** Unit tests using MockTerminalUI for each branch

### T-12 — Wire correction loop into workflow
- After collecting `result.skipped`, if `PROMPT_FOR_FAILURE=true` and `ui.is_interactive()`:
  iterate skipped emails, invoke correction loop
- If not interactive: log warning, continue
- Update `RunResult.corrections_saved` and `prompts_updated`
- **Acceptance:** End-to-end test with MockTerminalUI; non-interactive path confirmed via test

### T-13 — Implement ignore list check
- At start of `run_sync()`, load ignored patterns for matched profile
- Skip emails matching any ignored subject_pattern before attempting parse
- Count in a new `result.ignored` field
- **Acceptance:** Unit test: email matching ignored pattern is skipped before LLM call

---

## Phase 1c — Prompt Evolution

### T-14 — Implement prompt change proposal
- After confirmed field walkthrough, send second LLM call requesting prompt addition
- Display proposal as quoted block in terminal
- Ask `Apply this prompt change? [y/n]`
- On yes: append to `prompt_overrides.yaml` via store
- **Acceptance:** Unit tests: proposal displayed, accepted, rejected, persisted correctly

### T-15 — Inject prompt overrides into prompt construction
- After base template and before few-shot examples, append active overrides from
  `prompt_overrides.yaml` as an additional "Rules" section
- **Acceptance:** Parser test confirms overrides appear in constructed prompt

---

## Phase 1d — Hardening

### T-16 — Migration: auto-generate profiles.yaml if missing
- On startup, if `AGENT_PROFILES_FILE` path does not exist, write the default InClubGolf
  profile to that path and log `"Created default profiles.yaml"`
- **Acceptance:** Fresh clone runs without errors; file appears after first run

### T-17 — Update gitignore
- Add `.calendar-agent/`, `.env.local`
- **Acceptance:** `git status` does not show learned state or local overrides

### T-18 — Update RunResult output in main.py
- Print `corrections_saved` and `prompts_updated` in summary if > 0
- **Acceptance:** Smoke test confirms output lines appear

### T-19 — Integration test: full correction flow
- Fixture with a mock email that fails parse + MockTerminalUI that confirms fix
- Asserts: example persisted, sync called, RunResult counts correct
- **Acceptance:** Test passes; no real LLM or IMAP calls

### T-20 — Update README
- Document new `.env` keys
- Document `profiles.yaml` format with example
- Document the correction loop (what to expect interactively)

---

## Dependencies

```
T-01 ──► T-05
T-02 ──► T-03 ──► T-06 ──► T-07
T-04 ──► T-11
T-08 ──► T-10 ──► T-11 ──► T-12
T-09 ──► T-10
T-07 + T-12 ──► T-19
T-14 ──► T-15
T-16, T-17, T-18, T-20 can run in parallel after T-07
```

## Recommended implementation order

```
T-01 → T-02 → T-03 → T-04 → T-05 → T-06 → T-07
→ T-08 → T-09 → T-10 → T-11 → T-12 → T-13
→ T-14 → T-15
→ T-16 → T-17 → T-18 → T-19 → T-20
```

---

## Definition of Done (Phase 1)

- [ ] All tasks complete
- [ ] Test coverage ≥ 80% on new modules
- [ ] Existing test suite still passes
- [ ] Agent runs end-to-end with `DRY_RUN=true PROMPT_FOR_FAILURE=false` (no behavior change)
- [ ] Agent runs interactively with `PROMPT_FOR_FAILURE=true` on a skipped email
- [ ] Learned example persists and is used on the next run
- [ ] No new mandatory dependencies added
- [ ] `.env.local` and `.calendar-agent/` are gitignored
