"""Shared ``CartridgeContext`` dataclass.

This is the "standard" cartridge context shape used by the majority of the
PDF-analysis agents (see docs/architecture/consolidation_survey_2026-07.md,
Tier 2 proposal 9: "cartridge 読み込みの統合"). Before this consolidation the
same dataclass was copy-pasted verbatim into ~10 agents' ``schema.py`` files.

Two agents genuinely need extra required fields beyond this shape
(``component_assembly`` needs ``component_types``/``relation_types``,
``component_graph`` needs ``relation_types``) and keep their own
``CartridgeContext`` dataclass defined locally in their ``schema.py`` instead
of importing this one — see those files for why unifying them here would
silently reorder constructor positional arguments used by existing call
sites/tests.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CartridgeContext:
    cartridge_id: str
    ontology: dict
    validation_rules: dict
    aliases: dict | None = None
    notation_patterns: list | None = None
    normalization_rules: list | None = None
    extraction_hints: dict | list | None = None
