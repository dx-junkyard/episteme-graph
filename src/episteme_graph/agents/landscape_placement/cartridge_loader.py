"""CartridgeLoader for LandscapePlacementAgent.

Thin re-export of the shared CartridgeContext/CartridgeLoader (正本 =
``episteme_graph.agents.cartridge_context`` / ``episteme_graph.agents.cartridge_loader``,
see docs/architecture/consolidation_survey_2026-07.md Tier 2 proposal 9). No
cartridge-specific field differences here, and the cartridge is entirely
optional: with no cartridge the agent still runs (only the optional vocabulary
hint in the prompt disappears). The **map** comes from the frozen atlas skeleton,
never from the cartridge files, so a skeleton-only domain
(``backend/atlas_domains/<key>/skeleton.yaml``) works with no cartridge at all.
"""
from __future__ import annotations

from episteme_graph.agents.cartridge_context import CartridgeContext
from episteme_graph.agents.cartridge_loader import CartridgeLoader

__all__ = ["CartridgeContext", "CartridgeLoader"]
