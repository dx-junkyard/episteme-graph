"""LLM client for DSLLinkingAgent."""
from __future__ import annotations

from episteme_graph.agents.llm_json_client import ProviderJSONLLMClient


class DSLLinkingLLMClient(ProviderJSONLLMClient):
    """Provider-aware JSON client. Tests should mock generate()."""
