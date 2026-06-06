"""Canonical component responsibility vocabulary."""
from __future__ import annotations

CANONICAL_RESPONSIBILITY_TYPES = {
    "definition",
    "model",
    "observation_model",
    "observable_basis",
    "equation_system",
    "derivation",
    "constraint",
    "application",
    "limitation",
}

RESPONSIBILITY_ALIASES = {
    "observation_design": "observation_model",
    "algebraic_elimination": "derivation",
    "final_constraint": "constraint",
    "forecast_application": "application",
    "caveat": "limitation",
}


def canonical_responsibility_type(value: object) -> str:
    raw = str(value or "").strip()
    return RESPONSIBILITY_ALIASES.get(raw, raw)


def canonical_responsibility_types(values) -> set[str]:
    return {
        canonical
        for value in values or []
        if (canonical := canonical_responsibility_type(value)) in CANONICAL_RESPONSIBILITY_TYPES
    }
