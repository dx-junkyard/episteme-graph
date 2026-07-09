"""item オーサリングの LLM 入力整形（claim の concepts / equation / scope）。

オーサリング時、LLM は答えキー（claim 本文含む）を見てよい（LLM は出題者）。
learner への伏せ（§3.2）は item_builder.public_item_view で別途行う。
"""

from __future__ import annotations

import json
from typing import Any

from core.reconstruction.schema import subject_driver_concepts


def build_user_content(claim: dict[str, Any]) -> str:
    """claim を LLM 入力（user ロール1本）に整形する。"""
    equation = claim.get("equation") if isinstance(claim.get("equation"), dict) else {}
    payload = {
        "claim_type": str(claim.get("claim_type") or ""),
        "text": str(claim.get("text") or ""),
        "normalized_text": str(claim.get("normalized_text") or ""),
        "subject_driver_concepts": subject_driver_concepts(claim),
        "all_concepts": _concept_names(claim.get("concepts")),
        "equation": {
            "label": str(equation.get("label") or ""),
            "latex": str(equation.get("latex") or ""),
            "defined_symbols": [str(s) for s in (equation.get("defined_symbols") or [])],
            "relation_type": str(equation.get("relation_type") or ""),
        },
        "source_scope": _scope_context(claim.get("source_scope")),
        "evidence_text": str(claim.get("evidence_text") or "")[:600],
    }
    return "答えキー（claim）:\n" + json.dumps(payload, ensure_ascii=False, indent=2)


def _concept_names(concepts: Any) -> list[str]:
    out: list[str] = []
    if isinstance(concepts, list):
        for c in concepts:
            if isinstance(c, dict):
                name = str(c.get("name") or "").strip()
            else:
                name = str(c or "").strip()
            if name and name not in out:
                out.append(name)
    return out


def _scope_context(scope: Any) -> dict:
    if not isinstance(scope, dict):
        return {}
    return {
        "section_title": str(scope.get("section_title") or ""),
        "level": str(scope.get("level") or ""),
    }
