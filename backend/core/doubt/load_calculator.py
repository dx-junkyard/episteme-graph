"""負荷度（load_score）のバッチ計算（D2-1, 非LLM・決定論的）。

「その前提が偽なら下流の何が崩れるか」を理論操作グラフ上の到達可能性で計算し、
epistemic_ledger.load_score に記帳する。

原則:
  - 生数値は DB のみに保存する。API/UI は段階ラベル（低 / 中 / 高 / 最高位）に
    変換して返す（core/doubt/schema.py load_level_for_score）。
  - graph_layer='debug' / inferred ノードは負荷計算の根拠にしない（dependency.py）。
  - 閉路があってもハングしない（visited set BFS）。
  - 対象ごとの load = その対象が直接支えるノード集合 + その下流到達集合のサイズ。
  - 論文の主張を出現順に並べた claim チェーンの辺は依存に数えない（IK-1030・2026-10-27
    オーナー判断。``build_dependency_graph(exclude_claim_sequence_edges=True)`` — 判定の正本は
    一覧の母集合 IK-0933 と同じ ``claim_sequence_node_ids``）。

トリガー: 解析完了時（orchestrator の D層フック）+ 手動
（POST /api/admin/doubt/courses/{course_id}/load/recompute）。どちらも論文単位で計算する
（IK-1001 — 範囲は行の論文 1 本。段の閾値だけを読み時にコースの母集団で取る）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text as sa_text

from core.doubt.dependency import DependencyGraph, build_dependency_graph, seed_nodes_for_target
from core.doubt.scope import document_scope_clause, normalize_document_ids
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


def _ledger_rows_for_document(session, document_id: str) -> list[tuple[str, str, str]]:
    """1 論文の台帳行 ``(ledger_id, target_id, target_type)``（行キーの論文で絞る — IK-0899）。"""
    where, params = document_scope_clause([document_id])
    rows = session.execute(
        sa_text(f"""
            SELECT id::text, target_id, target_type
            FROM epistemic_ledger
            WHERE {where}
        """),
        params,
    ).fetchall()
    return [(str(r[0]), str(r[1]), str(r[2])) for r in rows or []]


def _assumption_sources(session, rows) -> dict[str, Any]:
    assumption_ids = [target_id for _lid, target_id, target_type in rows if target_type == "assumption"]
    if not assumption_ids:
        return {}
    assumption_rows = session.execute(
        sa_text("""
            SELECT id::text, created_from
            FROM assumption_nodes
            WHERE id::text = ANY(:ids)
        """),
        {"ids": assumption_ids},
    ).fetchall()
    return {str(r[0]): r[1] for r in assumption_rows or []}


def _scores_for_rows(graph: DependencyGraph, rows, assumption_sources: dict[str, Any]) -> dict[str, float | None]:
    """台帳行ごとの load_score（純計算）。キーは ledger_id。"""
    scores: dict[str, float | None] = {}
    for ledger_id, target_id, target_type in rows:
        if target_type == "assumption":
            seeds = _assumption_seed_nodes(graph, assumption_sources.get(target_id))
        else:
            seeds = seed_nodes_for_target(graph, target_type, target_id)
        scores[ledger_id] = _load_for_seeds(graph, seeds)
    return scores


def recompute_load_scores(*, document_ids) -> dict:
    """指定範囲（document 集合, IK-0852）の台帳行の load_score を再計算する（冪等）。

    計算範囲は**論文単位**（IK-1001・2026-10-27 オーナー判断）: 範囲に論文が何本あっても、
    各台帳行はその行の論文（行キー ``document_id`` — IK-0899）1 本の依存グラフで計算する。
    グラフのノード ID（``theory_op_*`` / ``eq_*``）は論文の中でしか一意でなく、複数の論文を
    1 枚に混ぜると別の論文の同じ ID のノードが繋がって下流到達が膨らむ・縮むため、
    パイプライン完了時（論文 1 本）とコースの［再計算］（論文の集合）で同じ行の値が食い違っていた。
    コースの地図は読み時に論文ごとの値を並べ、段の閾値だけをコースの母集団で取る
    （``load_percentiles``）。

    範囲が空なら DB に触れずに ``{"updated": 0, "documents": 0}`` を返す（fail-closed）。
    """
    document_ids = normalize_document_ids(document_ids)
    if not document_ids:
        return {"updated": 0, "documents": 0}
    session = get_session()
    updated = 0
    try:
        for document_id in document_ids:
            graph = build_dependency_graph(
                session, document_ids=[document_id], exclude_claim_sequence_edges=True,
            )
            rows = _ledger_rows_for_document(session, document_id)
            scores = _scores_for_rows(graph, rows, _assumption_sources(session, rows))
            for ledger_id, score in scores.items():
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
            "load score recompute failed (documents=%d)", len(document_ids), exc_info=True,
        )
        return {"updated": 0, "failed": True, "documents": len(document_ids)}
    finally:
        session.close()

    logger.info(
        "load score recompute done: updated=%d (documents=%d)", updated, len(document_ids),
    )
    return {"updated": updated, "documents": len(document_ids)}


def compare_load_scopes(session, *, document_id: str, scope_document_ids) -> list[dict]:
    """同じ台帳行を 2 つの範囲で計算して並べる読み取り（IK-1001 の仮説の確かめ・書き込みなし）。

    ``document_scope`` = 行の論文 1 本のグラフ（保存の正本）、``wide_scope`` = ``scope_document_ids``
    （例: コースの論文全体）を 1 枚に混ぜたグラフ。値が違う行があれば「計算範囲で値が変わる」が
    成り立つ。生値は運用の点検用で、API・画面には出さない（SL4）。
    """
    document_id = str(document_id or "").strip()
    if not document_id:
        return []
    wide_ids = normalize_document_ids(list(scope_document_ids or []) + [document_id])
    rows = _ledger_rows_for_document(session, document_id)
    sources = _assumption_sources(session, rows)
    narrow = _scores_for_rows(
        build_dependency_graph(session, document_ids=[document_id], exclude_claim_sequence_edges=True),
        rows, sources,
    )
    wide = _scores_for_rows(
        build_dependency_graph(session, document_ids=wide_ids, exclude_claim_sequence_edges=True),
        rows, sources,
    )
    return [
        {
            "target_type": target_type,
            "target_id": target_id,
            "document_scope": narrow.get(ledger_id),
            "wide_scope": wide.get(ledger_id),
        }
        for ledger_id, target_id, target_type in rows
    ]


def compare_scores_with_and_without(
    graph_with: DependencyGraph,
    graph_without: DependencyGraph,
    rows,
    assumption_sources: dict[str, Any],
) -> list[dict]:
    """同じ台帳行を「出現順の辺あり / なし」の 2 枚のグラフで計算して並べる（純関数・IK-1030）。

    ``with_sequence_edges`` = claim チェーンの辺も依存に数えたグラフ（第 5 波までの計算）、
    ``without_sequence_edges`` = 外したグラフ（第 6 波以降の保存の正本）。値が大きく縮む行が
    論文の先頭側の主張（序論）に偏っていれば「出現順の先頭ほど最高位になる」仮説が成り立つ。
    生値は運用の点検用で、API・画面には出さない（SL4）。
    """
    with_scores = _scores_for_rows(graph_with, rows, assumption_sources)
    without_scores = _scores_for_rows(graph_without, rows, assumption_sources)
    return [
        {
            "target_type": target_type,
            "target_id": target_id,
            "with_sequence_edges": with_scores.get(ledger_id),
            "without_sequence_edges": without_scores.get(ledger_id),
        }
        for ledger_id, target_id, target_type in rows
    ]


def compare_claim_sequence_edges(session, *, document_id: str) -> list[dict]:
    """1 論文の台帳行を出現順の辺あり / なしで計算して並べる読み取り（IK-1030 の仮説の確かめ・書き込みなし）。"""
    document_id = str(document_id or "").strip()
    if not document_id:
        return []
    rows = _ledger_rows_for_document(session, document_id)
    sources = _assumption_sources(session, rows)
    return compare_scores_with_and_without(
        build_dependency_graph(session, document_ids=[document_id]),
        build_dependency_graph(session, document_ids=[document_id], exclude_claim_sequence_edges=True),
        rows,
        sources,
    )


def percentiles_from_scores(scores) -> tuple[float, float, float]:
    """load_score の列から p50 / p90 / p99（``percentile_cont`` と同じ線形補間・純関数）。

    空なら (0, 0, 0)（段階は最も慎重な側へ倒れる — ``load_level_for_score`` の約束）。
    """
    values = sorted(float(v) for v in scores if v is not None)
    if not values:
        return 0.0, 0.0, 0.0

    def _at(fraction: float) -> float:
        position = fraction * (len(values) - 1)
        lower = int(position)
        upper = min(lower + 1, len(values) - 1)
        weight = position - lower
        return values[lower] + (values[upper] - values[lower]) * weight

    return _at(0.5), _at(0.9), _at(0.99)


def load_percentiles(session, *, document_ids, exclusion=None) -> tuple[float, float, float]:
    """範囲（document 集合, IK-0852）内 load_score の p50 / p90 / p99（段階ラベル変換の閾値）。

    範囲が空なら SQL を出さずに (0, 0, 0)（呼び出し側の段階は最も慎重な側へ倒れる）。

    exclusion（IK-0970, 2026-10-25）: ``core.doubt.population.ClaimChainExclusion``。一覧から
    外す行（claim チェーンの導出ステップ）を**閾値の母集団からも**外す。表示の母集団と閾値の
    母集団を揃えないと、外した行が分布の上側を占めていたとき残りがどれも「高」に届かない。
    渡されたときは行を読んでアプリ側で百分位を取る（判定は ``exclusion.excludes`` の 1 箇所）。
    """
    clause = document_scope_clause(document_ids)
    if clause is None:
        return 0.0, 0.0, 0.0
    where, params = clause
    if exclusion is not None:
        rows = session.execute(
            sa_text(f"""
                SELECT target_type, target_id, document_id::text, load_score
                FROM epistemic_ledger
                WHERE {where} AND load_score IS NOT NULL
            """),
            params,
        ).fetchall()
        kept = [
            row[3] for row in rows or []
            if not _excluded_without_marking(exclusion, row[0], row[1], row[2])
        ]
        return percentiles_from_scores(kept)
    row = session.execute(
        sa_text(f"""
            SELECT
                COALESCE(percentile_cont(0.5) WITHIN GROUP (ORDER BY load_score), 0),
                COALESCE(percentile_cont(0.9) WITHIN GROUP (ORDER BY load_score), 0),
                COALESCE(percentile_cont(0.99) WITHIN GROUP (ORDER BY load_score), 0)
            FROM epistemic_ledger
            WHERE {where} AND load_score IS NOT NULL
        """),
        params,
    ).fetchone()
    if not row:
        return 0.0, 0.0, 0.0
    return float(row[0] or 0.0), float(row[1] or 0.0), float(row[2] or 0.0)


def _excluded_without_marking(exclusion, target_type, target_id, document_id) -> bool:
    """閾値の母集団の判定。``exclusion.excluded``（一覧に事実文を添える印）は立てない。

    印は「一覧から実際に外したか」の事実で、閾値の計算で外しただけでは立てない。
    """
    before = bool(getattr(exclusion, "excluded", False))
    hit = bool(exclusion.excludes(str(target_type or ""), str(target_id or ""), str(document_id or "")))
    try:
        exclusion.excluded = before
    except Exception:  # noqa: BLE001
        pass
    return hit
