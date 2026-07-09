"""検証スコープ候補 — 対象文脈の構築（非LLM, D1-4）。

台帳対象（claim / equation / component / assumption）ごとに、A層成果物から
出典テキストブロックを集める。evidence_quote の逐語検証の母集団になるため、
LLM に渡していないテキストを候補の根拠にはできない。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text as sa_text

from core.doubt.scope_candidates.schema import ScopeTargetContext, SourceBlock

logger = logging.getLogger(__name__)

_MAX_BLOCKS = 8
_MAX_BLOCK_CHARS = 1500


def _clip(text: str) -> str:
    text = (text or "").strip()
    if len(text) <= _MAX_BLOCK_CHARS:
        return text
    return text[:_MAX_BLOCK_CHARS]


def _equation_dict(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def build_target_context(session, target_type: str, target_id: str) -> ScopeTargetContext:
    """対象 1 件の出典文脈を組み立てる。出典が無い場合 source_blocks は空。"""
    target_type = str(target_type or "").strip()
    target_id = str(target_id or "").strip()
    context = ScopeTargetContext(target_id=target_id, target_type=target_type)
    blocks: list[SourceBlock] = []

    try:
        if target_type == "claim":
            rows = session.execute(
                sa_text("""
                    SELECT text, normalized_text, evidence_text
                    FROM theory_claims WHERE id::text = :tid
                """),
                {"tid": target_id},
            ).fetchall()
            for row in rows:
                context.target_label = _clip(str(row[1] or row[0] or ""))[:120]
                if str(row[0] or "").strip():
                    blocks.append(SourceBlock("S1", "claim 本文", _clip(str(row[0]))))
                if str(row[2] or "").strip():
                    blocks.append(SourceBlock("S2", "evidence（原文）", _clip(str(row[2]))))

        elif target_type == "equation":
            rows = session.execute(
                sa_text("""
                    SELECT text, evidence_text, equation
                    FROM theory_claims
                    WHERE (equation->>'equation_id') = :tid
                    ORDER BY created_at ASC
                """),
                {"tid": target_id},
            ).fetchall()
            for idx, row in enumerate(rows[:_MAX_BLOCKS // 2]):
                equation = _equation_dict(row[2])
                if not context.target_label:
                    context.target_label = str(equation.get("label") or target_id)
                if str(row[0] or "").strip():
                    blocks.append(SourceBlock(f"S{len(blocks)+1}", f"式 {target_id} に触れる claim", _clip(str(row[0]))))
                if str(row[1] or "").strip():
                    blocks.append(SourceBlock(f"S{len(blocks)+1}", "evidence（原文）", _clip(str(row[1]))))

        elif target_type == "component":
            rows = session.execute(
                sa_text("""
                    SELECT name, summary, preconditions, constraints
                    FROM theory_components WHERE id::text = :tid
                """),
                {"tid": target_id},
            ).fetchall()
            for row in rows:
                context.target_label = str(row[0] or "")
                if str(row[1] or "").strip():
                    blocks.append(SourceBlock("S1", "component 要約", _clip(str(row[1]))))
                for label, value in (("成立条件（preconditions）", row[2]), ("制約（constraints）", row[3])):
                    items = value if isinstance(value, list) else []
                    texts = []
                    for item in items:
                        if isinstance(item, dict):
                            entry = str(item.get("label") or "").strip()
                            desc = str(item.get("description") or "").strip()
                            if entry:
                                texts.append(f"{entry}: {desc}" if desc else entry)
                            for ref in item.get("source_refs") or []:
                                if isinstance(ref, dict) and str(ref.get("quote") or "").strip():
                                    texts.append(str(ref["quote"]).strip())
                    if texts:
                        blocks.append(SourceBlock(f"S{len(blocks)+1}", label, _clip("\n".join(texts))))

        elif target_type == "assumption":
            rows = session.execute(
                sa_text("""
                    SELECT statement, evidence_quote, reason
                    FROM assumption_nodes WHERE id::text = :tid
                """),
                {"tid": target_id},
            ).fetchall()
            for row in rows:
                context.target_label = _clip(str(row[0] or ""))[:120]
                if str(row[0] or "").strip():
                    blocks.append(SourceBlock("S1", "前提文", _clip(str(row[0]))))
                if str(row[1] or "").strip():
                    blocks.append(SourceBlock("S2", "検出時の逐語 evidence", _clip(str(row[1]))))
    except Exception:
        logger.warning(
            "scope candidate context build failed for %s/%s", target_type, target_id, exc_info=True
        )

    context.source_blocks = blocks[:_MAX_BLOCKS]
    return context
