# Calendar Agent

Syncs InClubGolf emails from Yahoo Mail or Gmail into Google Calendar and Outlook.
If Outlook credentials are not configured or direct sync fails, the agent writes
`.ics` files to `~/Downloads/inclubgolf`.

## Behavior

- Searches Yahoo by IMAP or Gmail by Google OAuth for `noreply@inclubgolf.com`.
- Sends every found InClubGolf email to the configured LLM. A deterministic parser still proposes a candidate parse when it can, but the LLM is always called to confirm, correct, or reject the email as not being a calendar event.
- Treats every event as 20 minutes.
- Uses Eastern time.
- Creates or updates Google Calendar events using a stable event identity based on event type, location, and start time.
- For cancellations, keeps the calendar event visible by renaming the title to `[CANCELED] ...`; it does not delete the event.
- Generates ICS files named like `2026-05-14_1740.ics`; canceled events include `STATUS:CANCELLED`.
- Moves the source email to Trash only after Google sync succeeds and either Outlook sync or ICS generation succeeds.

## Setup

```bash
cp .env.example .env
uv venv
uv pip install -e .
```

## Mailbox Setup

The default mailbox mode is `auto`: try Yahoo IMAP first, then Gmail using
Sign in with Google if Yahoo credentials are missing or Yahoo IMAP fails.

```env
MAIL_PROVIDER=auto

YAHOO_IMAP_USER=your-email@yahoo.com
YAHOO_IMAP_PASSWORD=your-yahoo-app-password
```

Gmail does not use an app password in this project. It uses the same
`credentials.json` OAuth client as Google Calendar, with Gmail API access added.
The agent moves processed mail to Trash in whichever mailbox it actually read
from.

If Yahoo login fails after creating an app password, check:

- `YAHOO_IMAP_USER` is the full Yahoo email address for the account that created the app password.
- `YAHOO_IMAP_PASSWORD` is the generated app password, not the normal Yahoo password.
- The app password was copied without spaces or hyphens.
- `YAHOO_IMAP_FOLDER=INBOX`.
- `MAIL_PROVIDER=auto` if you want Gmail OAuth fallback when Yahoo fails.

Put your Google OAuth desktop `credentials.json` in this directory. The first
run will open a browser for Google consent and write `token.json`.

## Google OAuth

Create `credentials.json` as a Google desktop OAuth client:

1. Open <https://console.cloud.google.com/>.
2. Create or select a Google Cloud project.
3. Go to APIs & Services, then Library, then enable `Google Calendar API`.
4. Also enable `Gmail API`.
5. Go to Google Auth Platform, then Branding, and configure the OAuth consent screen.
6. Use `External` audience for a personal Gmail account, and add yourself as a test user if prompted.
7. Go to Google Auth Platform, then Clients, then Create Client.
8. Choose application type `Desktop app`.
9. Name it `CalendarAgent Local`.
10. Download the JSON file and save it as:

```bash
/Users/mfanti/AgenticAI/CalendarAgent/credentials.json
```

Keep these `.env` values:

```env
GOOGLE_CALENDAR_ID=primary
GOOGLE_CREDENTIALS_FILE=credentials.json
GOOGLE_TOKEN_FILE=token.json
```

The first run that uses Google Calendar opens a browser. Sign in as
`marco.fanti@gmail.com` and approve Calendar and Gmail access. The app then
writes `token.json`. If `token.json` was created earlier without Gmail access or
with a read-only Calendar scope, delete it and rerun because this agent needs
Calendar write access and Gmail modify access.

## LLM Parser

Ollama is the default parser provider:

```env
LLM_PROVIDER=ollama
LLM_MODEL=llama3.2
OLLAMA_BASE_URL=http://localhost:11434
```

Make sure Ollama is running and the selected model is installed:

```bash
ollama pull llama3.2
ollama serve
```

To use Gemini instead:

```env
LLM_PROVIDER=gemini
LLM_MODEL=gemini-2.5-flash
LLM_API_KEY=your-api-key
```

## Run

Preview without changing email/calendar state:

```bash
uv run calendar-agent --dry-run
```

Dry run still connects to the configured mailbox and LLM provider, but it does
not require `credentials.json` and does not touch Google Calendar, Outlook, ICS
files, or email deletion.

For provider and per-email diagnostics:

```bash
uv run calendar-agent --dry-run --debug
```

Debug mode shows which mailbox provider was tried, IMAP login/search/fetch
statuses, fetched email UIDs and subjects, LLM parse failures, parsed event
details, and what calendar/ICS/trash actions dry run would take.

Apply changes:

```bash
uv run calendar-agent
```
