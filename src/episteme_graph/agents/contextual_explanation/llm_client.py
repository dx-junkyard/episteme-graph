"""LLM client for ContextualExplanationAgent.

Subclasses the shared provider-aware JSON client so calls go through
``core.llm.generate_text`` — this keeps U層 (LLM usage metering) accounting
automatic and respects ``LLM_PROVIDER`` routing, exactly like every other
document-analysis agent (see ``episteme_graph.agents.llm_json_client``).
"""
from __future__ import annotations

from episteme_graph.agents.llm_json_client import ProviderJSONLLMClient


class ContextualExplanationLLMClient(ProviderJSONLLMClient):
    """Provider-aware JSON client. Tests should mock ``generate()``."""
