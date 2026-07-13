"""item オーサリング worker（tension worker と同じ threading.Thread 方式）。

- トリガー: claim の承認時（theory_components の claim PATCH フック）/ 手動バッチ API。
- 冪等性: claim_id に非 retired の既存 item があればスキップ。
- コスト上限: 1 ドキュメントあたり RECON_MAX_ITEMS_PER_DOCUMENT（既定 30）、
  1 日あたり RECON_MAX_CALLS_PER_DAY（既定 10、他機能と独立）。モデルは fast tier 既定。
- LLM を使うのはここ（オーサリング）のみ。実行時 DIFF は非LLM（P6）。

core に属するため api/services.py に依存しない（永続化はここで直接 SQL を発行する）。
"""

from __future__ import annotations

import datetime
import json
import logging
import threading
from typing import Any

from sqlalchemy import text as sa_text

from core.config import get_settings
from core.llm_usage.context import bind_usage_context
from core.llm_worker.cost_gate import CostGate
from core.postgres import get_session as _pg_session
from core.reconstruction.input_builder import build_user_content
from core.reconstruction.item_builder import preferred_elicit_mode, response_options_to_dicts
from core.reconstruction.llm_client import ReconstructionLLMClient
from core.reconstruction.prompt import build_instruction
from core.reconstruction.repair import run_with_repair
from core.reconstruction.schema import (
    APPROVED_REVIEW_STATUSES,
    ENTITY_ITEM,
    SOURCE_BACKED,
    ItemAuthoringResult,
)

logger = logging.getLogger(__name__)

# 実装は core/llm_worker/cost_gate.py の CostGate に共通化済み（daily のみ・session
# 上限は使わない）。daily_call_counts は同じ dict オブジェクトへのエイリアス。
_cost_gate = CostGate()
_daily_call_counts: dict[str, int] = _cost_gate.daily_counts


def _today() -> str:
    return datetime.date.today().isoformat()


def _check_and_count_llm_call() -> bool:
    """1 日あたりのオーサリング LLM コール上限内なら True。上限超過なら False。"""
    per_day = int(getattr(get_settings(), "recon_max_calls_per_day", 10))
    ok = _cost_gate.check_and_count(daily_limit=per_day, daily_key=_today())
    if not ok:
        logger.info("recon item authoring skipped: per-day cap reached")
    return ok


def _fetch_authorable_claims(session, document_id: str, limit: int) -> list[dict]:
    """source_backed + 承認済み、かつ非 retired の item を持たない claim を返す。

    記号葉プローブ（elicit_mode='symbol'、descend 時に非LLMで生成）は
    「その claim はオーサリング済み」とは数えない。
    """
    approved = list(APPROVED_REVIEW_STATUSES)
    rows = session.execute(
        sa_text("""
            SELECT c.id::text, c.document_id, c.claim_type, c.text, c.normalized_text,
                   c.concepts, c.equation, c.source_scope, c.evidence_text,
                   c.support_status, c.review_status
            FROM theory_claims c
            WHERE c.document_id = :doc
              AND c.support_status = :backed
              AND c.review_status = ANY(:approved)
              AND NOT EXISTS (
                  SELECT 1 FROM reconstruction_items i
                  WHERE i.claim_id = c.id AND i.status <> 'retired'
                    AND i.elicit_mode <> 'symbol'
              )
            ORDER BY c.created_at ASC
            LIMIT :limit
        """),
        {"doc": document_id, "backed": SOURCE_BACKED, "approved": approved, "limit": limit},
    ).fetchall()
    claims: list[dict] = []
    for r in rows:
        claims.append({
            "id": r[0],
            "document_id": r[1] or "",
            "claim_type": r[2] or "",
            "text": r[3] or "",
            "normalized_text": r[4] or "",
            "concepts": _json(r[5], []),
            "equation": _json(r[6], {}),
            "source_scope": _json(r[7], {}),
            "evidence_text": r[8] or "",
            "support_status": r[9] or "",
            "review_status": r[10] or "",
        })
    return claims


def _json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def author_item_for_claim(claim: dict, llm_client=None) -> ItemAuthoringResult:
    """1 claim 分の item を LLM でオーサリングする（validator/repair 付き）。"""
    client = llm_client or ReconstructionLLMClient()
    mode = preferred_elicit_mode(claim)
    content = build_instruction(mode) + "\n\n" + build_user_content(claim)
    return run_with_repair(client, content, claim)


def _persist_item(session, claim: dict, result: ItemAuthoringResult) -> str | None:
    row = session.execute(
        sa_text("""
            INSERT INTO reconstruction_items
            (claim_id, document_id, elicit_mode, prompt, response_space, expected,
             claim_fields_used, author, author_confidence, status)
            VALUES (CAST(:claim_id AS uuid), :document_id, :elicit_mode, :prompt,
                    CAST(:response_space AS jsonb), CAST(:expected AS jsonb),
                    CAST(:claim_fields_used AS jsonb), 'llm', :author_confidence, 'auto')
            RETURNING id::text
        """),
        {
            "claim_id": claim["id"],
            "document_id": claim.get("document_id") or "",
            "elicit_mode": result.elicit_mode,
            "prompt": result.prompt,
            "response_space": json.dumps(response_options_to_dicts(result.response_space), ensure_ascii=False),
            "expected": json.dumps(result.expected or {}, ensure_ascii=False),
            "claim_fields_used": json.dumps(result.claim_fields_used or [], ensure_ascii=False),
            "author_confidence": float(result.author_confidence),
        },
    ).fetchone()
    return str(row[0]) if row else None


def run_item_authoring_for_document(document_id: str) -> int:
    """1 ドキュメント分の未オーサリング claim に item を生成する。戻り値は生成数。

    RECON_MAX_ITEMS_PER_DOCUMENT は**累積**上限（1回の実行あたりではない）。
    既存の非 retired item 数を差し引いた残余分だけ新規オーサリングする。
    記号葉プローブ（非LLM・descend 由来）は LLM コスト制御の対象外なので数えない。
    """
    if not document_id:
        return 0
    bind_usage_context("admin:reconstruction_authoring", document_id=str(document_id))
    max_items = int(getattr(get_settings(), "recon_max_items_per_document", 30))
    session = _pg_session()
    try:
        existing = session.execute(
            sa_text("""
                SELECT COUNT(*) FROM reconstruction_items
                WHERE document_id = :doc AND status <> 'retired' AND elicit_mode <> 'symbol'
            """),
            {"doc": document_id},
        ).scalar() or 0
        remaining = max_items - int(existing)
        if remaining <= 0:
            logger.info(
                "recon item authoring skipped: per-document cap reached (document=%s, existing=%d)",
                document_id, int(existing),
            )
            return 0
        claims = _fetch_authorable_claims(session, document_id, remaining)
    except Exception as exc:
        logger.warning("recon authoring fetch failed for %s: %s", document_id, exc)
        return 0
    finally:
        session.close()

    if not claims:
        return 0

    created = 0
    for claim in claims:
        if not _check_and_count_llm_call():
            break
        try:
            result = author_item_for_claim(claim)
        except Exception as exc:
            logger.warning("recon authoring LLM failed for claim %s: %s", claim["id"], exc)
            continue
        if result.repair_failed:
            logger.info("recon authoring skipped claim %s (repair failed)", claim["id"])
            continue
        session = _pg_session()
        try:
            item_id = _persist_item(session, claim, result)
            session.commit()
        except Exception as exc:
            session.rollback()
            logger.warning("recon authoring persist failed for claim %s: %s", claim["id"], exc)
            session.close()
            continue
        finally:
            session.close()
        if item_id:
            created += 1
            _record_item_event(item_id, claim["id"], "", "auto", None, {"author": "llm", "mode": result.elicit_mode})
    logger.info("recon item authoring done: document=%s created=%d", document_id, created)
    return created


def maybe_schedule_item_authoring(document_id: str) -> bool:
    """バックグラウンドスレッドでオーサリングを起動する（best-effort・非同期）。"""
    if not document_id:
        return False
    try:
        threading.Thread(
            target=run_item_authoring_for_document,
            args=(document_id,),
            daemon=True,
        ).start()
        return True
    except Exception as exc:
        logger.warning("maybe_schedule_item_authoring failed: %s", exc)
        return False


def _record_item_event(
    item_id: str,
    claim_id: str,
    old_status: str,
    new_status: str,
    user_id: str | None,
    metadata: dict | None = None,
) -> None:
    """theory_review_events への監査記録（entity_type='reconstruction_item'）。"""
    session = _pg_session()
    try:
        meta = dict(metadata or {})
        meta.setdefault("claim_id", claim_id)
        session.execute(
            sa_text("""
                INSERT INTO theory_review_events
                (entity_type, entity_id, old_status, new_status, changed_by, metadata)
                VALUES (:etype, :eid, :old, :new, CAST(:uid AS uuid), CAST(:meta AS jsonb))
            """),
            {
                "etype": ENTITY_ITEM,
                "eid": item_id,
                "old": old_status or "",
                "new": new_status or "",
                "uid": user_id or None,
                "meta": json.dumps(meta, ensure_ascii=False),
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        logger.warning("Failed to record recon item event for %s", item_id, exc_info=True)
    finally:
        session.close()
