"""Load cartridge context for ComponentGraphAgent.

Subclasses the shared ``CartridgeLoader`` (see
docs/architecture/consolidation_survey_2026-07.md, Tier 2 proposal 9:
"cartridge 読み込みの統合"), reusing its base-dir resolution and JSON loading.

This agent's CartridgeContext has an extra required field (``relation_types``)
not present in the standard shape, so it keeps its own dataclass in
``schema.py`` instead of the shared
``episteme_graph.agents.cartridge_context.CartridgeContext`` — unifying the two
would silently reorder the constructor's positional arguments used by
existing call sites/tests.
"""
from __future__ import annotations

from episteme_graph.agents.cartridge_loader import CartridgeLoader as _BaseCartridgeLoader

from .schema import CartridgeContext


class CartridgeLoader(_BaseCartridgeLoader):
    def load(self, cartridge_id: str) -> CartridgeContext:
        cartridge_dir = self._cartridge_dir(cartridge_id)
        ontology = self._load_json(cartridge_dir, "ontology.json")
        validation_rules = self._load_json(cartridge_dir, "validation_rules.json")
        relation_types = self._load_json(cartridge_dir, "relation_types.json")
        aliases = (
            {a["canonical"]: a["aliases"] for a in ontology.get("aliases", [])}
            if ontology
            else None
        )
        return CartridgeContext(
            cartridge_id=cartridge_id,
            ontology=ontology,
            validation_rules=validation_rules,
            relation_types=relation_types,
            aliases=aliases,
            notation_patterns=ontology.get("notation_patterns") if ontology else None,
        )
