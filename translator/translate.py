"""Core translation logic: one Claude call returns all three target languages."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import anthropic

MODEL = os.environ.get("TRANSLATOR_MODEL", "claude-opus-5")

TARGETS = {
    "zh": "Simplified Chinese (简体中文)",
    "en": "English",
    "ja": "Japanese (日本語)",
}

SYSTEM_PROMPT = """You are a professional translator.
The user gives you Korean text. Translate it into Simplified Chinese, English, and Japanese.

Guidelines:
- Preserve meaning, tone, register (formal/informal), and formatting (line breaks, lists).
- Do not add explanations, notes, or romanization. Output only the translations.
- If the input is not Korean, still translate it into the three target languages.
- Keep proper nouns, product names, numbers, and URLs intact.
"""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "zh": {"type": "string", "description": "Simplified Chinese translation"},
        "en": {"type": "string", "description": "English translation"},
        "ja": {"type": "string", "description": "Japanese translation"},
    },
    "required": ["zh", "en", "ja"],
    "additionalProperties": False,
}


@dataclass
class Translation:
    zh: str
    en: str
    ja: str

    def as_dict(self) -> dict[str, str]:
        return {"zh": self.zh, "en": self.en, "ja": self.ja}


_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def translate(text: str) -> Translation:
    text = text.strip()
    if not text:
        raise ValueError("입력 텍스트가 비어 있습니다.")

    response = get_client().messages.create(
        model=MODEL,
        max_tokens=16000,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": text}],
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
        },
    )

    if response.stop_reason == "refusal":
        detail = response.stop_details.explanation if response.stop_details else ""
        raise RuntimeError(f"모델이 번역을 거부했습니다. {detail}".strip())
    if response.stop_reason == "max_tokens":
        raise RuntimeError("출력이 너무 길어 잘렸습니다. 텍스트를 나눠서 시도해 주세요.")

    raw = next(b.text for b in response.content if b.type == "text")
    data = json.loads(raw)
    return Translation(zh=data["zh"], en=data["en"], ja=data["ja"])
