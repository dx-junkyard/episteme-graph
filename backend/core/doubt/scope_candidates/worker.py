"""検証スコープ候補 — 非同期バッチ worker（D1-4, tension worker と同型）。

トリガー:
  - ドキュメント解析完了時の台帳バックフィル後（best-effort）
  - 管理画面からの明示リフレッシュ（POST /api/admin/doubt/.../scope-candidates/refresh）

冪等性: epistemic_ledger.scope_candidates_analyzed_at（LLM 呼び出し前に打つ）。
コスト上限: DOUBT_SCOPE_MAX_CALLS_PER_DAY（既定 10、in-memory 日次カウンタ）。
LLM 候補は scope_candidates 列に status='candidate' で append され、教員確定まで
verification_scopes 本体には入らない。
"""

from __future__ import annotations

import datetime
import json
import logging
import threading

from sqlalchemy import text as sa_text

from core.config import get_settings
from core.doubt.scope_candidates.agent import ScopeCandidateAgent
from core.doubt.scope_candidates.input_builder import build_target_context
from core.doubt.scope_candidates.system import SYSTEM
from core.doubt.ledger_targets import ledger_row_filter_or_none, ledger_target_refs
from core.doubt.scope import document_scope_clause
from core.llm_usage.context import bind_usage_context
from core.llm_worker.cost_gate import CostGate, today_str
from core.postgres import get_session
from core.schema import CLAIM_ORIGIN_EQUATION_SYNTHESIS, NON_SOURCE_TEXT_CLAIM_ORIGINS  # noqa: F401

logger = logging.getLogger(__name__)

# 1 回の worker 起動で処理する対象数の上限
_BATCH_LIMIT = 10

# 実装は core/llm_worker/cost_gate.py の CostGate（core/doubt/scope_candidates/system.py
# の WorkerSystem が1個だけ持つ。daily のみ・過去日のカウンタは破棄）。
# daily_call_counts は同じ dict オブジェクトへのエイリアス。
_cost_gate: CostGate = SYSTEM.gate
_daily_call_counts: dict[str, int] = _cost_gate.daily_counts

_today_key = today_str

#: 対象から外す理由（IK-0790）。式から決定論で合成した定型主張
#: （``theory_claims.origin='equation_synthesis'``・「An equation of this paper defines X.」型）は
#: 論文の文を持たず、検証条件を抽出する材料が原理的に無い。LLM に渡すと候補は必ず空で、
#: 日次上限だけを消費する。台帳行には触らない（scope_candidates は空のまま・P4）。
SKIP_REASON_SYNTHESIZED_CLAIMS = "skipped_synthesized_claims"

#: 対象選択で除外する主張の origin。
_EXCLUDED_CLAIM_ORIGINS: tuple[str, ...] = NON_SOURCE_TEXT_CLAIM_ORIGINS

#: 台帳行のうち「式由来の合成主張を指す claim 行」を表す述語（epistemic_ledger を外側に置く）。
_SYNTHESIZED_CLAIM_PREDICATE = """
    target_type = 'claim' AND EXISTS (
        SELECT 1 FROM theory_claims_live c
        WHERE c.id::text = epistemic_ledger.target_id
          AND c.origin = ANY(:excluded_origins)
    )
"""


def _check_and_count_llm_call() -> bool:
    """日次上限内なら加算して True。上限超過なら False。

    上限値の正本は core/doubt/scope_candidates/system.py の CostSpec
    （doubt_scope_max_calls_per_day を settings から読む）。
    """
    return SYSTEM.check_and_count(gate=_cost_gate, settings=get_settings())


def _claim_pending_targets(
    session, document_id: str, document_ids: list[str] | None = None,
) -> list[tuple[str, str]]:
    """未解析の台帳行を claim し（analyzed_at を先に打つ）、対象リストを返す。"""
    filters = [
        "scope_candidates_analyzed_at IS NULL",
        f"NOT ({_SYNTHESIZED_CLAIM_PREDICATE})",
    ]
    params: dict = {"limit": _BATCH_LIMIT, "excluded_origins": list(_EXCLUDED_CLAIM_ORIGINS)}
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
            SET scope_candidates_analyzed_at = now()
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


def _has_skipped_synthesized_targets(
    session, document_id: str, document_ids: list[str] | None = None,
) -> bool:
    """未解析のまま対象選択から外れた合成主張の台帳行があるか（件数は返さない）。"""
    filters = ["scope_candidates_analyzed_at IS NULL", _SYNTHESIZED_CLAIM_PREDICATE]
    params: dict = {"excluded_origins": list(_EXCLUDED_CLAIM_ORIGINS)}
    if document_id:
        filters.append("document_id = CAST(NULLIF(:doc, '') AS uuid)")
        params["doc"] = document_id
    if document_ids is not None:
        # コース単位の起動（IK-0852）: 範囲はコースの教材から求めた document 集合。
        # 行の course_id 列では絞らない（空集合は SQL を出さない — 呼び出し側で閉じる）。
        scope = document_scope_clause(document_ids)
        if scope is None:
            return False
        filters.append(scope[0])
        params.update(scope[1])
    row = session.execute(
        sa_text(f"""
            SELECT EXISTS (
                SELECT 1 FROM epistemic_ledger WHERE {' AND '.join(filters)}
            )
        """),
        params,
    ).fetchone()
    return bool(row and row[0])


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
            SET scope_candidates = scope_candidates || CAST(:new AS jsonb),
                updated_at = now()
            WHERE {where}
        """),
        {"new": json.dumps(candidates, ensure_ascii=False), **key_params},
    )
    session.commit()


def run_scope_candidate_mining(
    document_id: str = "", course_id: str = "", document_ids: list[str] | None = None,
) -> dict:
    """未解析の台帳行に対してスコープ候補を抽出する（同期実行本体）。

    ``document_ids``（コース単位の起動, IK-0852）はコースの教材から求めた範囲。
    ``course_id`` は U層の帰属にだけ使い、台帳行の絞り込みには使わない。
    """
    bind_usage_context(
        "doubt:scope_candidates",
        document_id=document_id or None,
        course_id=course_id or None,
    )
    session = get_session()
    try:
        targets = _claim_pending_targets(session, document_id, document_ids)
    except Exception:
        session.rollback()
        logger.warning("scope candidate claim failed", exc_info=True)
        session.close()
        return {"processed": 0}

    skipped_reasons: list[str] = []
    try:
        if _has_skipped_synthesized_targets(session, document_id, document_ids):
            skipped_reasons.append(SKIP_REASON_SYNTHESIZED_CLAIMS)
            logger.info(
                "scope candidate targets excluded: reason=%s (document=%s)",
                SKIP_REASON_SYNTHESIZED_CLAIMS, document_id or "-",
            )
    except Exception:
        session.rollback()
        logger.debug("scope candidate skip probe failed", exc_info=True)

    agent = ScopeCandidateAgent()
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
                logger.info("scope candidate daily cap reached; remaining targets deferred")
                # 上限超過分は未解析に戻す（翌日以降のバッチで処理）
                _reset_key = ledger_row_filter_or_none(
                    target_type=target_type, target_id=target_id,
                    document_id=getattr(target, "document_id", None),
                ) or ("target_id = :tid AND target_type = :ttype", {"tid": target_id, "ttype": target_type})
                session.execute(
                    sa_text(f"""
                        UPDATE epistemic_ledger
                        SET scope_candidates_analyzed_at = NULL
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
        logger.warning("scope candidate mining failed", exc_info=True)
    finally:
        session.close()

    logger.info(
        "scope candidate mining done: processed=%d candidates=%d (document=%s)",
        processed, generated, document_id or "-",
    )
    return {
        "processed": processed,
        "candidates": generated,
        "skipped_reasons": skipped_reasons,
    }


def maybe_schedule_scope_candidates(
    document_id: str = "", course_id: str = "", document_ids: list[str] | None = None,
) -> bool:
    """非同期バッチをデーモンスレッドで起動する（P6: 同期パスに LLM を入れない）。"""
    return SYSTEM.spawn(
        run_scope_candidate_mining,
        thread_factory=threading.Thread,
        thread_name="doubt-scope-candidates",
        document_id=document_id,
        course_id=course_id,
        document_ids=document_ids,
    )
