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

_MAX_NEARBY_TEXT_ITEMS = 6
_MAX_NEARBY_TEXT_CHARS = 600

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"


class ApparatusSemanticsInputBuilder:
    def build_image_payload(self, figure: FigureImageInput) -> dict | None:
        """Return ``{"mime_type", "data_base64"}`` for the figure image, or
        ``None`` when no image bytes are available (agent.py handles that case
        by never calling the LLM — see design doc §5-2)."""
        if not figure.image_bytes:
            return None
        return {
            "mime_type": self._detect_mime_type(figure.image_bytes),
            "data_base64": base64.b64encode(figure.image_bytes).decode("ascii"),
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

    def build_cartridge_hints(self, cartridge: CartridgeContext | None) -> dict:
        """Optional vocabulary hints only — the agent must work identically
        (minus these hints) when ``cartridge`` is ``None`` (design principle 1)."""
        if not cartridge:
            return {}
        hints = cartridge.extraction_hints
        component_types: list = []
        if isinstance(hints, dict):
            component_types = hints.get("component_types") or []
        return {
            "component_types": component_types,
            "aliases": cartridge.aliases or {},
        }

    @staticmethod
    def _detect_mime_type(image_bytes: bytes) -> str:
        if image_bytes.startswith(_PNG_MAGIC):
            return "image/png"
        if image_bytes.startswith(_JPEG_MAGIC):
            return "image/jpeg"
        return "image/png"
