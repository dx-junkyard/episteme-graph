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
        "suggested_mode": {
            "type": "string",
            "enum": [
                "functional_diagram", "data_plot", "descriptive_image", "mixed", "unknown"
            ],
        },
        "mode_reason": {"type": "string"},
        # Mode-specific content intentionally uses a permissive nested shape:
        # validator.py owns acceptance and older providers differ in how fully
        # they implement JSON Schema for nested structured output.
        "analysis_profile": {
            "type": "object",
            "properties": {
                "overall_function": {"type": "string"},
                "external_inputs": {"type": "array", "items": {"type": "object"}},
                "external_outputs": {"type": "array", "items": {"type": "object"}},
                "functions": {"type": "array", "items": {"type": "object"}},
                "connections": {"type": "array", "items": {"type": "object"}},
                "plot_type": {"type": "string"},
                "axes": {"type": "array", "items": {"type": "object"}},
                "series": {"type": "array", "items": {"type": "object"}},
                "observations": {"type": "array", "items": {"type": "object"}},
                "interpretations": {"type": "array", "items": {"type": "object"}},
                "highlights": {"type": "array", "items": {"type": "object"}},
                "summary": {"type": "string"},
                "subjects": {"type": "array", "items": {"type": "object"}},
                "regions": {"type": "array", "items": {"type": "object"}},
                "teaching_points": {"type": "array", "items": {"type": "object"}},
                "panels": {"type": "array", "items": {"type": "object"}},
            },
        },
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
                    "label_ref": {"type": ["string", "null"]},
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


# ---------------------------------------------------------------------------
# Iterative contextual-analysis pipeline response-schema hints
# (docs/features/contextual_figure_analysis_iterative_verification.md)
#
# These are loose JSON-schema hints for each of the four pipeline steps'
# structured-output calls; acceptance is still owned by validator.py/repair.py.
# Wave 1 scaffolding — no caller in this wave dispatches these yet (the state
# machine lives in a later wave's iterative.py).
# ---------------------------------------------------------------------------

HYPOTHESIS_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "role_in_paper": {"type": "string"},
        "overall_subject": {"type": "string"},
        "expected_elements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "element_id": {"type": "string"},
                    "name": {"type": "string"},
                    "evidence_quote": {"type": "string"},
                    "expected_labels": {"type": "array", "items": {"type": "string"}},
                    "importance": {"type": "string", "enum": ["primary", "secondary"]},
                    "confidence": {"type": "number"},
                },
            },
        },
        "expected_relations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "relation_id": {"type": "string"},
                    "from_element_id": {"type": "string"},
                    "to_element_id": {"type": "string"},
                    "relation": {"type": "string"},
                    "direction": {"type": "string"},
                    "evidence_quote": {"type": "string"},
                    "confidence": {"type": "number"},
                },
            },
        },
        "expected_visual_cues": {"type": "array", "items": {"type": "string"}},
        "unstated_points": {"type": "array", "items": {"type": "string"}},
        "falsification_conditions": {"type": "array", "items": {"type": "string"}},
        "evidence_quotes": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
        "confidence": {"type": "number"},
    },
}

OBSERVATION_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "panels": {"type": "array", "items": {"type": "object"}},
        "elements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "observation_id": {"type": "string"},
                    "kind": {"type": "string"},
                    "description": {"type": "string"},
                    "label_text": {"type": "string"},
                    "region_hint": {"type": "string"},
                },
            },
        },
        "connections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from_observation_id": {"type": "string"},
                    "to_observation_id": {"type": "string"},
                    "connector": {"type": "string"},
                    "direction": {"type": "string"},
                    "description": {"type": "string"},
                },
            },
        },
        "ocr_labels": {"type": "array", "items": {"type": "string"}},
        "repeated_motifs": {"type": "array", "items": {"type": "object"}},
        "unreadable_regions": {"type": "array", "items": {"type": "object"}},
        "undecidable_elements": {"type": "array", "items": {"type": "object"}},
        "visual_mode_guess": {
            "type": "string",
            "enum": ["functional_diagram", "data_plot", "descriptive_image", "mixed", "unknown"],
        },
        "reason": {"type": "string"},
        "confidence": {"type": "number"},
    },
}

# Shared nested shapes reused by both ALIGNMENT_RESPONSE_SCHEMA and
# VERIFICATION_RESPONSE_SCHEMA — an alignment item / verification task looks
# the same whether it is emitted by the alignment step or the verification step.
_ALIGNMENT_ITEM_PROPERTIES: dict = {
    "type": "object",
    "properties": {
        "item_id": {"type": "string"},
        "item_kind": {"type": "string"},
        "label": {"type": "string"},
        "status": {
            "type": "string",
            "enum": ["supported_by_both", "visual_only", "text_only", "contradicted", "unresolved"],
        },
        "expected_ref": {"type": "string"},
        "observation_refs": {"type": "array", "items": {"type": "string"}},
        "label_ref": {"type": "string"},
        "text_evidence": {"type": "string"},
        "visual_evidence": {"type": "string"},
        "severity": {"type": "string", "enum": ["high", "medium", "low"]},
        "reason": {"type": "string"},
        "confidence": {"type": "number"},
    },
}

_VERIFICATION_TASK_PROPERTIES: dict = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
        "question": {"type": "string"},
        "target_item_ids": {"type": "array", "items": {"type": "string"}},
        "region_hint": {"type": "string"},
        "focus_bbox_rel": {"type": ["array", "null"]},
        "success_condition": {"type": "string"},
        "refutation_condition": {"type": "string"},
    },
}

ALIGNMENT_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        **APPARATUS_RESPONSE_SCHEMA["properties"],
        "alignment_items": {"type": "array", "items": _ALIGNMENT_ITEM_PROPERTIES},
        "alternative_hypotheses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "hypothesis_id": {"type": "string"},
                    "description": {"type": "string"},
                    "supporting_evidence": {"type": "array", "items": {"type": "string"}},
                    "counter_evidence": {"type": "array", "items": {"type": "string"}},
                    "unverified_conditions": {"type": "array", "items": {"type": "string"}},
                    "status": {"type": "string", "enum": ["active", "rejected", "selected"]},
                    "confidence": {"type": "number"},
                },
            },
        },
        "verification_tasks": {"type": "array", "items": _VERIFICATION_TASK_PROPERTIES},
        "review_questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question_id": {"type": "string"},
                    "question": {"type": "string"},
                    "related_item_ids": {"type": "array", "items": {"type": "string"}},
                    "region_hint": {"type": "string"},
                },
            },
        },
    },
}

VERIFICATION_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "task_findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "outcome": {"type": "string", "enum": ["resolved", "refuted", "unresolved"]},
                    "observation": {"type": "string"},
                    "evidence": {"type": "string"},
                },
            },
        },
        "updated_alignment_items": {"type": "array", "items": _ALIGNMENT_ITEM_PROPERTIES},
        "new_alignment_items": {"type": "array", "items": _ALIGNMENT_ITEM_PROPERTIES},
        "new_verification_tasks": {"type": "array", "items": _VERIFICATION_TASK_PROPERTIES},
        "hypothesis_updates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "hypothesis_id": {"type": "string"},
                    "status": {"type": "string", "enum": ["active", "rejected", "selected"]},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                },
            },
        },
        "record_deltas": {
            "type": "object",
            "properties": {
                "parts_to_add": {"type": "array", "items": {"type": "object"}},
                "parts_to_remove": {"type": "array", "items": {"type": "string"}},
                "connections_to_add": {"type": "array", "items": {"type": "object"}},
                "connections_to_remove": {"type": "array", "items": {"type": "object"}},
            },
        },
        "notes": {"type": "string"},
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
