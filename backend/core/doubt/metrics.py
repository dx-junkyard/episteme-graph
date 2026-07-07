"""DX-2 — D層 KPI の内部計測（ユーザー非表示）。

「システムが指摘した数」ではなく「人間が行為した数」を測る（構想 §9）。
集計は theory_review_events + 台帳/前提テーブルの再集計だけで再現できる
（専用カウンタテーブルを持たない）。数値をユーザーに見せる API・UI は作らない
（この関数の呼び出し元は SYSTEM_ADMIN 専用の運用判断 API のみ）。
"""

from __future__ import annotations

import logging

from sqlalchemy import text as sa_text

from core.postgres import get_session

logger = logging.getLogger(__name__)


def collect_doubt_metrics() -> dict:
    """D層 KPI を theory_review_events と台帳の再集計から算出する。"""
    session = get_session()
    try:
        metrics: dict = {}

        # 台帳被覆率: 高負荷（コーパス p90 以上）ノードのうちスコープ記帳済みの割合
        row = session.execute(sa_text("""
            WITH threshold AS (
                SELECT COALESCE(percentile_cont(0.9) WITHIN GROUP (ORDER BY load_score), 0) AS p90
                FROM epistemic_ledger WHERE load_score IS NOT NULL
            )
            SELECT
                COUNT(*) FILTER (WHERE l.load_score >= t.p90 AND t.p90 > 0),
                COUNT(*) FILTER (WHERE l.load_score >= t.p90 AND t.p90 > 0
                                   AND l.verification_scopes <> '[]'::jsonb)
            FROM epistemic_ledger l, threshold t
            WHERE l.load_score IS NOT NULL
        """)).fetchone()
        high_load_total = int(row[0] or 0) if row else 0
        high_load_scoped = int(row[1] or 0) if row else 0
        metrics["ledger_coverage"] = {
            "high_load_targets": high_load_total,
            "high_load_with_scopes": high_load_scoped,
        }

        # 空欄の可視化数（スコープ未記帳の台帳行 = 見えている空欄）
        row = session.execute(sa_text("""
            SELECT COUNT(*),
                   COUNT(*) FILTER (WHERE verification_scopes = '[]'::jsonb)
            FROM epistemic_ledger
        """)).fetchone()
        metrics["ledger_rows"] = {
            "total": int(row[0] or 0) if row else 0,
            "unscoped_visible": int(row[1] or 0) if row else 0,
        }

        # 人間の記帳行為数（review_events ベース）
        row = session.execute(sa_text("""
            SELECT
                COUNT(*) FILTER (WHERE entity_type = 'ledger'),
                COUNT(*) FILTER (WHERE entity_type = 'ledger'
                                   AND (metadata->>'action') = 'scope_add'),
                COUNT(*) FILTER (WHERE entity_type = 'ledger'
                                   AND (metadata->>'action') = 'scope_candidate_confirm')
            FROM theory_review_events
        """)).fetchone()
        metrics["ledger_actions"] = {
            "total_events": int(row[0] or 0) if row else 0,
            "scopes_recorded": int(row[1] or 0) if row else 0,
            "candidates_confirmed": int(row[2] or 0) if row else 0,
        }

        # candidate → confirmed 率（前提）
        row = session.execute(sa_text("""
            SELECT
                COUNT(*),
                COUNT(*) FILTER (WHERE status = 'confirmed'),
                COUNT(*) FILTER (WHERE status = 'dismissed'),
                COUNT(*) FILTER (WHERE status = 'operationalized')
            FROM assumption_nodes
        """)).fetchone()
        metrics["assumptions"] = {
            "total": int(row[0] or 0) if row else 0,
            "confirmed": int(row[1] or 0) if row else 0,
            "dismissed": int(row[2] or 0) if row else 0,
            "operationalized": int(row[3] or 0) if row else 0,
        }

        # 理由つき疑義数と検証提案昇格率
        row = session.execute(sa_text("""
            SELECT
                COUNT(*),
                COUNT(*) FILTER (WHERE status = 'led_to_verification'),
                COUNT(*) FILTER (WHERE status = 'withdrawn')
            FROM challenges
        """)).fetchone()
        metrics["challenges"] = {
            "total": int(row[0] or 0) if row else 0,
            "led_to_verification": int(row[1] or 0) if row else 0,
            "withdrawn": int(row[2] or 0) if row else 0,
        }
        row = session.execute(sa_text("SELECT COUNT(*) FROM verification_proposals")).fetchone()
        metrics["verification_proposals"] = {"total": int(row[0] or 0) if row else 0}

        # 反実仮想セッション数と共有数
        row = session.execute(sa_text("""
            SELECT COUNT(*),
                   COUNT(*) FILTER (WHERE shared_scope <> 'private')
            FROM counterfactual_sessions
        """)).fetchone()
        metrics["counterfactual_sessions"] = {
            "total": int(row[0] or 0) if row else 0,
            "shared": int(row[1] or 0) if row else 0,
        }

        # 素朴な問い → confirmed 前提の転換数（origin='naive_aggregate' の confirmed）
        row = session.execute(sa_text("""
            SELECT COUNT(*) FROM assumption_nodes
            WHERE origin = 'naive_aggregate' AND status IN ('confirmed', 'operationalized')
        """)).fetchone()
        metrics["naive_to_confirmed"] = int(row[0] or 0) if row else 0

        return metrics
    finally:
        session.close()
