"""観測系 claim の多段同定（SL-2, 非LLM・決定論的・読み時）。

正本: docs/features/stakes_ledger_design.md §4.1。単一の抽出手段に賭けず、
3段の縮退で観測系 claim を同定する。どの経路で同定したかを ``identified_via``
として保持する（P4: 情報を落とさない）。

  A. DSL 述語（第一）: ``graph_json.dsl.edges[]`` の ``predicate == "MEASURES"``
     → ``evidence_refs.claim_ids``。DSL node id は component 層と別体系のため
     claim 経由でのみ渡る。
  B. theory stage（第二）: main 層 node の stage を
     ``element_vocab.theory_stage_key(label or visual_label)`` で復元し、
     ``{diagnostic_application, observation_model, observable_construction}``
     に属する node の ``linked_claim_ids``。
  C. claim 型（第三の縮退）: ``theory_claims.claim_type ∈
     {observable_definition, diagnostic_claim}``。

``dsl`` ブロックが空の旧 run では A が空になるだけで B/C が生きる（fail-soft）。
同一 claim が複数経路で見つかった場合は最初（優先度の高い）経路の
``identified_via`` を保持する（A > B > C）。

FastAPI 非 import・LLM 非 import（core/doubt/dependency.py と同じ非LLM層）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text as sa_text

from core.element_vocab import theory_stage_key

logger = logging.getLogger(__name__)

# B段（theory stage）で観測系とみなす stage キー（正本: schema.py の THEORY_STAGES）
_OBSERVATION_STAGES = (
    "diagnostic_application",
    "observation_model",
    "observable_construction",
)

# C段（claim 型）で観測系とみなす claim_type
_OBSERVATION_CLAIM_TYPES = ("observable_definition", "diagnostic_claim")

_MEASURES_PREDICATE = "MEASURES"

_IDENTIFIED_VIA_DSL = "dsl_measures"
_IDENTIFIED_VIA_STAGE = "theory_stage"
_IDENTIFIED_VIA_CLAIM_TYPE = "claim_type"

_LABEL_MAX_CHARS = 160


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _graph_payload(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _fetch_graph_rows(session, course_id: str, document_id: str) -> list[tuple[str, dict]]:
    """theory_component_graphs から (document_id, graph_json) を course/document 範囲で読む。

    dependency.build_dependency_graph と同じフィルタ規約（filters が空なら TRUE）。
    このモジュールは dsl ブロック・stage 判定に生の nodes/edges が必要なため、
    DependencyGraph（除外済み・加工済み）を経由せず独自に読む。
    """
    filters = []
    params: dict[str, Any] = {}
    if course_id:
        filters.append("course_id = :course")
        params["course"] = course_id
    if document_id:
        filters.append("document_id = :doc")
        params["doc"] = document_id
    if not filters:
        filters.append("TRUE")
    try:
        rows = session.execute(
            sa_text(f"""
                SELECT document_id, graph_json
                FROM theory_component_graphs
                WHERE {' AND '.join(filters)}
            """),
            params,
        ).fetchall()
    except Exception:
        logger.warning("observation target graph lookup failed", exc_info=True)
        return []
    return [(str(r[0] or ""), _graph_payload(r[1])) for r in rows]


def _claim_ids_from_dsl(payload: dict) -> list[str]:
    """A段: dsl.edges[] の predicate=='MEASURES' → evidence_refs.claim_ids。"""
    dsl = payload.get("dsl") if isinstance(payload.get("dsl"), dict) else {}
    edges = [e for e in _as_list(dsl.get("edges")) if isinstance(e, dict)]
    claim_ids: list[str] = []
    for edge in edges:
        predicate = str(edge.get("predicate") or edge.get("edge_type") or "").strip().upper()
        if predicate != _MEASURES_PREDICATE:
            continue
        refs = edge.get("evidence_refs") if isinstance(edge.get("evidence_refs"), dict) else {}
        for cid in _as_list(refs.get("claim_ids")):
            cid = str(cid or "").strip()
            if cid:
                claim_ids.append(cid)
    return claim_ids


def _claim_ids_from_stage(payload: dict) -> list[str]:
    """B段: main 層 node の stage が観測系 → linked_claim_ids。"""
    nodes = [n for n in _as_list(payload.get("nodes")) if isinstance(n, dict)]
    claim_ids: list[str] = []
    for node in nodes:
        layer = str(node.get("graph_layer") or "main")
        if layer != "main":
            continue
        key = theory_stage_key(node.get("label")) or theory_stage_key(node.get("visual_label"))
        if key not in _OBSERVATION_STAGES:
            continue
        for cid in _as_list(node.get("linked_claim_ids")):
            cid = str(cid or "").strip()
            if cid:
                claim_ids.append(cid)
    return claim_ids


def _resolve_document_ids(document_id: str, graph_rows: list[tuple[str, dict]]) -> list[str]:
    """C段の theory_claims 検索範囲（document_id の集合）を決める。

    明示 document_id は常に含める（グラフが無い document でも C段は生きる,
    fail-soft）。course_id のみのときは、その course の theory_component_graphs
    行から観測された document_id を使う（既知の限界: グラフを一件も持たない
    course では C段の対象文書を特定できない）。
    """
    doc_ids: list[str] = []
    if document_id:
        doc_ids.append(document_id)
    for doc_id, _payload in graph_rows:
        if doc_id and doc_id not in doc_ids:
            doc_ids.append(doc_id)
    return doc_ids


def _claim_labels(session, claim_ids: set[str]) -> dict[str, str]:
    if not claim_ids:
        return {}
    try:
        rows = session.execute(
            sa_text("""
                SELECT id::text, COALESCE(NULLIF(normalized_text, ''), text)
                FROM theory_claims
                WHERE id::text = ANY(:ids)
            """),
            {"ids": list(claim_ids)},
        ).fetchall()
    except Exception:
        logger.debug("observation target claim label lookup failed", exc_info=True)
        return {}
    return {str(r[0]): str(r[1] or "")[:_LABEL_MAX_CHARS] for r in rows}


def observation_claim_targets(
    session,
    *,
    course_id: str = "",
    document_id: str = "",
) -> list[dict]:
    """観測系 claim の一覧を3段の縮退で同定する（非LLM・読み時・決定論的）。

    Returns:
        ``[{"claim_id", "label", "identified_via"}]``。claim_id 昇順・重複なし。
        identified_via の優先順位は dsl_measures > theory_stage > claim_type
        （同一 claim が複数経路で見つかったら最初の経路を保持する）。
    """
    course_id = str(course_id or "").strip()
    document_id = str(document_id or "").strip()

    graph_rows = _fetch_graph_rows(session, course_id, document_id)

    identified_via: dict[str, str] = {}

    # --- A: DSL 述語 MEASURES（第一） -------------------------------------
    for _doc_id, payload in graph_rows:
        for cid in _claim_ids_from_dsl(payload):
            identified_via.setdefault(cid, _IDENTIFIED_VIA_DSL)

    # --- B: theory stage（第二） -------------------------------------------
    for _doc_id, payload in graph_rows:
        for cid in _claim_ids_from_stage(payload):
            identified_via.setdefault(cid, _IDENTIFIED_VIA_STAGE)

    # --- C: claim 型（第三の縮退） -------------------------------------------
    doc_ids = _resolve_document_ids(document_id, graph_rows)
    if doc_ids:
        try:
            rows = session.execute(
                sa_text("""
                    SELECT id::text
                    FROM theory_claims
                    WHERE document_id = ANY(:docs)
                      AND claim_type = ANY(:ctypes)
                """),
                {"docs": doc_ids, "ctypes": list(_OBSERVATION_CLAIM_TYPES)},
            ).fetchall()
            for row in rows:
                cid = str(row[0])
                identified_via.setdefault(cid, _IDENTIFIED_VIA_CLAIM_TYPE)
        except Exception:
            logger.warning("observation target claim_type lookup failed", exc_info=True)

    labels = _claim_labels(session, set(identified_via.keys()))
    items = [
        {"claim_id": cid, "label": labels.get(cid, cid), "identified_via": via}
        for cid, via in identified_via.items()
    ]
    items.sort(key=lambda item: item["claim_id"])
    return items
