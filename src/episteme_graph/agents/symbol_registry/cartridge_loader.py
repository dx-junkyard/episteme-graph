"""CartridgeLoader for SymbolRegistryBuilder (issue #355).

Thin re-export of the shared CartridgeContext/CartridgeLoader (see
docs/architecture/consolidation_survey_2026-07.md, Tier 2 proposal 9:
"cartridge 読み込みの統合"). This agent previously defined its own copy of both
classes here instead of in ``schema.py``; the actual implementation now lives
in ``episteme_graph.agents.cartridge_context`` /
``episteme_graph.agents.cartridge_loader`` and is shared by every agent whose
CartridgeContext has the standard shape.
"""
from __future__ import annotations

from episteme_graph.agents.cartridge_context import CartridgeContext
from episteme_graph.agents.cartridge_loader import CartridgeLoader

__all__ = ["CartridgeContext", "CartridgeLoader"]
