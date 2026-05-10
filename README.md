# Calendar Agent

Syncs calendar events from email sources (InClubGolf, CourtReserve, and any
custom profile) into Google Calendar, Outlook, and ICS files. Email sources
are configured in `profiles.yaml`; each profile defines which sender to watch,
which mailbox folder to search, and what prompt to give the LLM.

## How it works

1. Loads `profiles.yaml` and groups profiles by mailbox folder.
2. For each folder, searches the mailbox for emails matching any configured
   sender address in that folder (one IMAP `SELECT` per unique folder).
3. Routes each email to the matching profile, then calls the LLM (with a
   deterministic pre-parse as a hint) to extract event details as JSON.
4. Creates or updates Google Calendar events using a stable UID derived from
   event type, location, and start time. Cancellations rename the event to
   `[CANCELED] …` rather than deleting it.
5. Writes ICS files and syncs to Outlook if configured.
6. Moves processed emails to Trash only after calendar sync succeeds.
7. When `PROMPT_FOR_FAILURE=true` and running in a TTY, pauses on parse
   failures and offers an interactive correction loop that saves learned
   examples and prompt rules for future runs.

## Setup

```bash
cp .env.example .env
uv venv
uv sync
```

## Mailbox

| `MAIL_PROVIDER` | Behavior |
|---|---|
| `yahoo` | IMAP only — no fallback |
| `gmail` | Gmail API only |
| `auto` | Try Yahoo first, fall back to Gmail |

```env
MAIL_PROVIDER=yahoo

YAHOO_IMAP_USER=your-email@yahoo.com
YAHOO_IMAP_PASSWORD=your-yahoo-app-password
```

Gmail uses the same `credentials.json` OAuth desktop client as Google Calendar
(requires Gmail API enabled in the same project). The agent moves processed
mail to Trash in whichever folder it was fetched from.

**Yahoo troubleshooting:** ensure `YAHOO_IMAP_USER` is the full Yahoo address,
`YAHOO_IMAP_PASSWORD` is the generated app password (no spaces or hyphens), and
Yahoo IMAP is enabled under Account Security.

## Google OAuth

Create `credentials.json` as a Google desktop OAuth client:

1. <https://console.cloud.google.com/> → create/select a project.
2. Enable **Google Calendar API** and **Gmail API**.
3. OAuth consent screen → External, add yourself as test user.
4. Clients → Create → Desktop app → download JSON → save as `credentials.json`.

```env
GOOGLE_CALENDAR_ID=primary
GOOGLE_CREDENTIALS_FILE=credentials.json
GOOGLE_TOKEN_FILE=token.json
```

The first run opens a browser for consent and writes `token.json`. If
`token.json` was created without Gmail access, delete it and rerun.

## LLM

```env
# Ollama (default)
LLM_PROVIDER=ollama
LLM_MODEL=llama3.2
OLLAMA_BASE_URL=http://localhost:11434

# Gemini
LLM_PROVIDER=gemini
LLM_MODEL=gemini-2.5-flash
LLM_API_KEY=your-api-key
```

## Run

```bash
# Preview without writing anything
uv run calendar-agent --dry-run --debug

# Apply changes
uv run calendar-agent

# Interactive correction loop for failed parses
PROMPT_FOR_FAILURE=true uv run calendar-agent
```

`--debug` shows full provider, IMAP, and per-email diagnostics including the
resolved ICS output path and each LLM response.

## Profile Configuration (`profiles.yaml`)

Profiles drive everything: which emails to fetch, which mailbox folder to look
in, how to prompt the LLM, and where to route the resulting ICS file.

```yaml
profiles:
  - name: my-source

    # Required — Python regex matched against the From address
    from_pattern: "alerts@example\\.com"

    # Optional filters (default: match everything)
    subject_pattern: "Booking.*"
    to_pattern: ".*"
    body_pattern: ".*"

    # IMAP folder to search (default: INBOX).
    # Folder names with spaces are quoted automatically.
    mailbox: "00 - Tennis"

    # Truncate email body to this many chars before sending to LLM.
    # Useful when bookings appear in the first few lines and the rest
    # is legal boilerplate.
    body_max_chars: 500

    # Event duration fallback when the LLM does not provide an end time.
    duration_minutes: 30

    # X-WR-CALNAME written into ICS files — Apple Calendar and Outlook
    # use this to route the import to the named calendar.
    calendar_name: "Tennis"

    prompt_template: |
      Extract a calendar event …
```

All `*_pattern` values are Python `re` expressions applied with `re.search`.
The first matching profile wins. Override the config path with
`AGENT_PROFILES_FILE=path/to/profiles.yaml`.

### Built-in profiles

**`inclubgolf`** — matches `noreply@inclubgolf.com`, 20-minute sessions,
returns `event_type` of `Practice` or `Lesson`.

**`courtreserve`** — matches the CourtReserve iCloud relay address, 90-minute
fallback duration, returns the full program title as `event_type` plus explicit
`start_time` and `end_time` fields so duration is derived from the email rather
than the fallback.

### Profile-aware parsing

- **Event title**: `{profile_name} {event_type} - {location}`. Location is
  omitted from the title when the email provides no court/facility name.
- **ICS PRODID**: uses the profile name instead of the hardcoded string.
- **Free-form `event_type`**: profiles accept any non-empty string (not just
  `Practice`/`Lesson`), so CourtReserve program names and party registrations
  are preserved verbatim.

### Deterministic fallback

When the LLM returns `is_event=false` but the deterministic parser already
extracted a complete candidate (event type, status, location, and start time
all found via regex), the agent overrides the LLM and uses the deterministic
result. This prevents false correction-loop prompts for clear InClubGolf emails
that the LLM occasionally rejects.

## Interactive Learning

When an email fails to parse, enable the correction loop:

```env
PROMPT_FOR_FAILURE=true
```

Silently ignored in non-TTY contexts (cron, pipes).

**Correction flow:**

1. Agent prints the full trace: subject, body preview, deterministic candidate,
   full LLM prompt, raw LLM response, failure reason.
2. You choose: `y` (valid event) / `n` (not an event) / `s` (skip) / `S` (skip all).
3. If `y`: enter corrected fields. Defaults are pre-filled from the LLM response
   or deterministic candidate; press Enter to accept.
   - Profile-based events accept any `event_type` string and allow empty location.
   - `end_time` is available for profiles that provide a time range.
4. Agent syncs the corrected event immediately.
5. On success, optionally save the correction as a few-shot example.
6. If the LLM returned `is_event=false`, an override rule is auto-saved so
   future runs recognise this email type as a valid event.

**Learned data** (in `.calendar-agent/learned/{profile-name}/`):

| File | Contents |
|---|---|
| `examples.json` | Confirmed corrections — injected as few-shot examples |
| `ignored.json` | Subject patterns permanently marked as non-events |
| `prompt_overrides.json` | Operator rules injected into the LLM prompt |

```env
AGENT_LEARNING_DIR=.calendar-agent/learned
MAX_PROMPT_CHARS=12000   # trim oldest examples when prompt grows too large
```

Learned data is gitignored by default. Commit it selectively to share across
machines.

## Environment variables

```env
# Mail
MAIL_PROVIDER=auto            # yahoo | gmail | auto
YAHOO_IMAP_USER=
YAHOO_IMAP_PASSWORD=
YAHOO_IMAP_FOLDER=INBOX
YAHOO_IMAP_TRASH_FOLDER=Trash

# Google
GOOGLE_CALENDAR_ID=primary
GOOGLE_CREDENTIALS_FILE=credentials.json
GOOGLE_TOKEN_FILE=token.json

# LLM
LLM_PROVIDER=ollama           # ollama | gemini
LLM_MODEL=llama3.2
OLLAMA_BASE_URL=http://localhost:11434
LLM_API_KEY=                  # Gemini only

# Agent
ICS_OUTPUT_DIR=~/Downloads/inclubgolf
DRY_RUN=false
AGENT_PROFILES_FILE=profiles.yaml
PROMPT_FOR_FAILURE=false
AGENT_LEARNING_DIR=.calendar-agent/learned
MAX_PROMPT_CHARS=12000
```
