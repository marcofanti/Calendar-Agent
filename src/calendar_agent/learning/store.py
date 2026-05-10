from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class SavedExample:
    id: str
    added_at: str
    subject: str
    body_preview: str
    correction: dict
    confirmed: bool = True


@dataclass
class IgnoredPattern:
    id: str
    added_at: str
    subject_pattern: str
    reason: str


@dataclass
class PromptOverride:
    id: str
    added_at: str
    content: str
    trigger_subject: str


class LearningStore:
    def __init__(self, profile_name: str, base_dir: Path | None = None):
        if base_dir is None:
            base_dir = Path(os.getenv("AGENT_LEARNING_DIR", ".calendar-agent/learned"))
        self._dir = base_dir / profile_name
        self._examples_path = self._dir / "examples.json"
        self._ignored_path = self._dir / "ignored.json"
        self._overrides_path = self._dir / "prompt_overrides.json"

    # --- examples ---

    def load_examples(self) -> list[SavedExample]:
        return [SavedExample(**item) for item in self._read_json(self._examples_path, default=[])]

    def save_example(self, example: SavedExample) -> None:
        examples = self.load_examples()
        examples.append(example)
        self._write_json(self._examples_path, [asdict(e) for e in examples])

    def new_example(self, subject: str, body_preview: str, correction: dict) -> SavedExample:
        return SavedExample(
            id=str(uuid.uuid4()),
            added_at=_now_iso(),
            subject=subject,
            body_preview=body_preview,
            correction=correction,
        )

    # --- ignored patterns ---

    def load_ignored(self) -> list[IgnoredPattern]:
        return [IgnoredPattern(**item) for item in self._read_json(self._ignored_path, default=[])]

    def save_ignored(self, pattern: IgnoredPattern) -> None:
        patterns = self.load_ignored()
        patterns.append(pattern)
        self._write_json(self._ignored_path, [asdict(p) for p in patterns])

    def new_ignored(self, subject_pattern: str, reason: str) -> IgnoredPattern:
        return IgnoredPattern(
            id=str(uuid.uuid4()),
            added_at=_now_iso(),
            subject_pattern=subject_pattern,
            reason=reason,
        )

    def is_ignored(self, subject: str) -> bool:
        import re
        for pattern in self.load_ignored():
            try:
                if re.search(pattern.subject_pattern, subject, re.IGNORECASE):
                    return True
            except re.error:
                pass
        return False

    # --- prompt overrides ---

    def load_overrides(self) -> list[PromptOverride]:
        return [PromptOverride(**item) for item in self._read_json(self._overrides_path, default=[])]

    def save_override(self, override: PromptOverride) -> None:
        overrides = self.load_overrides()
        overrides.append(override)
        self._write_json(self._overrides_path, [asdict(o) for o in overrides])

    def new_override(self, content: str, trigger_subject: str) -> PromptOverride:
        return PromptOverride(
            id=str(uuid.uuid4()),
            added_at=_now_iso(),
            content=content,
            trigger_subject=trigger_subject,
        )

    def build_prompt_context(self, max_chars: int = 12000) -> str:
        """Assemble few-shot examples + overrides into a prompt context block."""
        parts: list[str] = []

        overrides = self.load_overrides()
        if overrides:
            rules = "\n".join(f"- {o.content.strip()}" for o in overrides)
            parts.append(f"Additional rules learned from corrections:\n{rules}")

        examples = self.load_examples()
        if examples:
            lines = ["Corrected examples (use these as few-shot guidance):"]
            for ex in examples:
                lines.append(json.dumps({
                    "subject": ex.subject,
                    "correction": ex.correction,
                }, ensure_ascii=False))
            parts.append("\n".join(lines))

        context = "\n\n".join(parts)

        # Trim oldest examples if over budget, keeping overrides intact
        if len(context) > max_chars and examples:
            while len(context) > max_chars and examples:
                examples.pop(0)
                lines = ["Corrected examples (use these as few-shot guidance):"]
                for ex in examples:
                    lines.append(json.dumps({
                        "subject": ex.subject,
                        "correction": ex.correction,
                    }, ensure_ascii=False))
                parts[-1] = "\n".join(lines)
                context = "\n\n".join(parts)

        return context.strip()

    # --- internal ---

    def _read_json(self, path: Path, default: list) -> list:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default

    def _write_json(self, path: Path, data: list) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
