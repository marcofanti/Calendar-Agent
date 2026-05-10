"""Test Yahoo IMAP login using app password credentials from .env."""

import imaplib
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

YAHOO_IMAP_HOST = "imap.mail.yahoo.com"
YAHOO_IMAP_PORT = 993


def test_yahoo_imap_login():
    user = os.environ.get("YAHOO_IMAP_USER", "")
    password = os.environ.get("YAHOO_IMAP_PASSWORD", "")
    folder = os.environ.get("YAHOO_IMAP_FOLDER", "INBOX")

    assert user and user != "your-email@yahoo.com", "YAHOO_IMAP_USER not set in .env"
    assert password and password != "your-yahoo-app-password", "YAHOO_IMAP_PASSWORD not set in .env"

    with imaplib.IMAP4_SSL(YAHOO_IMAP_HOST, YAHOO_IMAP_PORT) as imap:
        status, response = imap.login(user, password)
        assert status == "OK", f"Login failed: {response}"

        status, data = imap.select(folder, readonly=True)
        assert status == "OK", f"Could not select folder {folder!r}: {data}"

        message_count = int(data[0])
        print(f"\nLogged in as {user}")
        print(f"Folder {folder!r} contains {message_count} messages")


if __name__ == "__main__":
    test_yahoo_imap_login()
    print("Yahoo IMAP login: OK")
