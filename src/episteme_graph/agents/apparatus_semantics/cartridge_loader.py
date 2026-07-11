"""Load cartridge context for ApparatusSemanticsAgent.

Same CartridgeContext interface as every other agent's CartridgeLoader
(equation_semantics, claim_qualification, ...). The agent must work with
``cartridge_id=None`` (no cartridge on disk) — see agent.py::_load_cartridge.
"""
from __future__ import annotations

import json
import os

from episteme_graph.agents.cartridge_paths import resolve_cartridge_base_dir

from .schema import CartridgeContext

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_CARTRIDGE_BASE = os.path.abspath(
    os.path.join(_HERE, "..", "..", "..", "..", "backend", "cartridges")
)


class CartridgeLoader:
    def __init__(self, cartridge_base_dir: str | None = None) -> None:
        self._base_dir = cartridge_base_dir or resolve_cartridge_base_dir(_DEFAULT_CARTRIDGE_BASE)

    def load(self, cartridge_id: str) -> CartridgeContext:
        cartridge_dir = os.path.join(self._base_dir, cartridge_id)
        if not os.path.isdir(cartridge_dir):
            raise FileNotFoundError(
                f"Cartridge '{cartridge_id}' not found at {cartridge_dir}"
            )
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

    def _load_json(self, directory: str, filename: str) -> dict:
        path = os.path.join(directory, filename)
        if not os.path.exists(path):
            return {}
        with open(path, encoding="utf-8") as f:
            return json.load(f)
