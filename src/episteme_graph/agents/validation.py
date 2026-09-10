"""Shared ``ValidationIssue`` dataclass for the PDF-analysis agents.

Before this consolidation the same four-field dataclass was copy-pasted into
every agent's ``schema.py``. The canonical definition now lives here and each
agent's ``schema.py`` re-exports it, so
``from episteme_graph.agents.<agent>.schema import ValidationIssue`` keeps
working unchanged (same precedent as
:mod:`episteme_graph.agents.cartridge_context`).

Agents whose issue shape genuinely differs keep a local definition and are
listed here so the divergence stays visible:

* ``component_assembly`` — subclasses this dataclass to add
  ``target_type`` / ``target_id``.
* ``document_structure`` — uses ``block_id`` instead of ``field``.
* ``document_unit_boundary`` — uses ``unit_id`` + ``block_id``.
* ``discuss_opening`` / ``landscape_placement`` — add a ``to_dict()`` method.

``severity`` is ``"error"`` or ``"warning"``; only ``"error"`` blocks a result
(see :func:`episteme_graph.agents.llm_step.error_issues`).
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ValidationIssue"]


@dataclass
class ValidationIssue:
    rule_id: str
    severity: str  # "error" | "warning"
    message: str
    field: str | None = None
