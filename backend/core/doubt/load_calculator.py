"""負荷度（load_score）のバッチ計算（D2-1, 非LLM・決定論的）。

「その前提が偽なら下流の何が崩れるか」を理論操作グラフ上の到達可能性で計算し、
epistemic_ledger.load_score に記帳する。

原則:
  - 生数値は DB のみに保存する。API/UI は段階ラベル（低 / 中 / 高 / 最高位）に
    変換して返す（core/doubt/schema.py load_level_for_score）。
  - graph_layer='debug' / inferred ノードは負荷計算の根拠にしない（dependency.py）。
  - 閉路があってもハングしない（visited set BFS）。
  - 対象ごとの load = その対象が直接支えるノード集合 + その下流到達集合のサイズ。

トリガー: 解析完了時（orchestrator の D層フック）+ 手動
（POST /api/admin/doubt/courses/{course_id}/load/recompute）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text as sa_text

from core.doubt.dependency import DependencyGraph, build_dependency_graph, seed_nodes_for_target
from core.postgres import get_session

logger = logging.getLogger(__name__)


def _load_for_seeds(graph: DependencyGraph, seeds: set[str]) -> float | None:
    """seed ノード集合の負荷 = |seeds ∪ downstream(seeds)|。seed 無しなら None。"""
    if not seeds:
        return None
    affected = set(seeds) | graph.downstream_of_set(set(seeds))
    return float(len(affected))


def _assumption_seed_nodes(graph: DependencyGraph, created_from: Any) -> set[str]:
    """assumption の created_from（出所参照）からグラフ上の seed ノードを引く。"""
    if isinstance(created_from, str):
        try:
            created_from = json.loads(created_from)
        except Exception:
            created_from = {}
    if not isinstance(created_from, dict):
        return set()
    seeds: set[str] = set()
    for key in ("linked_node_ids", "component_ids", "node_ids"):
        values = created_from.get(key)
        if isinstance(values, list):
            for nid in values:
                node_id = str(nid or "").strip()
                if node_id in graph.node_ids:
                    seeds.add(node_id)
    for key in ("claim_ids", "linked_claim_ids"):
        values = created_from.get(key)
        if isinstance(values, list):
            for cid in values:
                seeds |= graph.claim_refs.get(str(cid or "").strip(), set())
    for key in ("equation_ids", "linked_equation_ids"):
        values = created_from.get(key)
        if isinstance(values, list):
            for eid in values:
                seeds |= graph.equation_refs.get(str(eid or "").strip(), set())
    return seeds


def recompute_load_scores(course_id: str = "", document_id: str = "") -> dict:
    """指定範囲の台帳行の load_score を再計算する（冪等）。"""
    session = get_session()
    updated = 0
    try:
        graph = build_dependency_graph(session, course_id=course_id, document_id=document_id)

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

        rows = session.execute(
            sa_text(f"""
                SELECT id::text, target_id, target_type
                FROM epistemic_ledger
                WHERE {' AND '.join(filters)}
            """),
            params,
        ).fetchall()

        assumption_ids = [str(r[1]) for r in rows if str(r[2]) == "assumption"]
        assumption_sources: dict[str, Any] = {}
        if assumption_ids:
            assumption_rows = session.execute(
                sa_text("""
                    SELECT id::text, created_from
                    FROM assumption_nodes
                    WHERE id::text = ANY(:ids)
                """),
                {"ids": assumption_ids},
            ).fetchall()
            assumption_sources = {str(r[0]): r[1] for r in assumption_rows}

        for row in rows:
            ledger_id, target_id, target_type = str(row[0]), str(row[1]), str(row[2])
            if target_type == "assumption":
                seeds = _assumption_seed_nodes(graph, assumption_sources.get(target_id))
            else:
                seeds = seed_nodes_for_target(graph, target_type, target_id)
            score = _load_for_seeds(graph, seeds)
            session.execute(
                sa_text("""
                    UPDATE epistemic_ledger
                    SET load_score = :score,
                        load_computed_at = now(),
                        updated_at = now()
                    WHERE id = CAST(:lid AS uuid)
                """),
                {"score": score, "lid": ledger_id},
            )
            updated += 1
        session.commit()
    except Exception:
        session.rollback()
        logger.warning(
            "load score recompute failed (course=%s document=%s)",
            course_id or "-", document_id or "-", exc_info=True,
        )
        return {"updated": 0, "failed": True}
    finally:
        session.close()

    logger.info(
        "load score recompute done: updated=%d (course=%s document=%s)",
        updated, course_id or "-", document_id or "-",
    )
    return {"updated": updated}


def load_percentiles(session, course_id: str) -> tuple[float, float, float]:
    """course 内 load_score の p50 / p90 / p99（段階ラベル変換の閾値）。"""
    row = session.execute(
        sa_text("""
            SELECT
                COALESCE(percentile_cont(0.5) WITHIN GROUP (ORDER BY load_score), 0),
                COALESCE(percentile_cont(0.9) WITHIN GROUP (ORDER BY load_score), 0),
                COALESCE(percentile_cont(0.99) WITHIN GROUP (ORDER BY load_score), 0)
            FROM epistemic_ledger
            WHERE course_id = :course AND load_score IS NOT NULL
        """),
        {"course": course_id},
    ).fetchone()
    if not row:
        return 0.0, 0.0, 0.0
    return float(row[0] or 0.0), float(row[1] or 0.0), float(row[2] or 0.0)
