"""Concept and notation normalization for theory-component graph matching."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from core.cartridges import load_cartridge


_GREEK = {
    "Λ": "lambda",
    "\\Lambda": "lambda",
    "λ": "lambda",
    "\\lambda": "lambda",
    "Δ": "delta",
    "\\Delta": "delta",
    "δ": "delta",
    "\\delta": "delta",
    "μ": "mu",
    "\\mu": "mu",
    "π": "pi",
    "\\pi": "pi",
}


@dataclass(frozen=True)
class NormalizedConcept:
    raw: str
    normalized: str
    canonical: str
    concept_type: str = "Concept"
    normalization_source: str = "string_normalized"

    def as_dict(self) -> dict[str, str]:
        return {
            "raw": self.raw,
            "normalized": self.normalized,
            "canonical": self.canonical,
            "concept_type": self.concept_type,
            "normalization_source": self.normalization_source,
        }


def normalize_key(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\$+", "", text)
    text = text.replace("{", "").replace("}", "")
    text = re.sub(r"\\mathrm|\\text|\\mathcal|\\mathbb", "", text)
    for src, dst in _GREEK.items():
        text = text.replace(src, dst)
    text = text.replace("→", " to ").replace("\\to", " to ")
    text = re.sub(r"[_^]+", " ", text)
    text = re.sub(r"[^A-Za-z0-9]+", "_", text.lower()).strip("_")
    return re.sub(r"_+", "_", text)


def _alias_index(cartridge_id: str | None = None) -> dict[str, tuple[str, str]]:
    cartridge = load_cartridge(cartridge_id)
    index: dict[str, tuple[str, str]] = {}
    for item in cartridge.ontology.aliases:
        canonical = str(item.get("canonical") or "").strip()
        if not canonical:
            continue
        concept_type = str(item.get("concept_type") or "Concept")
        keys = [canonical] + [str(alias) for alias in item.get("aliases", []) if str(alias).strip()]
        for key in keys:
            index[normalize_key(key)] = (canonical, concept_type)
    for pattern in cartridge.ontology.notation_patterns:
        concept_type = str(pattern.get("concept_type") or "Concept")
        raw_pattern = str(pattern.get("pattern") or "")
        if raw_pattern:
            index.setdefault(normalize_key(raw_pattern), (raw_pattern, concept_type))
    return index


def normalize_concept(value: Any, concept_type: str = "Concept", cartridge_id: str | None = None) -> NormalizedConcept:
    raw = str(value or "").strip()
    key = normalize_key(raw)
    aliases = _alias_index(cartridge_id)
    if key in aliases:
        canonical, alias_type = aliases[key]
        return NormalizedConcept(raw=raw, normalized=normalize_key(canonical), canonical=canonical, concept_type=alias_type, normalization_source="ontology_alias")
    return NormalizedConcept(raw=raw, normalized=key, canonical=raw, concept_type=concept_type or "Concept")


def normalize_concepts(items: list[dict], cartridge_id: str | None = None) -> list[dict]:
    normalized = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        concept = normalize_concept(item.get("name") or item.get("label") or "", item.get("concept_type") or "Concept", cartridge_id)
        if not concept.normalized:
            continue
        merged = dict(item)
        merged.update(concept.as_dict())
        merged["name"] = concept.canonical
        normalized.append(merged)
    return normalized
