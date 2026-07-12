"""暗黙前提マイニング — 非同期バッチ worker（D2-2, tension worker と同型）。

処理: 検出（非LLM）→ candidate 登録（冪等）→ 未正規化クラスタの LLM 正規化。
冪等性: cluster_key の一意性（検出）+ created_from.normalized / normalization_failed
（正規化）。コスト上限: DOUBT_ASSUMPTION_MAX_CALLS_PER_DAY（既定 10）。

正規化に 2 回失敗したクラスタは placeholder 文のまま
created_from.normalization_failed=true を付けて candidate として保持する（P4）。
"""

from __future__ import annotations

import json
import logging
import threading

from sqlalchemy import text as sa_text

from core.config import get_settings
from core.doubt.assumption_mining.detector import detect_gap_clusters, register_candidates
from core.doubt.assumption_mining.llm_client import AssumptionLLMClient
from core.doubt.assumption_mining.prompt import build_content
from core.doubt.assumption_mining.repair import run_with_repair
from core.llm_usage.context import bind_usage_context
from core.llm_worker.cost_gate import CostGate, today_str
from core.postgres import get_session

logger = logging.getLogger(__name__)

_BATCH_LIMIT = 10

# 実装は core/llm_worker/cost_gate.py の CostGate に共通化済み（daily のみ・過去日の
# カウンタは古い方から破棄=prune_stale_daily。daily_call_counts は同じ dict のエイリアス）。
_cost_gate = CostGate()
_daily_call_counts: dict[str, int] = _cost_gate.daily_counts


def _check_and_count_llm_call() -> bool:
    settings = get_settings()
    per_day = int(getattr(settings, "doubt_assumption_max_calls_per_day", 10))
    return _cost_gate.check_and_count(
        daily_limit=per_day, daily_key=today_str(), prune_stale_daily=True,
    )


def _pending_normalizations(session) -> list[tuple[str, dict]]:
    rows = session.execute(
        sa_text("""
            SELECT id::text, created_from
            FROM assumption_nodes
            WHERE status = 'candidate'
              AND origin = 'mined_gap'
              AND (created_from->>'normalized') IS NULL
              AND (created_from->>'normalization_failed') IS NULL
            ORDER BY created_at ASC
            LIMIT :limit
        """),
        {"limit": _BATCH_LIMIT},
    ).fetchall()
    out = []
    for row in rows:
        created_from = row[1]
        if isinstance(created_from, str):
            try:
                created_from = json.loads(created_from)
            except Exception:
                created_from = {}
        out.append((str(row[0]), created_from if isinstance(created_from, dict) else {}))
    return out


def _mark_created_from(session, assumption_id: str, patch: dict) -> None:
    session.execute(
        sa_text("""
            UPDATE assumption_nodes
            SET created_from = created_from || CAST(:patch AS jsonb),
                updated_at = now()
            WHERE id = CAST(:aid AS uuid)
        """),
        {"patch": json.dumps(patch, ensure_ascii=False), "aid": assumption_id},
    )


def run_assumption_mining(course_id: str = "") -> dict:
    """検出 + 登録 + 正規化を 1 バッチ実行する（同期実行本体）。"""
    bind_usage_context("doubt:assumption_normalize", course_id=course_id or None)
    session = get_session()
    registered = 0
    normalized = 0
    try:
        clusters = detect_gap_clusters(session, course_id=course_id)
        registered = register_candidates(session, clusters, course_id=course_id)

        # 検出時のクラスタテキストを cluster_key で引けるようにしておく
        texts_by_key = {c.cluster_key: c.source_texts for c in clusters}
        ops_by_key = {c.cluster_key: (c.operation, c.review_reasons) for c in clusters}

        llm_client = AssumptionLLMClient()
        for assumption_id, created_from in _pending_normalizations(session):
            operation = str(created_from.get("operation") or "")
            review_reasons = [str(r) for r in created_from.get("review_reasons") or []]
            # 検出済みクラスタからテキストを引く（無い場合は created_from の情報のみ）
            cluster_key = ""
            row = session.execute(
                sa_text("SELECT cluster_key FROM assumption_nodes WHERE id = CAST(:aid AS uuid)"),
                {"aid": assumption_id},
            ).fetchone()
            if row:
                cluster_key = str(row[0] or "")
            source_texts = list(texts_by_key.get(cluster_key, []))
            if not source_texts:
                source_texts = [operation] + review_reasons
            if not operation and not source_texts:
                _mark_created_from(session, assumption_id, {"normalization_failed": True})
                session.commit()
                continue
            if not _check_and_count_llm_call():
                logger.info("assumption normalization daily cap reached; deferred")
                break
            content = build_content(
                operation, ops_by_key.get(cluster_key, (operation, review_reasons))[1], source_texts
            )
            result = run_with_repair(llm_client, content, source_texts)
            if result is None:
                # P4: 行は捨てず placeholder 文のまま失敗マークで保持
                _mark_created_from(session, assumption_id, {"normalization_failed": True})
                session.commit()
                continue
            if result["statement"]:
                session.execute(
                    sa_text("""
                        UPDATE assumption_nodes
                        SET statement = :statement,
                            evidence_quote = :quote,
                            reason = :reason,
                            confidence = :confidence,
                            updated_at = now()
                        WHERE id = CAST(:aid AS uuid) AND status = 'candidate'
                    """),
                    {
                        "statement": result["statement"],
                        "quote": result["evidence_quote"],
                        "reason": result["reason"],
                        "confidence": float(result["confidence"]),
                        "aid": assumption_id,
                    },
                )
                normalized += 1
            _mark_created_from(session, assumption_id, {"normalized": True})
            session.commit()
    except Exception:
        session.rollback()
        logger.warning("assumption mining failed (course=%s)", course_id or "-", exc_info=True)
    finally:
        session.close()

    logger.info(
        "assumption mining done: registered=%d normalized=%d (course=%s)",
        registered, normalized, course_id or "-",
    )
    return {"registered": registered, "normalized": normalized}


def maybe_schedule_assumption_mining(course_id: str = "") -> bool:
    """非同期バッチをデーモンスレッドで起動する（P6）。"""
    thread = threading.Thread(
        target=run_assumption_mining,
        kwargs={"course_id": course_id},
        name="doubt-assumption-mining",
        daemon=True,
    )
    thread.start()
    return True
