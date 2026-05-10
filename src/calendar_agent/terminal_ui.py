from __future__ import annotations

import json
import sys
from typing import Callable, Literal

from calendar_agent.parser import ParseTrace


class TerminalUI:
    def __init__(
        self,
        input_fn: Callable[[str], str] = input,
        print_fn: Callable[[str], None] | None = None,
    ):
        self._input = input_fn
        self._print = print_fn or (lambda s: print(s, flush=True))

    def is_interactive(self) -> bool:
        return sys.stdin.isatty() and sys.stdout.isatty()

    def show_trace(self, trace: ParseTrace) -> None:
        self._print("")
        self._print("=" * 60)
        self._print("PARSE FAILURE")
        self._print("=" * 60)
        self._print(f"UID     : {trace.uid}")
        self._print(f"Subject : {trace.subject}")
        self._print(f"Reason  : {trace.failure_reason or 'unknown'}")
        self._print("")
        self._print("Body preview:")
        self._print(f"  {trace.body_preview}")
        self._print("")
        self._print("Deterministic candidate:")
        self._print(f"  {json.dumps(trace.deterministic_candidate, indent=2)}")
        self._print("")
        self._print("LLM response:")
        self._print(f"  {json.dumps(trace.llm_response, indent=2)}")
        self._print("")
        self._print("Full prompt (press Enter to skip, or type 'show'):")
        choice = self._input("  > ").strip().lower()
        if choice == "show":
            self._print("")
            self._print(trace.prompt)
            self._print("")

    def ask_is_event(self) -> Literal["yes", "no", "skip", "skip_all"]:
        while True:
            raw = self._input("Is this a valid calendar event? [y=yes / n=no / s=skip / S=skip all]: ").strip()
            if raw in {"y", "yes"}:
                return "yes"
            if raw in {"n", "no"}:
                return "no"
            if raw == "s":
                return "skip"
            if raw == "S":
                return "skip_all"
            self._print("  Please enter y, n, s, or S.")

    def ask_ignore_permanently(self) -> bool:
        raw = self._input("Mark this subject pattern as permanently ignored? [y/N]: ").strip().lower()
        return raw in {"y", "yes"}

    def ask_field(
        self,
        label: str,
        current: str | None,
        choices: list[str] | None = None,
    ) -> str:
        hint = f"[{current}]" if current else "(empty)"
        choices_str = f" ({'/'.join(choices)})" if choices else ""
        prompt = f"  {label}{choices_str} {hint}: "
        raw = self._input(prompt).strip()
        return raw if raw else (current or "")

    def ask_run_sync(self) -> bool:
        raw = self._input("Run full sync for this email now? [Y/n]: ").strip().lower()
        return raw not in {"n", "no"}

    def ask_save_correction(self) -> bool:
        raw = self._input("Save this correction for future runs? [Y/n]: ").strip().lower()
        return raw not in {"n", "no"}

    def show_prompt_proposal(self, proposal: str) -> None:
        self._print("")
        self._print("Proposed prompt addition:")
        self._print("-" * 40)
        for line in proposal.strip().splitlines():
            self._print(f"  {line}")
        self._print("-" * 40)

    def ask_apply_prompt_change(self) -> bool:
        raw = self._input("Apply this prompt change? [Y/n]: ").strip().lower()
        return raw not in {"n", "no"}

    def info(self, msg: str) -> None:
        self._print(msg)


class MockTerminalUI(TerminalUI):
    """Scripted terminal for tests. Pops responses from a queue."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self._output: list[str] = []
        super().__init__(
            input_fn=self._pop,
            print_fn=self._capture,
        )

    def is_interactive(self) -> bool:
        return True

    def _pop(self, _prompt: str = "") -> str:
        if not self._responses:
            raise AssertionError("MockTerminalUI ran out of scripted responses")
        return self._responses.pop(0)

    def _capture(self, msg: str) -> None:
        self._output.append(msg)
