"""面①「内訳・同定」の組み立て（設計書 §3.1）。

resolve 済みの :class:`ElementRef` を受け、要素型ごとの内訳を **既存データの読み出しのみ**
（非LLM）で組み立てて JSON-serializable な dict を返す。W2/W4: 何も生成・確定しない。

各 build 関数は共通の外形を返す::

    {
        "element_type": str,
        "label": str,              # UI 見出し用の短いラベル
        "fields": dict,            # 要素型固有の内訳
        "notes": list[str],        # レビュー留意（例: apparatus 候補は review_required）
    }
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text as sa_text

from core.postgres import get_session
from core.deliberation import refs as refs_mod
from core.deliberation.schema import (
    ELEMENT_EQUATION,
    ELEMENT_FIGURE,
    ELEMENT_SHARED_PART,
    ELEMENT_THEORY_CLAIM,
    ELEMENT_THEORY_COMPONENT,
    ElementRef,
    ElementResolutionError,
)

# migration 041 で theory_components.component_type CHECK に追加された装置系語彙。
_APPARATUS_TYPES = ("apparatus", "instrument", "part")


def _json(value: Any, default: Any) -> Any:
    return value if isinstance(value, type(default)) else default


def _decompose_theory_claim(ref: ElementRef) -> dict[str, Any]:
    session = get_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT claim_type, text, normalized_text, concepts, equation,
                       support_status, evidence_text, review_status
                FROM theory_claims WHERE id = CAST(:id AS uuid) LIMIT 1
                """
            ),
            {"id": ref.element_id},
        ).fetchone()
    finally:
        session.close()
    if not row:
        raise ElementResolutionError(f"theory_claim not found: {ref.element_id}", kind="not_found")
    equation = _json(row[4], {})
    notes: list[str] = []
    if str(row[5] or "") != "source_backed":
        notes.append(f"support_status={row[5]}（source_backed でない）")
    return {
        "element_type": ELEMENT_THEORY_CLAIM,
        "label": (str(row[1] or "")[:80] or "claim"),
        "fields": {
            "claim_type": str(row[0] or ""),
            "text": str(row[1] or ""),
            "normalized_text": str(row[2] or ""),
            "concepts": _json(row[3], []),
            "equation": equation,
            "support_status": str(row[5] or ""),
            "evidence_text": str(row[6] or ""),
            "review_status": str(row[7] or ""),
            "has_equation": bool(equation),
        },
        "notes": notes,
    }


def _graph_node_for_component(document_id: str, component_id: str) -> dict[str, Any]:
    """theory_component_graphs.graph_json から当該 component の node を1つ探す（best-effort）。"""
    session = get_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT graph_json FROM theory_component_graphs
                WHERE document_id = :document_id
                ORDER BY updated_at DESC LIMIT 1
                """
            ),
            {"document_id": document_id},
        ).fetchone()
    finally:
        session.close()
    graph = _json(row[0], {}) if row else {}
    nodes = _json(graph.get("nodes"), []) if isinstance(graph, dict) else []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if component_id in (
            str(node.get("component_id") or ""),
            str(node.get("id") or ""),
            str(node.get("node_id") or ""),
        ):
            return {
                "graph_layer": node.get("graph_layer"),
                "component_type": node.get("component_type"),
                "stage": node.get("stage"),
                "label": node.get("label"),
                "description": node.get("description"),
                "source_backing_status": node.get("source_backing_status"),
                "review_status": node.get("review_status"),
                "review_reasons": _json(node.get("review_reasons"), []),
                "member_component_ids": _json(node.get("member_component_ids"), []),
                "parent_component_id": node.get("parent_component_id"),
            }
    return {}


def _decompose_theory_component(ref: ElementRef) -> dict[str, Any]:
    session = get_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT name, component_type, summary, status,
                       inputs, outputs, preconditions, constraints, dependencies
                FROM theory_components WHERE id = CAST(:id AS uuid) LIMIT 1
                """
            ),
            {"id": ref.element_id},
        ).fetchone()
    finally:
        session.close()
    if not row:
        raise ElementResolutionError(
            f"theory_component not found: {ref.element_id}", kind="not_found"
        )
    graph_node = _graph_node_for_component(ref.document_id or "", ref.element_id)
    notes: list[str] = []
    if str(row[3] or "") != "teacher_reviewed":
        notes.append(f"status={row[3]}（未承認）")
    if graph_node.get("review_reasons"):
        notes.append("グラフ上に review_reasons あり")
    return {
        "element_type": ELEMENT_THEORY_COMPONENT,
        "label": str(row[0] or "component"),
        "fields": {
            "name": str(row[0] or ""),
            "component_type": str(row[1] or ""),
            "summary": str(row[2] or ""),
            "status": str(row[3] or ""),
            "inputs": _json(row[4], []),
            "outputs": _json(row[5], []),
            "preconditions": _json(row[6], []),
            "constraints": _json(row[7], []),
            "dependencies": _json(row[8], []),
            "graph_node": graph_node,
        },
        "notes": notes,
    }


def _decompose_figure(ref: ElementRef) -> dict[str, Any]:
    session = get_session()
    try:
        fig = session.execute(
            sa_text(
                """
                SELECT figure_label, caption_text, page, status,
                       extraction_method, caption_block_id
                FROM document_figures WHERE id = CAST(:id AS uuid) LIMIT 1
                """
            ),
            {"id": ref.element_id},
        ).fetchone()
        # 装置・部品候補（apparatus_semantics → ComponentAssembly 経由の候補コンポーネント）。
        apparatus_rows = session.execute(
            sa_text(
                """
                SELECT id, name, component_type, status, summary
                FROM theory_components
                WHERE source_scope->>'document_id' = :document_id
                  AND component_type IN ('apparatus','instrument','part')
                ORDER BY created_at ASC
                """
            ),
            {"document_id": ref.document_id},
        ).fetchall()
    finally:
        session.close()
    if not fig:
        raise ElementResolutionError(f"figure not found: {ref.element_id}", kind="not_found")
    apparatus = [
        {
            "id": str(r[0]),
            "name": str(r[1] or ""),
            "component_type": str(r[2] or ""),
            "status": str(r[3] or ""),
            "summary": str(r[4] or ""),
        }
        for r in apparatus_rows
    ]
    notes: list[str] = []
    if apparatus:
        notes.append("装置・部品候補は review_required（確定は人間・L層）")
    if not fig[1]:
        notes.append("caption 対応なし（caption_block_id=NULL でも保持・P4）")
    return {
        "element_type": ELEMENT_FIGURE,
        "label": str(fig[0] or ref.provenance.get("figure_key") or "figure"),
        "fields": {
            "figure_label": str(fig[0] or ""),
            "figure_key": str(ref.provenance.get("figure_key") or ""),
            "caption_text": str(fig[1] or ""),
            "page": fig[2],
            "status": str(fig[3] or ""),
            "extraction_method": str(fig[4] or ""),
            "apparatus_candidates": apparatus,
        },
        "notes": notes,
    }


def _decompose_equation(ref: ElementRef) -> dict[str, Any]:
    # 独立テーブル無し（設計書 §2）。equation_semantics artifact から best-effort に読む。
    records = refs_mod.equation_records(ref.document_id or "")
    record: dict[str, Any] = {}
    for r in records:
        if str(r.get("equation_id") or "") == str(ref.element_id):
            record = r
            break
    src = _json(record.get("source_extraction"), {})
    rec = _json(record.get("reconstruction"), {})
    sem = _json(record.get("semantics"), {})
    latex = rec.get("latex") or src.get("latex")
    plain_text = rec.get("plain_text") or src.get("plain_text")
    return {
        "element_type": ELEMENT_EQUATION,
        "label": (str(plain_text or latex or ref.element_id)[:80]),
        "fields": {
            "equation_id": str(ref.element_id),
            "latex": latex,
            "plain_text": plain_text,
            "role_in_argument": sem.get("role_in_argument") or record.get("role_in_argument"),
            "input_equation_ids": _json(sem.get("input_equation_ids"), [])
            or _json(record.get("input_equation_ids"), []),
            "output_equation_ids": _json(sem.get("output_equation_ids"), [])
            or _json(record.get("output_equation_ids"), []),
            "needs_math_review": bool(src.get("needs_math_review")),
        },
        "notes": (
            ["equation は独立テーブル無し・artifact 由来の best-effort（設計書 §2）"]
            + (["数式は needs_math_review（表示用数式ではなく監査テキスト）"] if src.get("needs_math_review") else [])
        ),
    }


def _decompose_shared_part(ref: ElementRef) -> dict[str, Any]:
    session = get_session()
    try:
        entry = session.execute(
            sa_text(
                """
                SELECT domain_key, entry_type, status, name, summary
                FROM library_entries WHERE id = CAST(:id AS uuid) LIMIT 1
                """
            ),
            {"id": ref.element_id},
        ).fetchone()
        # 凍結版本文（最新の frozen version）。retrieval が読むのは凍結版のみ（L層方針）なので、
        # 凍結版があればそのスナップショット（content JSONB = エントリ全体）を優先表示する。
        version = session.execute(
            sa_text(
                """
                SELECT content, version_no FROM library_entry_versions
                WHERE entry_id = CAST(:id AS uuid)
                ORDER BY version_no DESC LIMIT 1
                """
            ),
            {"id": ref.element_id},
        ).fetchone()
    finally:
        session.close()
    if not entry:
        raise ElementResolutionError(f"shared_part not found: {ref.element_id}", kind="not_found")
    notes: list[str] = []
    if str(entry[2] or "") == "retired":
        notes.append("retired（retrieval には出ない・P4 で保持）")
    frozen_content = _json(version[0], {}) if version else {}
    if not version:
        notes.append("凍結版なし（draft のみ。パイプライン retrieval には未反映）")
    name = str(entry[3] or "")
    return {
        "element_type": ELEMENT_SHARED_PART,
        "label": name or f"shared_part:{ref.element_id[:8]}",
        "fields": {
            "domain_key": str(entry[0] or ""),
            "entry_type": str(entry[1] or ""),
            "status": str(entry[2] or ""),
            "name": name,
            "summary": str(entry[4] or ""),
            "frozen_content": frozen_content,
        },
        "notes": notes,
    }


_DISPATCH = {
    ELEMENT_THEORY_CLAIM: _decompose_theory_claim,
    ELEMENT_THEORY_COMPONENT: _decompose_theory_component,
    ELEMENT_FIGURE: _decompose_figure,
    ELEMENT_EQUATION: _decompose_equation,
    ELEMENT_SHARED_PART: _decompose_shared_part,
}


def build(ref: ElementRef) -> dict[str, Any]:
    """ElementRef の面①内訳を組み立てて返す。"""
    builder = _DISPATCH.get(ref.element_type)
    if builder is None:
        raise ElementResolutionError(
            f"unknown element_type: {ref.element_type!r}", kind="invalid"
        )
    return builder(ref)
