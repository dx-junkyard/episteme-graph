"""LLM client for ApparatusSemanticsAgent (multi-image vision, OpenAI v1).

``ProviderJSONLLMClient`` (llm_json_client.py, shared by every document
analysis agent) is text-only and is NOT modified here. Vision dispatch is
implemented entirely in this subclass, mirroring the single-image pattern
``equation_semantics/llm_client.py`` uses (``_generate_openai_vision`` etc.)
but generalized to a *list* of images — the figure image today, and (when
``APPARATUS_FEWSHOT_IMAGES=true``, design doc §5-3) retrieved library exemplar
images in the future.

``backend/core/llm.py::generate_structured_with_images(prompt, images, schema,
model)`` already provides the OpenAI-only vision call (multi-image, v1 scope
per design doc §5-4) — this client builds the flat prompt + image byte list it
expects and defers to it rather than re-implementing the OpenAI call.
"""
from __future__ import annotations

import base64
import logging

from episteme_graph.agents.llm_json_client import ProviderJSONLLMClient

logger = logging.getLogger(__name__)

# Loose JSON schema hint for the vision structured-output call. This is not the
# source of truth for acceptance — validator.py / repair.py own that — it only
# nudges the model's response_format toward the right shape.
APPARATUS_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "apparatus_name_candidate": {"type": "string"},
        "matched_library_entry_id": {"type": ["string", "null"]},
        "match_status": {"type": "string", "enum": ["matched", "novel", "unknown"]},
        "parts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "role": {"type": "string"},
                    "evidence_quote": {"type": "string"},
                    "reason": {"type": "string"},
                    "confidence": {"type": "number"},
                },
            },
        },
        "connections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from_part": {"type": "string"},
                    "to_part": {"type": "string"},
                    "relation": {"type": "string"},
                    "reason": {"type": "string"},
                    "confidence": {"type": "number"},
                },
            },
        },
        "evidence_quote": {"type": "string"},
        "reason": {"type": "string"},
        "confidence": {"type": "number"},
    },
}


class ApparatusSemanticsLLMClient(ProviderJSONLLMClient):
    """Provider-aware JSON client extended with multi-image vision input."""

    def generate(
        self,
        messages: list[dict],
        response_schema: dict | None = None,
        images: list[dict] | None = None,
    ) -> dict:
        """Generate structured JSON, optionally with one or more images.

        ``images`` is a list of ``{"mime_type": str, "data_base64": str}``
        dicts (the same per-image shape ``equation_semantics`` uses for its
        single image, generalized to a list here). When empty/``None`` this
        falls back to the plain text-only ``ProviderJSONLLMClient.generate``.
        """
        if not images:
            return super().generate(messages, response_schema=response_schema)

        try:
            from core.llm import generate_structured_with_images
        except ImportError as exc:
            raise RuntimeError(
                "core.llm.generate_structured_with_images is required for "
                "apparatus_semantics vision calls"
            ) from exc

        prompt = self._flatten_messages(messages)
        schema = response_schema or APPARATUS_RESPONSE_SCHEMA
        image_bytes = [self._decode_image(image) for image in images if image]
        raw = generate_structured_with_images(prompt, image_bytes, schema, model=self._model)
        self.last_raw_text = None
        self.last_parse_error = None
        return raw if isinstance(raw, dict) else {}

    @staticmethod
    def _decode_image(image: dict) -> bytes:
        data_b64 = str(image.get("data_base64", "") or "")
        if not data_b64:
            return b""
        return base64.b64decode(data_b64.encode("ascii"))

    @staticmethod
    def _flatten_messages(messages: list[dict]) -> str:
        """Collapse a system/user message list into one vision prompt string.

        ``generate_structured_with_images`` takes a single flat prompt (one
        ``user`` message carries text + image parts, per development rule 4 —
        no ``system`` role / temperature for the vision call), so system
        instructions are folded into the prompt text instead of a separate role.
        """
        return "\n\n".join(str(message.get("content", "")) for message in messages)
