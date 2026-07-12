"""Shared ``CartridgeLoader``: reads ``backend/cartridges/<cartridge_id>/*.json``
into a :class:`~episteme_graph.agents.cartridge_context.CartridgeContext`.

This is the "standard" loader used by most PDF-analysis agents (see
docs/architecture/consolidation_survey_2026-07.md, Tier 2 proposal 9:
"cartridge 読み込みの統合"). Before this consolidation the same ~50 lines were
copy-pasted verbatim into ~12 agents' ``cartridge_loader.py`` files.

Agents whose loader needs to read extra cartridge files beyond
``ontology.json``/``validation_rules.json`` (``apparatus_semantics`` folds
``component_types.json`` into ``extraction_hints``; ``component_assembly`` and
``component_graph`` load ``component_types.json``/``relation_types.json`` into
their own, differently-shaped ``CartridgeContext``) subclass this loader and
override :meth:`load`, reusing :meth:`_cartridge_dir` and :meth:`_load_json`.
"""
from __future__ import annotations

import json
import os

from episteme_graph.agents.cartridge_paths import resolve_cartridge_base_dir

from .cartridge_context import CartridgeContext

_HERE = os.path.dirname(os.path.abspath(__file__))
# src/episteme_graph/agents/ の3階層上がプロジェクトルート
_DEFAULT_CARTRIDGE_BASE = os.path.abspath(
    os.path.join(_HERE, "..", "..", "..", "backend", "cartridges")
)


class CartridgeLoader:
    """Loads ``ontology.json`` + ``validation_rules.json`` into a CartridgeContext."""

    def __init__(self, cartridge_base_dir: str | None = None) -> None:
        self._base_dir = cartridge_base_dir or resolve_cartridge_base_dir(_DEFAULT_CARTRIDGE_BASE)

    def load(self, cartridge_id: str) -> CartridgeContext:
        cartridge_dir = self._cartridge_dir(cartridge_id)
        ontology = self._load_json(cartridge_dir, "ontology.json")
        validation_rules = self._load_json(cartridge_dir, "validation_rules.json")
        aliases = (
            {a["canonical"]: a["aliases"] for a in ontology.get("aliases", [])}
            if ontology
            else None
        )
        return CartridgeContext(
            cartridge_id=cartridge_id,
            ontology=ontology,
            validation_rules=validation_rules,
            aliases=aliases,
            notation_patterns=ontology.get("notation_patterns") if ontology else None,
            normalization_rules=ontology.get("normalization_rules") if ontology else None,
            extraction_hints=ontology.get("extraction_hints") if ontology else None,
        )

    def _cartridge_dir(self, cartridge_id: str) -> str:
        cartridge_dir = os.path.join(self._base_dir, cartridge_id)
        if not os.path.isdir(cartridge_dir):
            raise FileNotFoundError(
                f"Cartridge '{cartridge_id}' not found at {cartridge_dir}"
            )
        return cartridge_dir

    @staticmethod
    def _load_json(directory: str, filename: str) -> dict:
        path = os.path.join(directory, filename)
        if not os.path.exists(path):
            return {}
        with open(path, encoding="utf-8") as f:
            return json.load(f)
