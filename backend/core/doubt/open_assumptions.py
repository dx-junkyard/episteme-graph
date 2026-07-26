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

logger = logging.getLogger(__name__)

_LOW_VERIFICATION_STATUSES = ("untested", "unknown")


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
) -> list[dict]:
    """未検証合意リストを台帳から編纂する（読み取り専用の投影）。

    include_challenger_names=False（学習者向け）では疑義者名を一切含めない。
    True（教員向け）でも名前は challenge 詳細参照用で、リスト自体には
    型と段階ラベルのみを載せる。
    """
    p50, p90, p99 = load_percentiles(session, course_id)

    rows = session.execute(
        sa_text("""
            SELECT target_id, target_type, verification_status,
                   verification_scopes, consensus_behavioral, load_score
            FROM epistemic_ledger
            WHERE course_id = :course
              AND load_score IS NOT NULL
        """),
        {"course": course_id},
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
        }
        if include_challenger_names:
            item["challengers"] = sorted({str(r[3] or "") for r in challenge_rows if r[3]})
        items.append(item)

    # 並び順は負荷段階 → 依存数（順位づけの演出はしない）
    level_order = {"highest": 0, "high": 1}
    items.sort(key=lambda i: (level_order.get(i["load_level"], 9), -i["dependent_count"], i["target_id"]))
    return items
