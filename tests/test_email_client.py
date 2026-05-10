import base64
from email.message import EmailMessage

from calendar_agent.email_client import FallbackEmailClient, GmailApiEmailClient, ImapEmailClient, build_email_client_from_env
from calendar_agent.models import EmailRecord


def test_fallback_uses_gmail_when_yahoo_search_fails():
    yahoo = _Client("yahoo", search_error=RuntimeError("login failed"))
    gmail = _Client(
        "gmail",
        emails=[EmailRecord(uid="22", message_id="m", subject="Practice Confirmed", body="body")],
    )
    client = FallbackEmailClient([yahoo, gmail])

    emails = client.search_inclubgolf()
    moved = client.move_to_trash("22")

    assert emails[0].uid == "22"
    assert moved is True
    assert yahoo.trashed == []
    assert gmail.trashed == ["22"]


def test_build_email_client_yahoo_explicit_returns_imap_only(monkeypatch):
    monkeypatch.setenv("MAIL_PROVIDER", "yahoo")
    monkeypatch.setenv("YAHOO_IMAP_USER", "user@yahoo.com")
    monkeypatch.setenv("YAHOO_IMAP_PASSWORD", "secret")

    client = build_email_client_from_env()

    assert isinstance(client, ImapEmailClient)
    assert client.config.provider == "yahoo"


def test_build_email_client_skips_missing_yahoo_and_uses_gmail(monkeypatch):
    monkeypatch.setenv("MAIL_PROVIDER", "auto")
    monkeypatch.delenv("YAHOO_IMAP_USER", raising=False)
    monkeypatch.delenv("YAHOO_IMAP_PASSWORD", raising=False)

    client = build_email_client_from_env()

    assert isinstance(client, FallbackEmailClient)
    assert [item.config.provider for item in client.clients] == ["gmail"]


def test_gmail_api_search_read_and_trash():
    service = _GmailService(_raw_message())
    client = GmailApiEmailClient(service=service)

    emails = client.search_inclubgolf()
    moved = client.move_to_trash("gmail-msg-1")

    assert len(emails) == 1
    assert emails[0].uid == "gmail-msg-1"
    assert emails[0].message_id == "source-message-id"
    assert emails[0].subject == "Practice Confirmed"
    assert "successfully reserved" in emails[0].body
    assert moved is True
    assert service.trashed == ["gmail-msg-1"]


class _Client:
    def __init__(self, provider, emails=None, search_error=None):
        self.config = _Config(provider)
        self.emails = emails or []
        self.search_error = search_error
        self.trashed = []

    def search_emails(self, from_addresses):
        if self.search_error:
            raise self.search_error
        return self.emails

    def search_inclubgolf(self):
        return self.search_emails(["noreply@inclubgolf.com"])

    def move_to_trash(self, uid):
        self.trashed.append(uid)
        return True


class _Config:
    def __init__(self, provider):
        self.provider = provider


class _GmailService:
    def __init__(self, raw):
        self.raw = raw
        self.trashed = []

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, **kwargs):
        self.list_kwargs = kwargs
        self.action = "list"
        return self

    def get(self, **kwargs):
        self.get_kwargs = kwargs
        self.action = "get"
        return self

    def trash(self, **kwargs):
        self.trashed.append(kwargs["id"])
        self.action = "trash"
        return self

    def execute(self):
        if self.action == "list":
            return {"messages": [{"id": "gmail-msg-1"}]}
        if self.action == "get":
            return {"raw": self.raw}
        return {}


def _raw_message():
    msg = EmailMessage()
    msg["From"] = "noreply@inclubgolf.com"
    msg["Subject"] = "Practice Confirmed"
    msg["Message-ID"] = "<source-message-id>"
    msg.set_content("You have successfully reserved a practice session.")
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii").rstrip("=")
