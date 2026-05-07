"""LLM client for EquationSemanticsAgent."""
from __future__ import annotations

import logging
from typing import Any

from episteme_graph.agents.llm_json_client import ProviderJSONLLMClient

logger = logging.getLogger(__name__)


class EquationSemanticsLLMClient(ProviderJSONLLMClient):
    """Provider-aware JSON client with optional vision input."""

    def generate(
        self,
        messages: list[dict],
        response_schema: dict | None = None,
        image: dict | None = None,
    ) -> dict:
        if not image:
            return super().generate(messages, response_schema=response_schema)
        try:
            from core.config import get_settings
            settings = get_settings()
            provider = settings.llm_provider
            model = self.model or settings.llm_analysis_model
        except Exception:
            logger.warning("Equation vision fallback: settings unavailable", exc_info=True)
            return self._text_fallback_with_vision_meta(
                messages,
                response_schema,
                provider=None,
                model=None,
                reason="settings_unavailable",
            )

        try:
            if provider == "openai":
                return self._with_vision_meta(
                    self._generate_openai_vision(messages, image, model),
                    provider=provider,
                    model=model,
                    used=True,
                    reason=None,
                )
            if provider in ("google", "gemini-vertex"):
                return self._with_vision_meta(
                    self._generate_vertex_vision(messages, image, model),
                    provider=provider,
                    model=model,
                    used=True,
                    reason=None,
                )
            if provider == "gemini":
                return self._with_vision_meta(
                    self._generate_gemini_vision(messages, image, model),
                    provider=provider,
                    model=model,
                    used=True,
                    reason=None,
                )
        except Exception:
            logger.warning(
                "Equation vision generation failed provider=%s model=%s; falling back to text context",
                provider,
                model,
                exc_info=True,
            )
            return self._text_fallback_with_vision_meta(
                messages,
                response_schema,
                provider=provider,
                model=model,
                reason="vision_generation_failed",
            )
        return self._text_fallback_with_vision_meta(
            messages,
            response_schema,
            provider=provider,
            model=model,
            reason="provider_has_no_equation_vision_path",
        )

    def _generate_openai_vision(self, messages: list[dict], image: dict, model: str) -> dict:
        from core.llm import _adapt_messages_for_model, _build_api_kwargs, _get_openai_client

        adapted = _adapt_messages_for_model(messages, model)
        user_idx = self._last_user_index(adapted)
        if user_idx is None:
            adapted.append({"role": "user", "content": []})
            user_idx = len(adapted) - 1
        text = adapted[user_idx].get("content", "")
        adapted[user_idx]["content"] = [
            {"type": "text", "text": str(text)},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{image['mime_type']};base64,{image['data_base64']}",
                    "detail": "high",
                },
            },
        ]
        kwargs = _build_api_kwargs(model, temperature=0.0, extra_kwargs={"response_format": {"type": "json_object"}})
        response = _get_openai_client().chat.completions.create(
            model=model,
            messages=adapted,
            **kwargs,
        )
        return self._parse_json(response.choices[0].message.content or "{}")

    def _generate_vertex_vision(self, messages: list[dict], image: dict, model: str) -> dict:
        from core.llm import _extract_vertex_ai_text, _messages_to_gemini, _get_vertex_ai_client
        from vertexai.generative_models import GenerativeModel, GenerationConfig, Part

        _get_vertex_ai_client()
        system_instruction, contents = _messages_to_gemini(messages)
        contents = self._with_gemini_image_part(contents, Part.from_data(
            data=self._b64decode(image["data_base64"]),
            mime_type=image["mime_type"],
        ))
        response = GenerativeModel(model_name=model, system_instruction=system_instruction).generate_content(
            contents,
            generation_config=GenerationConfig(
                temperature=0.0,
                response_mime_type="application/json",
            ),
        )
        return self._parse_json(_extract_vertex_ai_text(response) or "{}")

    def _generate_gemini_vision(self, messages: list[dict], image: dict, model: str) -> dict:
        from core.llm import _get_gemini_module, _messages_to_gemini

        genai = _get_gemini_module()
        system_instruction, contents = _messages_to_gemini(messages)
        contents = self._with_gemini_image_part(contents, {
            "inline_data": {
                "mime_type": image["mime_type"],
                "data": image["data_base64"],
            }
        })
        response = genai.GenerativeModel(
            model_name=model,
            system_instruction=system_instruction,
        ).generate_content(
            contents,
            generation_config={"temperature": 0.0, "response_mime_type": "application/json"},
        )
        return self._parse_json(getattr(response, "text", "") or "{}")

    @staticmethod
    def _last_user_index(messages: list[dict]) -> int | None:
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") in ("user", "developer"):
                return i
        return None

    @staticmethod
    def _with_gemini_image_part(contents: list[dict[str, Any]], image_part: Any) -> list[dict[str, Any]]:
        if not contents:
            return [{"role": "user", "parts": [image_part]}]
        updated = list(contents)
        last = dict(updated[-1])
        parts = list(last.get("parts") or [])
        parts.append(image_part)
        last["parts"] = parts
        updated[-1] = last
        return updated

    @staticmethod
    def _b64decode(value: str) -> bytes:
        import base64

        return base64.b64decode(value.encode("ascii"))

    def _text_fallback_with_vision_meta(
        self,
        messages: list[dict],
        response_schema: dict | None,
        provider: str | None,
        model: str | None,
        reason: str,
    ) -> dict:
        raw = super().generate(messages, response_schema=response_schema)
        return self._with_vision_meta(raw, provider=provider, model=model, used=False, reason=reason)

    @staticmethod
    def _with_vision_meta(
        raw: dict,
        provider: str | None,
        model: str | None,
        used: bool,
        reason: str | None,
    ) -> dict:
        if not isinstance(raw, dict):
            raw = {}
        raw["_vision_ocr"] = {
            "attempted": True,
            "used": used,
            "provider": provider,
            "model": model,
            "reason": reason,
        }
        return raw
