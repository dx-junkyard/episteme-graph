"""未検証合意リスト（Open Assumptions List, D3-2）の自動編纂。

台帳から「高負荷（段階: 高以上）× 低検証（untested / unknown またはスコープ空欄）」を
抽出し、紐づく疑義・検証提案・学習者シグナルを合成する。

原則:
  - リストは台帳の**投影**であり編集不可。台帳の状態変化（スコープ記帳）で自動的に増減する。
  - 生数値スコアを返さない（load は段階ラベル、疑義数は段階ラベル）。
  - 順位づけ・スコア化・賞レース的な価値づけ演出は禁止（§8-5）。並び順は負荷段階→依存数。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text as sa_text

from core.doubt.load_calculator import load_percentiles
from core.doubt.naive_signal import has_naive_signal
from core.doubt.schema import load_level_for_score, scope_coverage_level
from core.doubt.support_paths import build_support_context, compute_support_lines_from_context

logger = logging.getLogger(__name__)

_LOW_VERIFICATION_STATUSES = ("untested", "unknown")

# SL-4: 反証条件群からの到達可能性の要約（最良値。reachable > next_generation >
# unreachable > unassessed。条件なしは呼び出し側で "" のまま返す）。
_REACHABILITY_PRIORITY = {"reachable": 0, "next_generation": 1, "unreachable": 2, "unassessed": 3}


def _falsification_reachability_summary(conditions: list[dict]) -> str:
    """not_formulable を除いた条件群の最良到達可能性（SL-4, §6.1）。"""
    candidates = [str(c.get("reachability") or "unassessed") for c in conditions if isinstance(c, dict)]
    if not candidates:
        return ""
    return min(candidates, key=lambda r: _REACHABILITY_PRIORITY.get(r, 99))


def _challenge_count_label(count: int) -> str:
    """疑義数の段階ラベル（数値スコア化しない）。"""
    if count <= 0:
        return ""
    if count == 1:
        return "1件"
    if count <= 3:
        return "少数"
    return "複数"


def target_label(session, target_type: str, target_id: str) -> str:
    """台帳対象の表示ラベルを引く（見つからなければ id をそのまま）。"""
    try:
        if target_type == "claim":
            row = session.execute(
                sa_text("SELECT COALESCE(NULLIF(normalized_text, ''), text) FROM theory_claims WHERE id::text = :tid"),
                {"tid": target_id},
            ).fetchone()
            if row and row[0]:
                return str(row[0])[:160]
        elif target_type == "component":
            row = session.execute(
                sa_text("SELECT name FROM theory_components WHERE id::text = :tid"),
                {"tid": target_id},
            ).fetchone()
            if row and row[0]:
                return str(row[0])[:160]
        elif target_type == "assumption":
            row = session.execute(
                sa_text("SELECT statement FROM assumption_nodes WHERE id::text = :tid"),
                {"tid": target_id},
            ).fetchone()
            if row and row[0]:
                return str(row[0])[:200]
    except Exception:
        logger.debug("target label lookup failed for %s/%s", target_type, target_id, exc_info=True)
    return target_id


def related_confirmed_assumption(session, anchor_id: str) -> dict | None:
    """anchor（claim / equation / component id）に対応する confirmed 前提を引く（D3-6）。

    学習者の structure_anchor 確定時の「事後の静かな表示」用。通知しない・
    押し付けない — 表示は帰属事実のみ（B層の「接続を宣言しない」と同じ規律）。
    """
    anchor_id = str(anchor_id or "").strip()
    if not anchor_id:
        return None
    try:
        row = session.execute(
            sa_text("""
                SELECT id::text, statement
                FROM assumption_nodes
                WHERE status IN ('confirmed', 'operationalized')
                  AND (
                    created_from->'node_ids' @> to_jsonb(CAST(:aid AS text))
                    OR created_from->'claim_ids' @> to_jsonb(CAST(:aid AS text))
                    OR created_from->'equation_ids' @> to_jsonb(CAST(:aid AS text))
                  )
                ORDER BY confirmed_at DESC NULLS LAST
                LIMIT 1
            """),
            {"aid": anchor_id},
        ).fetchone()
        if row is None:
            return None
        return {"assumption_id": str(row[0]), "statement": str(row[1] or "")}
    except Exception:
        logger.debug("related assumption lookup failed for anchor=%s", anchor_id, exc_info=True)
        return None


def compile_open_assumptions(
    session,
    course_id: str,
    include_challenger_names: bool = False,
    document_id: str = "",
) -> list[dict]:
    """未検証合意リストを台帳から編纂する（読み取り専用の投影）。

    include_challenger_names=False（学習者向け）では疑義者名を一切含めない。
    True（教員向け）でも名前は challenge 詳細参照用で、リスト自体には
    型と段階ラベルのみを載せる。

    document_id（optional, seminar_brief_mirroring_design.md §3 精査④）:
    指定時は台帳行を当該 document に絞る（SQL に AND 1条件を足すだけ）。
    percentile は従来どおり **course 全体**で計算する — 「高」の意味
    （コースの中での相対負荷）を document 絞り込みで変えないため。
    既定（空文字）は従来挙動完全不変。
    """
    document_id = str(document_id or "").strip()
    p50, p90, p99 = load_percentiles(session, course_id)

    # SL-3: 独立支持経路の共有文脈を1度だけ構築し、対象ごとに再利用する
    # （compute_support_lines を項目数分呼ぶとグラフを N 回再構築してしまう — 性能改善）。
    support_ctx = build_support_context(session, course_id=course_id, document_id=document_id)

    params: dict[str, Any] = {"course": course_id}
    doc_filter = ""
    if document_id:
        doc_filter = "AND document_id = :doc"
        params["doc"] = document_id
    rows = session.execute(
        sa_text(f"""
            SELECT target_id, target_type, verification_status,
                   verification_scopes, consensus_behavioral, load_score,
                   falsification_conditions
            FROM epistemic_ledger
            WHERE course_id = :course
              AND load_score IS NOT NULL
              {doc_filter}
        """),
        params,
    ).fetchall()

    items: list[dict] = []
    for row in rows:
        target_id = str(row[0])
        target_type = str(row[1])
        status = str(row[2] or "unknown")
        scopes = row[3] if isinstance(row[3], list) else []
        behavioral = int(row[4] or 0)
        load_level = load_level_for_score(
            float(row[5]) if row[5] is not None else None, p50, p90, p99
        )
        if load_level not in ("high", "highest"):
            continue
        low_verification = status in _LOW_VERIFICATION_STATUSES or len(scopes) == 0
        if not low_verification:
            continue

        # SL-1/SL-4: 反証条件の記帳状況（§6.1）。not_formulable は「定式化できない」という
        # 人間の明示記帳であり、それ以外の記帳とは別に扱う（反証不可能の記帳 vs 未検討）。
        falsification_conditions = row[6] if isinstance(row[6], list) else []
        non_not_formulable = [
            c for c in falsification_conditions
            if isinstance(c, dict) and str(c.get("kind") or "") != "not_formulable"
        ]
        has_falsification_condition = bool(non_not_formulable)
        falsification_not_formulable = (not has_falsification_condition) and any(
            isinstance(c, dict) and str(c.get("kind") or "") == "not_formulable"
            for c in falsification_conditions
        )
        reachability_summary = _falsification_reachability_summary(non_not_formulable)

        # SL-3: 独立支持経路の段階（導出失敗は "" のまま — キーは常に返す）。
        support_line_level = ""
        try:
            support_lines = compute_support_lines_from_context(support_ctx, target_type, target_id)
            if support_lines:
                support_line_level = str(support_lines.get("level") or "")
        except Exception:  # noqa: BLE001
            logger.debug(
                "support line computation failed for %s/%s", target_type, target_id, exc_info=True
            )
            support_line_level = ""

        challenge_rows = session.execute(
            sa_text("""
                SELECT c.id::text, c.challenge_type, c.status, u.display_name
                FROM challenges c
                LEFT JOIN users u ON u.id = c.challenger_id
                WHERE c.target_type IN ('assumption', 'claim')
                  AND c.target_id = :tid
                  AND c.status <> 'withdrawn'
            """),
            {"tid": target_id},
        ).fetchall()
        proposal_row = session.execute(
            sa_text("""
                SELECT COUNT(*)
                FROM verification_proposals vp
                JOIN challenges c ON c.id = vp.challenge_id
                WHERE c.target_id = :tid AND vp.status <> 'withdrawn'
            """),
            {"tid": target_id},
        ).fetchone()

        challenge_types = sorted({str(r[1]) for r in challenge_rows})
        item = {
            "target_id": target_id,
            "target_type": target_type,
            "statement": target_label(session, target_type, target_id),
            "verification_status": status,
            "scope_coverage": scope_coverage_level(len(scopes)),
            "scope_count_is_zero": len(scopes) == 0,
            "load_level": load_level,
            "dependent_count": behavioral,
            "challenge_count_label": _challenge_count_label(len(challenge_rows)),
            "challenge_types": challenge_types,
            "has_verification_proposal": bool(int(proposal_row[0] or 0)) if proposal_row else False,
            # naive signal は anchor 語彙と一致する対象種別のみ引く
            "has_naive_signal": (
                has_naive_signal(target_type, target_id, course_id)
                if target_type in ("claim", "equation", "component")
                else False
            ),
            # SL-1/SL-3/SL-4: 覆る条件・到達可能性・支持線（すべて事実。数値は出さない）。
            "has_falsification_condition": has_falsification_condition,
            "falsification_not_formulable": falsification_not_formulable,
            "reachability_summary": reachability_summary,
            "support_line_level": support_line_level,
        }
        if include_challenger_names:
            item["challengers"] = sorted({str(r[3] or "") for r in challenge_rows if r[3]})
        items.append(item)

    # 並び順は負荷段階 → 依存数（順位づけの演出はしない）
    level_order = {"highest": 0, "high": 1}
    items.sort(key=lambda i: (level_order.get(i["load_level"], 9), -i["dependent_count"], i["target_id"]))
    return items
