"""Load cartridge context for ComponentAssemblyAgent.

Subclasses the shared ``CartridgeLoader`` (see
docs/architecture/consolidation_survey_2026-07.md, Tier 2 proposal 9:
"cartridge 読み込みの統合"), reusing its base-dir resolution and JSON loading.

This agent's CartridgeContext has extra required fields (``component_types``,
``relation_types``) not present in the standard shape, so it keeps its own
dataclass in ``schema.py`` instead of the shared
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
        component_types = self._load_json(cartridge_dir, "component_types.json")
        relation_types = self._load_json(cartridge_dir, "relation_types.json")
        validation_rules = self._load_json(cartridge_dir, "validation_rules.json")
        aliases = (
            {a["canonical"]: a["aliases"] for a in ontology.get("aliases", [])}
            if ontology
            else None
        )
        return CartridgeContext(
            cartridge_id=cartridge_id,
            ontology=ontology,
            component_types=component_types,
            relation_types=relation_types,
            validation_rules=validation_rules,
            aliases=aliases,
            notation_patterns=ontology.get("notation_patterns") if ontology else None,
            normalization_rules=ontology.get("normalization_rules") if ontology else None,
        )
