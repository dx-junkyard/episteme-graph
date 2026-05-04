"""LLM client for RhetoricalRoleAgent JSON generation."""
from __future__ import annotations

from episteme_graph.agents.llm_json_client import ProviderJSONLLMClient


class RhetoricalRoleLLMClient(ProviderJSONLLMClient):
    """Provider-aware JSON client. Tests should mock generate()."""
