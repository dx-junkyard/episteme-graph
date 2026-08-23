"""反証条件候補 — 非同期バッチ worker（SL-1, scope_candidates worker と同型）。

トリガー:
  - 管理画面からの明示リフレッシュ
    （POST /api/admin/doubt/courses/{course_id}/falsification-candidates/refresh）
  - v1 ではパイプラインフックへの相乗りは行わない（設計書 §3.3 / §14-5、
    コスト実測後の判断）。

冪等性: epistemic_ledger.falsification_analyzed_at（LLM 呼び出し前に打つ）。
コスト上限: DOUBT_FALSIFICATION_MAX_CALLS_PER_DAY（既定 10、doubt.scope_candidates /
doubt.assumption_mining とは独立の日次カウンタ）。

書き込み分離（SL2/SL3, ガードレール対象）: このモジュールは
falsification_candidates 列にのみ append し、falsification_conditions（確定列）・
verification_status・verification_scopes・reachability には一切書き込まない。
LLM 候補は教員確定まで falsification_conditions 本体には入らない。
"""

from __future__ import annotations

import datetime
import json
import logging
import threading

from sqlalchemy import text as sa_text

from core.config import get_settings
from core.doubt.falsification_conditions.agent import FalsificationConditionAgent
from core.doubt.falsification_conditions.input_builder import build_target_context
from core.llm_usage.context import bind_usage_context
from core.llm_worker.cost_gate import CostGate
from core.postgres import get_session

logger = logging.getLogger(__name__)

# 1 回の worker 起動で処理する対象数の上限
_BATCH_LIMIT = 10

# 実装は core/llm_worker/cost_gate.py の CostGate に共通化済み（daily のみ・過去日の
# カウンタは古い方から破棄=prune_stale_daily）。doubt.scope_candidates /
# doubt.assumption_mining とは独立のインスタンス（独立カウンタ, 設計書 §3.3）。
_cost_gate = CostGate()
_daily_call_counts: dict[str, int] = _cost_gate.daily_counts


def _today_key() -> str:
    return datetime.date.today().isoformat()


def _check_and_count_llm_call() -> bool:
    """日次上限内なら加算して True。上限超過なら False。"""
    settings = get_settings()
    per_day = int(getattr(settings, "doubt_falsification_max_calls_per_day", 10))
    return _cost_gate.check_and_count(
        daily_limit=per_day, daily_key=_today_key(), prune_stale_daily=True,
    )


def _claim_pending_targets(session, document_id: str, course_id: str) -> list[tuple[str, str]]:
    """未解析の台帳行を claim し（analyzed_at を先に打つ）、対象リストを返す。"""
    filters = ["falsification_analyzed_at IS NULL"]
    params: dict = {"limit": _BATCH_LIMIT}
    if document_id:
        filters.append("document_id = :doc")
        params["doc"] = document_id
    if course_id:
        filters.append("course_id = :course")
        params["course"] = course_id
    rows = session.execute(
        sa_text(f"""
            UPDATE epistemic_ledger
            SET falsification_analyzed_at = now()
            WHERE id IN (
                SELECT id FROM epistemic_ledger
                WHERE {' AND '.join(filters)}
                ORDER BY created_at ASC
                LIMIT :limit
                FOR UPDATE SKIP LOCKED
            )
            RETURNING target_type, target_id
        """),
        params,
    ).fetchall()
    session.commit()
    return [(str(r[0]), str(r[1])) for r in rows]


def _append_candidates(session, target_type: str, target_id: str, candidates: list[dict]) -> None:
    if not candidates:
        return
    session.execute(
        sa_text("""
            UPDATE epistemic_ledger
            SET falsification_candidates = falsification_candidates || CAST(:new AS jsonb),
                updated_at = now()
            WHERE target_id = :tid AND target_type = :ttype
        """),
        {
            "new": json.dumps(candidates, ensure_ascii=False),
            "tid": target_id,
            "ttype": target_type,
        },
    )
    session.commit()


def run_falsification_condition_mining(document_id: str = "", course_id: str = "") -> dict:
    """未解析の台帳行に対して反証条件候補を抽出する（同期実行本体）。"""
    bind_usage_context(
        "doubt:falsification_conditions",
        document_id=document_id or None,
        course_id=course_id or None,
    )
    session = get_session()
    try:
        targets = _claim_pending_targets(session, document_id, course_id)
    except Exception:
        session.rollback()
        logger.warning("falsification condition claim failed", exc_info=True)
        session.close()
        return {"processed": 0}

    agent = FalsificationConditionAgent()
    processed = 0
    generated = 0
    try:
        for target_type, target_id in targets:
            context = build_target_context(session, target_type, target_id)
            if not context.has_sources():
                processed += 1
                continue
            if not _check_and_count_llm_call():
                logger.info(
                    "falsification condition daily cap reached; remaining targets deferred"
                )
                # 上限超過分は未解析に戻す（翌日以降のバッチで処理）
                session.execute(
                    sa_text("""
                        UPDATE epistemic_ledger
                        SET falsification_analyzed_at = NULL
                        WHERE target_id = :tid AND target_type = :ttype
                    """),
                    {"tid": target_id, "ttype": target_type},
                )
                session.commit()
                break
            result = agent.run(context)
            stamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
            payloads = []
            for candidate in result.candidates:
                data = candidate.model_dump()
                data["created_at"] = stamp
                payloads.append(data)
            _append_candidates(session, target_type, target_id, payloads)
            processed += 1
            generated += len(payloads)
    except Exception:
        session.rollback()
        logger.warning("falsification condition mining failed", exc_info=True)
    finally:
        session.close()

    logger.info(
        "falsification condition mining done: processed=%d candidates=%d (document=%s)",
        processed, generated, document_id or "-",
    )
    return {"processed": processed, "candidates": generated}


def maybe_schedule_falsification_candidates(document_id: str = "", course_id: str = "") -> bool:
    """非同期バッチをデーモンスレッドで起動する（P6: 同期パスに LLM を入れない）。"""
    thread = threading.Thread(
        target=run_falsification_condition_mining,
        kwargs={"document_id": document_id, "course_id": course_id},
        name="doubt-falsification-conditions",
        daemon=True,
    )
    thread.start()
    return True
