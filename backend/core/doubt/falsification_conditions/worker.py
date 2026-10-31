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
from core.doubt.falsification_conditions.system import SYSTEM
from core.doubt.ledger_targets import ledger_row_filter_or_none, ledger_target_refs
from core.doubt.scope import document_scope_clause
from core.llm_usage.context import bind_usage_context
from core.llm_worker.cost_gate import CostGate, today_str
from core.postgres import get_session

logger = logging.getLogger(__name__)

# 1 回の worker 起動で処理する対象数の上限
_BATCH_LIMIT = 10

# 実装は core/llm_worker/cost_gate.py の CostGate に共通化済み（daily のみ・過去日の
# カウンタは古い方から破棄=prune_stale_daily）。doubt.scope_candidates /
# doubt.assumption_mining とは独立のインスタンス（独立カウンタ, 設計書 §3.3）。
_cost_gate: CostGate = SYSTEM.gate
_daily_call_counts: dict[str, int] = _cost_gate.daily_counts

_today_key = today_str


def _check_and_count_llm_call() -> bool:
    """日次上限内なら加算して True。上限超過なら False。

    上限値の正本は core/doubt/falsification_conditions/system.py の CostSpec
    （doubt_falsification_max_calls_per_day を settings から読む）。
    """
    return SYSTEM.check_and_count(gate=_cost_gate, settings=get_settings())


def _claim_pending_targets(
    session, document_id: str, document_ids: list[str] | None = None,
) -> list[tuple[str, str]]:
    """未解析の台帳行を claim し（analyzed_at を先に打つ）、対象リストを返す。"""
    filters = ["falsification_analyzed_at IS NULL"]
    params: dict = {"limit": _BATCH_LIMIT}
    if document_id:
        filters.append("document_id = CAST(NULLIF(:doc, '') AS uuid)")
        params["doc"] = document_id
    if document_ids is not None:
        # コース単位の起動（IK-0852）: 範囲はコースの教材から求めた document 集合。
        # 行の course_id 列では絞らない（空集合は SQL を出さない — 呼び出し側で閉じる）。
        scope = document_scope_clause(document_ids)
        if scope is None:
            return []
        filters.append(scope[0])
        params.update(scope[1])
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
            RETURNING target_type, target_id, document_id::text
        """),
        params,
    ).fetchall()
    session.commit()
    # IK-0899: 行キーの論文を対象に添えて運ぶ（2 要素の tuple のまま）。
    return ledger_target_refs(rows)


def _append_candidates(
    session, target_type: str, target_id: str, candidates: list[dict], *, document_id: str | None = None,
) -> None:
    if not candidates:
        return
    key = ledger_row_filter_or_none(target_type=target_type, target_id=target_id, document_id=document_id)
    if key is None:
        logger.warning("ledger row needs document_id; candidates not appended for %s/%s", target_type, target_id)
        return
    where, key_params = key
    session.execute(
        sa_text(f"""
            UPDATE epistemic_ledger
            SET falsification_candidates = falsification_candidates || CAST(:new AS jsonb),
                updated_at = now()
            WHERE {where}
        """),
        {"new": json.dumps(candidates, ensure_ascii=False), **key_params},
    )
    session.commit()


def run_falsification_condition_mining(
    document_id: str = "", course_id: str = "", document_ids: list[str] | None = None,
) -> dict:
    """未解析の台帳行に対して反証条件候補を抽出する（同期実行本体）。

    ``document_ids``（コース単位の起動, IK-0852）はコースの教材から求めた範囲。
    ``course_id`` は U層の帰属にだけ使い、台帳行の絞り込みには使わない。
    """
    bind_usage_context(
        "doubt:falsification_conditions",
        document_id=document_id or None,
        course_id=course_id or None,
    )
    session = get_session()
    try:
        targets = _claim_pending_targets(session, document_id, document_ids)
    except Exception:
        session.rollback()
        logger.warning("falsification condition claim failed", exc_info=True)
        session.close()
        return {"processed": 0}

    agent = FalsificationConditionAgent()
    processed = 0
    generated = 0
    try:
        for target in targets:
            target_type, target_id = target
            context = build_target_context(session, target_type, target_id)
            if not context.has_sources():
                processed += 1
                continue
            if not _check_and_count_llm_call():
                logger.info(
                    "falsification condition daily cap reached; remaining targets deferred"
                )
                # 上限超過分は未解析に戻す（翌日以降のバッチで処理）
                _reset_key = ledger_row_filter_or_none(
                    target_type=target_type, target_id=target_id,
                    document_id=getattr(target, "document_id", None),
                ) or ("target_id = :tid AND target_type = :ttype", {"tid": target_id, "ttype": target_type})
                session.execute(
                    sa_text(f"""
                        UPDATE epistemic_ledger
                        SET falsification_analyzed_at = NULL
                        WHERE {_reset_key[0]}
                    """),
                    _reset_key[1],
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
            _append_candidates(
                session, target_type, target_id, payloads,
                document_id=getattr(target, "document_id", None),
            )
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


def maybe_schedule_falsification_candidates(
    document_id: str = "", course_id: str = "", document_ids: list[str] | None = None,
) -> bool:
    """非同期バッチをデーモンスレッドで起動する（P6: 同期パスに LLM を入れない）。"""
    return SYSTEM.spawn(
        run_falsification_condition_mining,
        thread_factory=threading.Thread,
        thread_name="doubt-falsification-conditions",
        document_id=document_id,
        course_id=course_id,
        document_ids=document_ids,
    )
