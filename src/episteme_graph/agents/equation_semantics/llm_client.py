"""LLM client for EquationSemanticsAgent.

The vision branch (a cropped equation image attached to the prompt) goes through
``core.llm.generate_json_with_image`` — **not** the provider SDK directly. The
provider SDK was called here until 2026-08 and therefore skipped the U層 usage
metering hooks in ``core/llm.py`` (design doc `llm_usage_metering_design.md` U3:
計測点は core/llm.py に一元化). Model resolution stays on this side
(``_resolve_vision_model``) because the vision call needs a concrete model name.
"""
from __future__ import annotations

import base64
import logging
from typing import Any

from episteme_graph.agents.llm_json_client import ProviderJSONLLMClient

logger = logging.getLogger(__name__)


def _resolve_vision_model(settings: Any) -> str:
    """Resolve the model for the vision branch through the M層 policy source of truth.

    The vision providers need a concrete model name (they are called directly,
    not through ``core.llm.generate_text``), so ``None`` is not an option here.
    Reading ``settings.llm_analysis_model`` directly would bypass the run
    override / user policy / system policy layers, so delegate to
    ``core.llm_policy.resolve_scene_model`` — its fallback for
    ``pipeline:equation_semantics`` is the analysis tier, i.e. exactly the
    previous ``settings.llm_analysis_model`` behaviour when no policy row and no
    override exist.
    """
    try:
        from core.llm_policy import SCENE_PIPELINE, resolve_scene_model

        resolved = resolve_scene_model(f"{SCENE_PIPELINE}:equation_semantics").model
        if resolved:
            return resolved
    except Exception:
        logger.debug("equation vision: llm_policy unavailable", exc_info=True)
    return settings.llm_analysis_model


class EquationSemanticsLLMClient(ProviderJSONLLMClient):
    """Provider-aware JSON client with optional vision input."""

    def generate(
        self,
        messages: list[dict],
        response_schema: dict | None = None,
        image: dict | None = None,
    ) -> dict:
        if not image:
            # Text path: keep ``model`` unset so ``core.llm`` resolves it at its own
            # entry point (M層 §3 の解決順②〜⑥ がそのまま効く)。
            return super().generate(messages, response_schema=response_schema)
        try:
            from core.config import get_settings
            settings = get_settings()
            provider = settings.llm_provider
            model = self.model or _resolve_vision_model(settings)
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
            raw_text = self._generate_vision_json(messages, image, model)
        except NotImplementedError:
            # このプロバイダには vision 経路が無い（従来と同じ理由ラベルを維持）。
            logger.info(
                "Equation vision unavailable for provider=%s; falling back to text context",
                provider,
            )
            return self._text_fallback_with_vision_meta(
                messages,
                response_schema,
                provider=provider,
                model=model,
                reason="provider_has_no_equation_vision_path",
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
        return self._with_vision_meta(
            self._parse_json(raw_text or "{}"),
            provider=provider,
            model=model,
            used=True,
            reason=None,
        )

    @staticmethod
    def _generate_vision_json(messages: list[dict], image: dict, model: str) -> str:
        """Run the single-image JSON vision call through ``core.llm``.

        This is the only seam between the agent and the provider SDK; keeping it
        in ``core.llm`` is what makes the call visible to the U層 usage metering
        hooks (``observe_vision``). Returns the raw response text — parsing stays
        on this side so ``ProviderJSONLLMClient._parse_json`` (truncated-JSON
        recovery) keeps owning it.
        """
        from core.llm import generate_json_with_image

        image_bytes = base64.b64decode(str(image.get("data_base64", "")).encode("ascii"))
        return generate_json_with_image(
            messages,
            image_bytes,
            model=model,
            mime_type=image.get("mime_type"),
        )

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
