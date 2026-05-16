"""Provider-aware JSON LLM client shared by document analysis agents."""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


class ProviderJSONLLMClient:
    """Generate JSON through the backend provider router.

    The production backend exposes ``core.llm.generate_text`` as the single
    provider-aware entry point. Keeping agent clients behind this adapter avoids
    bypassing ``LLM_PROVIDER=google`` / Vertex AI authentication.
    """

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
    ) -> None:
        # Keep this explicit. Passing None lets core.llm choose the correct
        # provider-specific analysis model from Settings.
        self._model = model
        self._api_key = api_key

    @property
    def model(self) -> str | None:
        return self._model

    def generate(self, messages: list[dict], response_schema: dict | None = None) -> dict:
        try:
            from core.llm import generate_text
        except ImportError as exc:
            raise RuntimeError(
                "core.llm.generate_text is required for provider-aware agent LLM calls"
            ) from exc

        try:
            provider = "unknown"
            try:
                from core.config import get_settings
                provider = get_settings().llm_provider
            except Exception:
                pass
            logger.info(
                "Agent LLM JSON generation started provider=%s model=%s messages=%d",
                provider,
                self._model or "<settings-default>",
                len(messages),
            )
            raw_text = generate_text(
                messages=messages,
                model=self._model,
                temperature=0.0,
            )
        except Exception:
            logger.exception(
                "Agent LLM JSON generation failed model=%s messages=%d",
                self._model or "<settings-default>",
                len(messages),
            )
            raise
        return self._parse_json(raw_text or "{}")

    @staticmethod
    def _parse_json(text: str) -> dict:
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(
                line for line in lines if not line.strip().startswith("```")
            ).strip()
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError as exc:
            logger.warning(
                "Failed to parse LLM JSON output: %s; output_prefix=%r",
                exc,
                text[:500],
            )
            return {}
