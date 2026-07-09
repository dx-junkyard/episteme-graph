"""経路A — 導出の隙間の反転（D2-2, 非LLM・決定論的）。

theory_component_graphs を横断し、AIが補った補完ステップ
（source_backing_status ∈ {inferred, review_required} または origin が fallback）を
構造キー（operation × review_reasons）でクラスタリングする。
**2 論文以上 or 2 導出以上**で反復するクラスタのみ候補化する。

この検出では graph_layer='debug' のノードも対象にする（debug 層こそが
AI 補完の置き場所）。負荷計算（D2-1）とは逆の関心であることに注意。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text as sa_text

from core.doubt.assumption_mining.schema import GapCluster

logger = logging.getLogger(__name__)

_GAP_BACKINGS = ("inferred", "review_required")
_GAP_ORIGINS = ("deterministic_fallback", "fallback", "inferred")


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


def _is_gap_node(node: dict) -> bool:
    backing = str(node.get("source_backing_status") or "").strip()
    origin = str(node.get("origin") or "").strip()
    if backing in _GAP_BACKINGS:
        return True
    if origin in _GAP_ORIGINS:
        return True
    return False


def _cluster_key(operation: str, review_reasons: list[str]) -> str:
    op = str(operation or "unknown_operation").strip().lower()
    reasons = "+".join(sorted({str(r).strip().lower() for r in review_reasons if str(r).strip()}))
    return f"gap|{op}|{reasons}"


def detect_gap_clusters(session, course_id: str = "") -> list[GapCluster]:
    """コーパス横断で補完ステップをクラスタリングし、候補条件を満たすものを返す。"""
    filters = ["TRUE"]
    params: dict[str, Any] = {}
    if course_id:
        filters.append("course_id = :course")
        params["course"] = course_id

    rows = session.execute(
        sa_text(f"""
            SELECT document_id, graph_json
            FROM theory_component_graphs
            WHERE {' AND '.join(filters)}
        """),
        params,
    ).fetchall()

    clusters: dict[str, GapCluster] = {}
    for row in rows:
        document_id = str(row[0] or "")
        payload = _graph_payload(row[1])
        for node in _as_list(payload.get("nodes")):
            if not isinstance(node, dict) or not _is_gap_node(node):
                continue
            operation = str(node.get("operation") or node.get("visual_label") or "").strip()
            review_reasons = [str(r) for r in _as_list(node.get("review_reasons"))]
            key = _cluster_key(operation, review_reasons)
            cluster = clusters.setdefault(
                key, GapCluster(cluster_key=key, operation=operation or "unknown_operation")
            )
            cluster.review_reasons.extend(review_reasons)
            node_id = str(node.get("component_id") or node.get("id") or "").strip()
            if node_id:
                cluster.node_ids.append(node_id)
            if document_id:
                cluster.document_ids.append(document_id)
            for did in _as_list(node.get("linked_derivation_ids")) + _as_list(node.get("supporting_derivation_ids")):
                derivation_id = str(did or "").strip()
                if derivation_id:
                    cluster.derivation_ids.append(derivation_id)
            for cid in _as_list(node.get("linked_claim_ids")):
                claim_id = str(cid or "").strip()
                if claim_id:
                    cluster.claim_ids.append(claim_id)
            for eid in _as_list(node.get("linked_equation_ids")):
                eq_id = str(eid or "").strip()
                if eq_id:
                    cluster.equation_ids.append(eq_id)
            for text_key in ("description", "review_reason", "theory_object", "label"):
                text = str(node.get(text_key) or "").strip()
                if text and text not in cluster.source_texts:
                    cluster.source_texts.append(text)

    candidates = [c for c in clusters.values() if c.is_candidate()]
    logger.info(
        "gap cluster detection: clusters=%d candidates=%d (course=%s)",
        len(clusters), len(candidates), course_id or "-",
    )
    return candidates


def register_candidates(session, clusters: list[GapCluster], course_id: str = "") -> int:
    """検出クラスタを assumption_nodes に candidate として冪等登録する。

    cluster_key の一意インデックスにより、confirmed / dismissed 済みの同一前提が
    再候補化されることはない（ON CONFLICT DO NOTHING）。
    """
    inserted = 0
    for cluster in clusters:
        result = session.execute(
            sa_text("""
                INSERT INTO assumption_nodes
                    (statement, origin, status, cluster_key, created_from,
                     course_id, document_ids)
                VALUES
                    (:statement, 'mined_gap', 'candidate', :ckey, CAST(:cfrom AS jsonb),
                     :course, CAST(:docs AS jsonb))
                ON CONFLICT (cluster_key) WHERE cluster_key <> '' DO NOTHING
                RETURNING id
            """),
            {
                "statement": cluster.placeholder_statement(),
                "ckey": cluster.cluster_key,
                "cfrom": json.dumps(cluster.created_from(), ensure_ascii=False),
                "course": course_id or "",
                "docs": json.dumps(sorted(set(cluster.document_ids)), ensure_ascii=False),
            },
        ).fetchone()
        if result is not None:
            inserted += 1
    session.commit()
    logger.info("gap candidates registered: %d new (course=%s)", inserted, course_id or "-")
    return inserted
