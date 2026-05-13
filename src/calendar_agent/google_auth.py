from __future__ import annotations

import os
from pathlib import Path


CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
GOOGLE_APP_SCOPES = [CALENDAR_SCOPE, GMAIL_MODIFY_SCOPE]


def get_google_credentials(
    scopes: list[str] | None = None,
    credentials_file: str | None = None,
    token_file: str | None = None,
):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    scopes = scopes or GOOGLE_APP_SCOPES
    token_file = Path(token_file or os.getenv("GOOGLE_TOKEN_FILE", "token.json"))
    credentials_file = Path(credentials_file or os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json"))
    creds = None

    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), scopes)

    missing_scope = creds is not None and not creds.has_scopes(scopes)
    if missing_scope:
        creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not credentials_file.exists():
                raise RuntimeError(f"Google credentials file not found: {credentials_file}")
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), scopes)
            creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json(), encoding="utf-8")

    return creds
