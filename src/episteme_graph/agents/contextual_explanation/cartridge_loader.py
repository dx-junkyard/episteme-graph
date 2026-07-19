"""CartridgeLoader for ContextualExplanationAgent.

Thin re-export of the shared CartridgeContext/CartridgeLoader (正本 =
``episteme_graph.agents.cartridge_context`` / ``episteme_graph.agents.cartridge_loader``,
see docs/architecture/consolidation_survey_2026-07.md Tier 2 proposal 9). This
agent has no cartridge-specific field differences, so it does not subclass
either class (cartridge is entirely optional — see design principle: agents
must work standalone without a cartridge).
"""
from __future__ import annotations

from episteme_graph.agents.cartridge_context import CartridgeContext
from episteme_graph.agents.cartridge_loader import CartridgeLoader

__all__ = ["CartridgeContext", "CartridgeLoader"]
