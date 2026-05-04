"""LLM client for thesis reconstruction JSON generation."""
from __future__ import annotations

from episteme_graph.agents.llm_json_client import ProviderJSONLLMClient


class ThesisReconstructionLLMClient(ProviderJSONLLMClient):
    """Provider-aware JSON client. Tests should mock generate()."""
