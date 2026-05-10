from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from calendar_agent.models import EmailRecord


class ProfileConfigError(Exception):
    pass


DEFAULT_DURATION_MINUTES = 30


@dataclass(frozen=True)
class SourceProfile:
    name: str
    from_pattern: re.Pattern
    subject_pattern: re.Pattern
    to_pattern: re.Pattern | None
    body_pattern: re.Pattern | None
    prompt_template: str
    duration_minutes: int = DEFAULT_DURATION_MINUTES
    mailbox: str | None = None  # IMAP folder / Gmail label; None → provider default (INBOX)

    @property
    def from_search_term(self) -> str:
        """Plain-text FROM address for IMAP/Gmail search (unescapes regex backslashes)."""
        return re.sub(r"\\(.)", r"\1", self.from_pattern.pattern)


def load_profiles(path: Path) -> list[SourceProfile]:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ProfileConfigError("PyYAML is required for profiles. Run: pip install pyyaml") from exc

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ProfileConfigError(f"Profiles file not found: {path}")

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ProfileConfigError(f"Invalid YAML in {path}: {exc}") from exc

    raw_profiles = (data or {}).get("profiles", [])
    if not isinstance(raw_profiles, list):
        raise ProfileConfigError(f"{path}: 'profiles' must be a list")

    profiles: list[SourceProfile] = []
    for i, raw in enumerate(raw_profiles):
        profiles.append(_parse_profile(raw, index=i, path=path))
    return profiles


def match_profile(email: "EmailRecord", profiles: list[SourceProfile]) -> SourceProfile | None:
    for profile in profiles:
        if not profile.from_pattern.search(email.sender or ""):
            continue
        if not profile.subject_pattern.search(email.subject or ""):
            continue
        if profile.to_pattern and not profile.to_pattern.search(email.recipient or ""):
            continue
        if profile.body_pattern and not profile.body_pattern.search(email.body or ""):
            continue
        return profile
    return None


def default_profiles_yaml() -> str:
    return '''\
profiles:
  - name: inclubgolf
    from_pattern: "noreply@inclubgolf\\.com"
    subject_pattern: ".*"
    prompt_template: |
      Extract and validate one InClubGolf calendar event from this email.

      Use the deterministic_candidate as a proposed parse. Confirm it if correct; correct it if the
      email text shows different event details. This LLM response is required even when the candidate
      looks complete.

      Return one JSON object only:
      {
        "is_event": true,
        "event_type": "Practice" or "Lesson",
        "status": "Reserved" or "Canceled",
        "location": "clean location string",
        "date": "MM/DD/YYYY",
        "time": "HH:MM AM/PM",
        "confidence": number between 0 and 1
      }

      If this is not a lesson/practice reservation or cancellation, return:
      {"is_event": false, "confidence": 0}

      Rules:
      - All event times are America/New_York.
      - Do not invent missing date, time, type, status, or location.
      - Treat "cancelled" and "canceled" as "Canceled".
      - Treat confirmed/reserved bookings as "Reserved".
      - Keep the location in the form used by the email, for example "Lake Nona in North Bay".
'''


def ensure_default_profiles(path: Path) -> None:
    if not path.exists():
        path.write_text(default_profiles_yaml(), encoding="utf-8")
        _log(f"Created default profiles file: {path}")


def profiles_from_env() -> list[SourceProfile]:
    profiles_file = Path(os.getenv("AGENT_PROFILES_FILE", "profiles.yaml"))
    ensure_default_profiles(profiles_file)
    return load_profiles(profiles_file)


def _parse_profile(raw: object, index: int, path: Path) -> SourceProfile:
    if not isinstance(raw, dict):
        raise ProfileConfigError(f"{path}: profile[{index}] must be a mapping")

    name = raw.get("name")
    if not name or not isinstance(name, str):
        raise ProfileConfigError(f"{path}: profile[{index}] missing required 'name'")

    from_pat = _compile(raw, "from_pattern", required=True, path=path, name=name)
    subject_pat = _compile(raw, "subject_pattern", required=False, path=path, name=name) or re.compile(".*")
    to_pat = _compile(raw, "to_pattern", required=False, path=path, name=name)
    body_pat = _compile(raw, "body_pattern", required=False, path=path, name=name)

    prompt_template = raw.get("prompt_template", "")
    if not isinstance(prompt_template, str) or not prompt_template.strip():
        raise ProfileConfigError(f"{path}: profile '{name}' missing required 'prompt_template'")

    raw_duration = raw.get("duration_minutes", DEFAULT_DURATION_MINUTES)
    if not isinstance(raw_duration, int) or raw_duration <= 0:
        raise ProfileConfigError(f"{path}: profile '{name}'.duration_minutes must be a positive integer")

    mailbox = raw.get("mailbox")
    if mailbox is not None and not isinstance(mailbox, str):
        raise ProfileConfigError(f"{path}: profile '{name}'.mailbox must be a string")

    return SourceProfile(
        name=name,
        from_pattern=from_pat,
        subject_pattern=subject_pat,
        to_pattern=to_pat,
        body_pattern=body_pat,
        prompt_template=prompt_template.strip(),
        duration_minutes=raw_duration,
        mailbox=mailbox or None,
    )


def _compile(raw: dict, key: str, *, required: bool, path: Path, name: str) -> re.Pattern | None:
    value = raw.get(key)
    if value is None:
        if required:
            raise ProfileConfigError(f"{path}: profile '{name}' missing required '{key}'")
        return None
    if not isinstance(value, str):
        raise ProfileConfigError(f"{path}: profile '{name}'.{key} must be a string")
    try:
        return re.compile(value)
    except re.error as exc:
        raise ProfileConfigError(f"{path}: profile '{name}'.{key} invalid regex: {exc}") from exc


def _log(msg: str) -> None:
    import sys
    print(msg, file=sys.stderr)
