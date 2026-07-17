"""Build ApparatusSemanticsAgent LLM inputs.

Non-LLM assembly step: turns a ``FigureImageInput`` + retrieved
``LibraryCandidate`` list + optional cartridge into the plain-data pieces
``prompt.py`` renders into messages and ``llm_client.py`` sends to the vision
model. No domain vocabulary is hardcoded here (design principle #5) — the
candidate briefs and cartridge hints are the only source of domain knowledge,
and both degrade gracefully to empty when unavailable.
"""
from __future__ import annotations

import base64

from .schema import CartridgeContext, FigureImageInput, LibraryCandidate

# Backstop only — the upstream figure_context stage (§ image pipeline design)
# owns the main text budget for nearby_text. These caps just guard against an
# unexpectedly large upstream payload reaching the LLM prompt unbounded.
_MAX_NEARBY_TEXT_ITEMS = 16
_MAX_NEARBY_TEXT_CHARS = 1500

_MAX_INNER_LABEL_HINTS = 60
_MAX_ABBREVIATIONS = 40

# Guided re-analysis (guided_figure_reanalysis_design.md §6-2): teacher
# hint_text is re-capped here so a caller that bypasses the API-layer
# validation (e.g. a direct core call) still cannot blow up the prompt.
_MAX_GUIDANCE_TEXT_CHARS = 2000

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"


class ApparatusSemanticsInputBuilder:
    def build_image_payload(self, figure: FigureImageInput) -> dict | None:
        """Return ``{"mime_type", "data_base64"}`` for the figure image, or
        ``None`` when no image bytes are available (agent.py handles that case
        by never calling the LLM — see design doc §5-2).

        Kept for backward compatibility (single-image callers); returns the
        first element of :meth:`build_image_payloads` (or ``None``).
        """
        payloads = self.build_image_payloads(figure)
        return payloads[0] if payloads else None

    def build_image_payloads(self, figure: FigureImageInput) -> list[dict]:
        """Return the ordered image payload list for the vision call.

        First element (when present) is always the original figure image.
        A second element — a magnified crop of the teacher's focus region —
        is appended when ``figure.focus_image_bytes`` is set (guided
        re-analysis only; batch pipeline figures never set this field, so
        this always degrades to a single-element/empty list there — GF7).
        Returns ``[]`` when there is no original image at all.
        """
        if not figure.image_bytes:
            return []
        payloads = [{
            "mime_type": self._detect_mime_type(figure.image_bytes),
            "data_base64": base64.b64encode(figure.image_bytes).decode("ascii"),
        }]
        if figure.focus_image_bytes:
            payloads.append({
                "mime_type": self._detect_mime_type(figure.focus_image_bytes),
                "data_base64": base64.b64encode(figure.focus_image_bytes).decode("ascii"),
            })
        return payloads

    def build_guidance(self, figure: FigureImageInput) -> dict:
        """Normalize the teacher-guidance inputs for ``prompt.py``.

        Returns ``{}`` (falsy) when there is nothing to say — no hint text,
        no focus region, no focus image, and no focus labels — so
        ``prompt.py`` can skip the "## Teacher Guidance" section entirely for
        every batch-pipeline figure (GF7) and for un-guided deliberation
        re-analyze calls alike.
        """
        hint_text = str(figure.guidance_text or "").strip()[:_MAX_GUIDANCE_TEXT_CHARS]
        focus_bbox_rel = list(figure.focus_bbox_rel) if figure.focus_bbox_rel else None
        focus_label_texts = [
            str(t).strip() for t in (figure.focus_label_texts or []) if str(t).strip()
        ]
        has_focus_image = bool(figure.focus_image_bytes)

        if not (hint_text or focus_bbox_rel or has_focus_image or focus_label_texts):
            return {}

        return {
            "hint_text": hint_text,
            "focus_bbox_rel": focus_bbox_rel,
            "focus_label_texts": focus_label_texts,
            "has_focus_image": has_focus_image,
        }

    def build_candidate_briefs(self, candidates: list[LibraryCandidate] | None) -> list[dict]:
        """Text-only few-shot briefs for the retrieved library candidates
        (§5-3). An empty list is a fully supported input — the agent then
        degrades to ``match_status ∈ {novel, unknown}`` only."""
        briefs: list[dict] = []
        for candidate in candidates or []:
            body = candidate.body if isinstance(candidate.body, dict) else {}
            briefs.append({
                "entry_id": candidate.entry_id,
                "version_no": candidate.version_no,
                "name": candidate.name,
                "aliases": list(candidate.aliases or []),
                "summary": candidate.summary,
                "typical_parts": body.get("typical_parts", []),
                "visual_cues": body.get("visual_cues", []),
                "typical_configurations": body.get("typical_configurations", []),
            })
        return briefs

    def build_nearby_text(self, figure: FigureImageInput) -> list[str]:
        texts = [t.strip() for t in (figure.nearby_text or []) if t and t.strip()]
        return [t[:_MAX_NEARBY_TEXT_CHARS] for t in texts[:_MAX_NEARBY_TEXT_ITEMS]]

    def build_inner_label_hints(self, figure: FigureImageInput) -> list[str]:
        """Distinct in-figure label strings, order-preserving, for the prompt.

        Text only (no bbox) — the LLM only ever needs to reference a label by
        name (``ApparatusPart.label_ref``); bbox grounding happens
        deterministically downstream in agent.py, never from the LLM.
        """
        hints: list[str] = []
        seen: set[str] = set()
        for label in figure.inner_labels or []:
            if not isinstance(label, dict):
                continue
            text = str(label.get("text", "") or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            hints.append(text)
            if len(hints) >= _MAX_INNER_LABEL_HINTS:
                break
        return hints

    def build_abbreviations(self, figure: FigureImageInput) -> dict[str, str]:
        """Normalized abbreviation → expansion dict, insertion-order capped."""
        abbreviations: dict[str, str] = {}
        for key, value in (figure.abbreviations or {}).items():
            key = str(key).strip()
            value = str(value).strip()
            if not key or not value:
                continue
            abbreviations[key] = value
            if len(abbreviations) >= _MAX_ABBREVIATIONS:
                break
        return abbreviations

    def build_cartridge_hints(self, cartridge: CartridgeContext | None) -> dict:
        """Optional vocabulary hints only — the agent must work identically
        (minus these hints) when ``cartridge`` is ``None`` (design principle 1)."""
        if not cartridge:
            return {}
        hints = cartridge.extraction_hints
        component_types: list = []
        relation_types: list = []
        if isinstance(hints, dict):
            component_types = hints.get("component_types") or []
            relation_types = hints.get("relation_types") or []
        return {
            "component_types": component_types,
            "relation_types": relation_types,
            "aliases": cartridge.aliases or {},
        }

    @staticmethod
    def _detect_mime_type(image_bytes: bytes) -> str:
        if image_bytes.startswith(_PNG_MAGIC):
            return "image/png"
        if image_bytes.startswith(_JPEG_MAGIC):
            return "image/jpeg"
        return "image/png"
