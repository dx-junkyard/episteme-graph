"""Load cartridge context for ComponentGraphAgent."""
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

    @staticmethod
    def _load_json(directory: str, filename: str) -> dict:
        path = os.path.join(directory, filename)
        if not os.path.exists(path):
            return {}
        with open(path, encoding="utf-8") as f:
            return json.load(f)
