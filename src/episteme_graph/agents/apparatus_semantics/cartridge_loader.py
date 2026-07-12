"""Load cartridge context for ApparatusSemanticsAgent.

Same CartridgeContext interface as every other agent's CartridgeLoader
(equation_semantics, claim_qualification, ...). The agent must work with
``cartridge_id=None`` (no cartridge on disk) — see agent.py::_load_cartridge.

Subclasses the shared ``CartridgeLoader`` (see
docs/architecture/consolidation_survey_2026-07.md, Tier 2 proposal 9:
"cartridge 読み込みの統合"), reusing its base-dir resolution and JSON loading.
The one piece of agent-specific behavior — folding ``component_types.json``
into ``extraction_hints`` — isn't shared with any other agent's loader, so it
stays here as an override of ``load()``.
"""
from __future__ import annotations

from episteme_graph.agents.cartridge_loader import CartridgeLoader as _BaseCartridgeLoader

from .schema import CartridgeContext


class CartridgeLoader(_BaseCartridgeLoader):
    def load(self, cartridge_id: str) -> CartridgeContext:
        cartridge_dir = self._cartridge_dir(cartridge_id)
        ontology = self._load_json(cartridge_dir, "ontology.json")
        validation_rules = self._load_json(cartridge_dir, "validation_rules.json")
        component_types = self._load_json(cartridge_dir, "component_types.json")
        aliases = (
            {a["canonical"]: a["aliases"] for a in ontology.get("aliases", [])}
            if ontology
            else None
        )

        # Fold component_types.json into extraction_hints so the prompt can
        # offer the cartridge's allowed component-type vocabulary (e.g. an
        # 'apparatus' / 'instrument' / 'part' entry, per design doc §5-5) as a
        # *hint* only — never hardcoded in the agent itself (design principle 5).
        extraction_hints = ontology.get("extraction_hints") if ontology else None
        if component_types:
            merged_hints: dict = dict(extraction_hints) if isinstance(extraction_hints, dict) else {}
            merged_hints["component_types"] = component_types.get("component_types", component_types)
            extraction_hints = merged_hints

        return CartridgeContext(
            cartridge_id=cartridge_id,
            ontology=ontology,
            validation_rules=validation_rules,
            aliases=aliases,
            notation_patterns=ontology.get("notation_patterns") if ontology else None,
            normalization_rules=ontology.get("normalization_rules") if ontology else None,
            extraction_hints=extraction_hints,
        )
