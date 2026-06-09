"""Provider-aware JSON LLM client shared by document analysis agents."""
from __future__ import annotations

import json
import logging
import os
import queue
import threading

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
        self.last_raw_text: str | None = None
        self.last_parse_error: str | None = None

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
            timeout = float(os.getenv("AGENT_LLM_TIMEOUT_SECONDS", "300"))
            wall_timeout = float(
                os.getenv(
                    "AGENT_LLM_WALL_TIMEOUT_SECONDS",
                    str(max(timeout + 30.0, timeout * 1.1)),
                )
            )
            raw_text = _call_with_wall_timeout(
                generate_text,
                wall_timeout,
                messages=messages,
                model=self._model,
                temperature=0.0,
                max_tokens=int(os.getenv("AGENT_LLM_MAX_TOKENS", "12000")),
                timeout=timeout,
            )
            self.last_raw_text = raw_text
            self.last_parse_error = None
        except Exception:
            self.last_raw_text = None
            self.last_parse_error = None
            logger.exception(
                "Agent LLM JSON generation failed model=%s messages=%d",
                self._model or "<settings-default>",
                len(messages),
            )
            raise
        return self._parse_json(raw_text or "{}")

    def _parse_json(self, text: str) -> dict:
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(
                line for line in lines if not line.strip().startswith("```")
            ).strip()
        try:
            parsed = json.loads(text)
            self.last_parse_error = None
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError as exc:
            self.last_parse_error = str(exc)
            logger.warning(
                "Failed to parse LLM JSON output: %s; output_prefix=%r",
                exc,
                text[:500],
            )
            return {}


def _call_with_wall_timeout(func, timeout_seconds: float, *args, **kwargs):
    result_queue: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)

    def target() -> None:
        try:
            result_queue.put(("ok", func(*args, **kwargs)))
        except Exception as exc:
            result_queue.put(("error", exc))

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    try:
        status, value = result_queue.get(timeout=timeout_seconds)
    except queue.Empty as exc:
        raise TimeoutError(f"Agent LLM call exceeded {timeout_seconds:.1f}s") from exc
    if status == "error":
        raise value  # type: ignore[misc]
    return value
