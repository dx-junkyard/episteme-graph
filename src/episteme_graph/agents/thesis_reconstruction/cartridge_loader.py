"""CartridgeLoader for ThesisReconstructionAgent.

Thin re-export of the shared loader (see
docs/architecture/consolidation_survey_2026-07.md, Tier 2 proposal 9:
"cartridge 読み込みの統合"). The actual implementation lives in
``episteme_graph.agents.cartridge_loader`` and is shared by every agent whose
CartridgeContext has the standard shape.
"""
from __future__ import annotations

from episteme_graph.agents.cartridge_loader import CartridgeLoader

__all__ = ["CartridgeLoader"]
