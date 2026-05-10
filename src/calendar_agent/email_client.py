from __future__ import annotations

import base64
import email
import imaplib
import os
import re
from dataclasses import dataclass
from email import policy
from email.header import decode_header, make_header
from html import unescape

from calendar_agent.debug import debug_log, exception_summary
from calendar_agent.google_auth import GOOGLE_APP_SCOPES, get_google_credentials
from calendar_agent.models import EmailRecord


@dataclass(frozen=True, slots=True)
class ImapConfig:
    provider: str
    host: str
    user: str
    password: str
    folder: str = "INBOX"
    trash_folder: str | None = None

    @classmethod
    def from_env(cls) -> "ImapConfig":
        provider = os.getenv("MAIL_PROVIDER", "auto").strip().lower()
        if provider == "auto":
            provider = "yahoo"
        return cls.for_provider(provider, allow_legacy=True)

    @classmethod
    def for_provider(cls, provider: str, allow_legacy: bool = False) -> "ImapConfig":
        provider = provider.strip().lower()
        if provider not in {"yahoo", "gmail"}:
            raise RuntimeError(f"Unsupported MAIL_PROVIDER: {provider}")

        prefix = provider.upper()
        host = os.getenv(f"{prefix}_IMAP_HOST") or os.getenv("IMAP_HOST")
        if not host:
            host = "imap.gmail.com" if provider == "gmail" else "imap.mail.yahoo.com"

        user = os.getenv(f"{prefix}_IMAP_USER", "")
        password = os.getenv(f"{prefix}_IMAP_PASSWORD", "")
        if allow_legacy:
            user = user or os.getenv("IMAP_USER", "")
            password = password or os.getenv("IMAP_PASSWORD", "")
        if not user or not password:
            raise RuntimeError(f"{prefix}_IMAP_USER and {prefix}_IMAP_PASSWORD must be set.")

        folder = os.getenv(f"{prefix}_IMAP_FOLDER") or os.getenv("IMAP_FOLDER", "INBOX")
        trash_folder = os.getenv(f"{prefix}_IMAP_TRASH_FOLDER") or os.getenv("IMAP_TRASH_FOLDER")
        if not trash_folder:
            trash_folder = "[Gmail]/Trash" if provider == "gmail" else "Trash"

        return cls(
            provider=provider,
            host=host,
            user=user,
            password=password,
            folder=folder,
            trash_folder=trash_folder,
        )


class ImapEmailClient:
    def __init__(self, config: ImapConfig, debug: bool = False):
        self.config = config
        self.debug = debug

    def search_inclubgolf(self) -> list[EmailRecord]:
        debug_log(
            f"{self.config.provider}: selecting folder {self.config.folder!r} on {self.config.host}",
            self.debug,
        )
        with self._connect() as mail:
            status, select_data = mail.select(self.config.folder)
            debug_log(f"{self.config.provider}: select status={status} data={select_data}", self.debug)
            if status != "OK":
                raise RuntimeError(f"IMAP select failed for {self.config.folder}: {status} {select_data}")

            status, data = mail.uid("search", None, '(FROM "noreply@inclubgolf.com")')
            debug_log(f"{self.config.provider}: search status={status} raw={data}", self.debug)
            if status != "OK":
                raise RuntimeError(f"IMAP search failed: {status}")

            emails: list[EmailRecord] = []
            uids = data[0].split() if data and data[0] else []
            debug_log(f"{self.config.provider}: found {len(uids)} candidate message(s)", self.debug)
            for uid_bytes in uids:
                uid = uid_bytes.decode("ascii")
                status, fetched = mail.uid("fetch", uid, "(RFC822)")
                debug_log(f"{self.config.provider}: fetch uid={uid} status={status}", self.debug)
                if status != "OK" or not fetched or not isinstance(fetched[0], tuple):
                    debug_log(f"{self.config.provider}: skipping uid={uid}; malformed fetch response", self.debug)
                    continue

                msg = email.message_from_bytes(fetched[0][1], policy=policy.default)
                subject = _decode_subject(msg.get("Subject", ""))
                debug_log(f"{self.config.provider}: fetched uid={uid} subject={subject!r}", self.debug)
                emails.append(
                    EmailRecord(
                        uid=uid,
                        message_id=_message_id(msg, uid),
                        subject=subject,
                        body=_body_from_message(msg),
                        folder=self.config.folder,
                    )
                )
            return emails

    def move_to_trash(self, uid: str) -> bool:
        with self._connect() as mail:
            mail.select(self.config.folder)
            if self.config.trash_folder:
                status, _ = mail.uid("COPY", uid, self.config.trash_folder)
                debug_log(f"{self.config.provider}: copy uid={uid} to trash status={status}", self.debug)
                if status != "OK":
                    return False
            status, _ = mail.uid("STORE", uid, "+FLAGS", r"(\Deleted)")
            debug_log(f"{self.config.provider}: mark deleted uid={uid} status={status}", self.debug)
            if status != "OK":
                return False
            mail.expunge()
            return True

    def _connect(self):
        debug_log(
            f"{self.config.provider}: connecting to {self.config.host} as {self.config.user}",
            self.debug,
        )
        mail = imaplib.IMAP4_SSL(self.config.host)
        status, data = mail.login(self.config.user, self.config.password)
        debug_log(f"{self.config.provider}: login status={status} data={data}", self.debug)
        return _ImapSession(mail)


class FallbackEmailClient:
    def __init__(self, clients: list, debug: bool = False):
        if not clients:
            raise RuntimeError("No email providers are configured.")
        self.clients = clients
        self.active_client = None
        self.debug = debug

    def search_inclubgolf(self) -> list[EmailRecord]:
        errors: list[str] = []
        for client in self.clients:
            try:
                debug_log(f"trying email provider {client.config.provider}", self.debug)
                emails = client.search_inclubgolf()
                self.active_client = client
                if errors:
                    print("Email fallback used after: " + "; ".join(errors))
                print(f"Reading InClubGolf email from {client.config.provider}.")
                return emails
            except Exception as exc:
                summary = exception_summary(exc)
                debug_log(f"provider {client.config.provider} failed: {summary}", self.debug)
                errors.append(f"{client.config.provider}: {summary}")

        raise RuntimeError("All configured email providers failed: " + "; ".join(errors))

    def move_to_trash(self, uid: str) -> bool:
        if self.active_client is None:
            raise RuntimeError("No active email provider; search_inclubgolf must run before cleanup.")
        return self.active_client.move_to_trash(uid)


class GmailApiConfig:
    provider = "gmail"


class GmailApiEmailClient:
    def __init__(self, service=None, debug: bool = False):
        self.config = GmailApiConfig()
        self.service = service
        self.debug = debug

    def search_inclubgolf(self) -> list[EmailRecord]:
        service = self._service()
        debug_log("gmail: searching Gmail API for from:noreply@inclubgolf.com", self.debug)
        messages = []
        page_token = None
        while True:
            request = service.users().messages().list(
                userId="me",
                q="from:noreply@inclubgolf.com",
                includeSpamTrash=False,
                pageToken=page_token,
            )
            response = request.execute()
            messages.extend(response.get("messages", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break

        emails: list[EmailRecord] = []
        debug_log(f"gmail: found {len(messages)} candidate message(s)", self.debug)
        for item in messages:
            message_id = item["id"]
            raw_message = (
                service.users()
                .messages()
                .get(userId="me", id=message_id, format="raw")
                .execute()
            )
            raw = _decode_gmail_raw(raw_message["raw"])
            msg = email.message_from_bytes(raw, policy=policy.default)
            subject = _decode_subject(msg.get("Subject", ""))
            debug_log(f"gmail: fetched id={message_id} subject={subject!r}", self.debug)
            emails.append(
                EmailRecord(
                    uid=message_id,
                    message_id=_message_id(msg, message_id),
                    subject=subject,
                    body=_body_from_message(msg),
                    folder="gmail",
                )
            )
        return emails

    def move_to_trash(self, uid: str) -> bool:
        debug_log(f"gmail: moving id={uid} to trash", self.debug)
        self._service().users().messages().trash(userId="me", id=uid).execute()
        return True

    def _service(self):
        if self.service is None:
            from googleapiclient.discovery import build

            self.service = build("gmail", "v1", credentials=get_google_credentials(GOOGLE_APP_SCOPES))
        return self.service


def build_email_client_from_env(debug: bool = False) -> ImapEmailClient | GmailApiEmailClient | FallbackEmailClient:
    provider = os.getenv("MAIL_PROVIDER", "auto").strip().lower()
    if provider == "gmail":
        return GmailApiEmailClient(debug=debug)
    if provider == "yahoo":
        clients = _configured_clients(["yahoo", "gmail"], debug=debug)
        return FallbackEmailClient(clients, debug=debug)
    if provider == "auto":
        clients = _configured_clients(["yahoo", "gmail"], debug=debug)
        return FallbackEmailClient(clients, debug=debug)
    raise RuntimeError(f"Unsupported MAIL_PROVIDER: {provider}")


def _configured_clients(providers: list[str], debug: bool = False) -> list:
    clients: list = []
    selected_provider = os.getenv("MAIL_PROVIDER", "auto").strip().lower()
    for provider in providers:
        if provider == "gmail":
            clients.append(GmailApiEmailClient(debug=debug))
            continue
        allow_legacy = selected_provider == provider
        try:
            config = ImapConfig.for_provider(provider, allow_legacy=allow_legacy)
            clients.append(ImapEmailClient(config, debug=debug))
            debug_log(f"configured {provider} provider with user={config.user} host={config.host}", debug)
        except RuntimeError as exc:
            debug_log(f"skipping {provider} provider config: {exception_summary(exc)}", debug)
            continue
    return clients


class _ImapSession:
    def __init__(self, mail):
        self.mail = mail

    def __enter__(self):
        return self.mail

    def __exit__(self, exc_type, exc, tb):
        try:
            self.mail.logout()
        finally:
            return False


def _message_id(msg, uid: str) -> str:
    raw = msg.get("Message-ID")
    if not raw:
        return f"uid-{uid}"
    return str(raw).strip("<>")


def _decode_subject(value: str) -> str:
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _body_from_message(msg) -> str:
    plain = ""
    html = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                continue
            content_type = part.get_content_type()
            payload = part.get_content()
            if content_type == "text/plain" and not plain:
                plain = str(payload)
            elif content_type == "text/html" and not html:
                html = str(payload)
    else:
        content_type = msg.get_content_type()
        payload = str(msg.get_content())
        if content_type == "text/html":
            html = payload
        else:
            plain = payload

    return plain or _strip_html(html)


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return unescape(re.sub(r"\s+", " ", text)).strip()


def _decode_gmail_raw(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode((raw + padding).encode("ascii"))
