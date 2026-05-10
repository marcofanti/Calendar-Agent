from __future__ import annotations

import os


def debug_enabled() -> bool:
    return os.getenv("DEBUG", "false").lower() in {"1", "true", "yes", "on"}


def debug_log(message: str, enabled: bool | None = None) -> None:
    if enabled is None:
        enabled = debug_enabled()
    if enabled:
        print(f"[debug] {message}")


def exception_summary(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"
