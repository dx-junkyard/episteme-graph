"""IK-0755: the refiner writes no machine text into human-facing fields.

Split provenance (rule, parent, operation family, steps, equations) lives in the
structured ``derivation_refs`` field; summary / teaching_takeaway / reason — which
reach ``local_text`` (``label: summary``) of the two-layer explanation — never carry
issue numbers (``(#324)``) or counts (``covers N equation step(s)``).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from episteme_graph.agents.component_assembly import component_refiner

_MACHINE = re.compile(r"\(#\d|#\d{3}|step\(s\)|equation\(s\)|covers \{")


def _non_docstring_strings(tree: ast.AST):
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            yield node


def test_refiner_string_literals_carry_no_issue_numbers_or_step_counts():
    source = Path(component_refiner.__file__).read_text(encoding="utf-8")
    offenders = [
        (node.lineno, node.value)
        for node in _non_docstring_strings(ast.parse(source))
        if _MACHINE.search(node.value)
    ]
    assert offenders == []


def test_old_machine_sentence_builders_are_gone():
    for name in (
        "_theory_unit_summary", "_responsibility_summary", "_suggested_summary",
        "_suggested_takeaway", "_responsibility_takeaway",
    ):
        assert not hasattr(component_refiner, name), name
