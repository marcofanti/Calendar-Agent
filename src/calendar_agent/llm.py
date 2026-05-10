from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol


class LlmClient(Protocol):
    def extract_event_json(self, prompt: str) -> dict:
        """Return parsed JSON event data from an LLM prompt."""


@dataclass(frozen=True, slots=True)
class LlmConfig:
    provider: str = "ollama"
    model: str = "llama3.2"
    ollama_base_url: str = "http://localhost:11434"
    api_key: str | None = None

    @classmethod
    def from_env(cls) -> "LlmConfig":
        provider = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
        default_model = "gemini-2.5-flash" if provider == "gemini" else "llama3.2"
        return cls(
            provider=provider,
            model=os.getenv("LLM_MODEL", default_model).strip(),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/"),
            api_key=os.getenv("LLM_API_KEY") or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"),
        )


def llm_from_env() -> LlmClient:
    config = LlmConfig.from_env()
    if config.provider == "ollama":
        return OllamaClient(config)
    if config.provider == "gemini":
        return GeminiClient(config)
    raise ValueError(f"Unsupported LLM_PROVIDER: {config.provider}")


class OllamaClient:
    def __init__(self, config: LlmConfig):
        self.config = config

    def extract_event_json(self, prompt: str) -> dict:
        payload = {
            "model": self.config.model,
            "stream": False,
            "format": "json",
            "messages": [
                {
                    "role": "system",
                    "content": "You extract calendar event data and return valid JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
            "options": {"temperature": 0},
        }
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.config.ollama_base_url}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Could not reach Ollama at {self.config.ollama_base_url}. "
                "Start Ollama or change LLM_PROVIDER in .env."
            ) from exc

        content = raw.get("message", {}).get("content", "")
        return _loads_json_object(content)


class GeminiClient:
    def __init__(self, config: LlmConfig):
        if not config.api_key:
            raise RuntimeError("LLM_API_KEY or GEMINI_API_KEY is required when LLM_PROVIDER=gemini.")
        self.config = config

    def extract_event_json(self, prompt: str) -> dict:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.config.api_key)
        response = client.models.generate_content(
            model=self.config.model,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        return _loads_json_object(response.text)


def _loads_json_object(text: str) -> dict:
    text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < start:
            raise
        data = json.loads(text[start : end + 1])

    if not isinstance(data, dict):
        raise ValueError("LLM response must be a JSON object.")
    return data
