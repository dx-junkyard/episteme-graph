"""LLM client for thesis reconstruction JSON generation."""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)
_REASONING_PREFIXES = ("o1", "o3", "o4")


class ThesisReconstructionLLMClient:
    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        self._model = model or os.environ.get("LLM_ANALYSIS_MODEL") or "o3-mini"
        self._api_key = (
            api_key
            or os.environ.get("LLM_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )

    def generate(self, messages: list[dict], response_schema: dict | None = None) -> dict:
        try:
            import openai
        except ImportError as exc:
            raise RuntimeError("openai package is required for ThesisReconstructionLLMClient") from exc
        client = openai.OpenAI(api_key=self._api_key)
        adapted = self._adapt_messages(messages)
        kwargs = self._build_kwargs()
        try:
            response = client.chat.completions.create(
                model=self._model,
                messages=adapted,
                response_format={"type": "json_object"},
                **kwargs,
            )
        except openai.BadRequestError:
            response = client.chat.completions.create(
                model=self._model,
                messages=adapted,
                **kwargs,
            )
        return self._parse_json(response.choices[0].message.content or "{}")

    def _is_reasoning_model(self) -> bool:
        return any(self._model.startswith(p) for p in _REASONING_PREFIXES)

    def _adapt_messages(self, messages: list[dict]) -> list[dict]:
        if not self._is_reasoning_model():
            return messages
        return [
            {**m, "role": "developer"} if m.get("role") == "system" else m
            for m in messages
        ]

    def _build_kwargs(self) -> dict[str, Any]:
        return {}

    @staticmethod
    def _parse_json(text: str) -> dict:
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(
                line for line in lines if not line.strip().startswith("```")
            )
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse LLM JSON output: %s", exc)
            return {}
