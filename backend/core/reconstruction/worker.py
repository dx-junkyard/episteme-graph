"""item オーサリング worker（tension worker と同じ threading.Thread 方式）。

- トリガー: claim の承認時（theory_components の claim PATCH フック）/ 手動バッチ API。
- 冪等性: claim_id に非 retired の既存 item があればスキップ。同じ文書のオーサリングは
  プロセス内で直列化し、INSERT 時にも advisory lock 下で存在を再確認する（IK-0480 —
  claim 承認のたびに起動する thread が並走して同じ claim に 2 件作っていた）。
  同じ本文・親子関係の claim には 1 件だけ作る（atomic child を優先）。
- 対象外: claim_type='unknown' と短い否定文（問いに答えが入る, IK-0482）。理由別件数は
  ``last_authoring_report(document_id)`` とログに残す（行は消さない）。
- 入力: live 行の空欄（出典文・節見出し・式・親の概念）を同じ文書の残りの構造から
  決定論で補う（IK-0479, claim_context.enrich_claim）。
- コスト上限: 1 ドキュメントあたり RECON_MAX_ITEMS_PER_DOCUMENT（既定 30）、
  1 日あたり RECON_MAX_CALLS_PER_DAY（既定 10、他機能と独立）。モデルは fast tier 既定。
- LLM を使うのはここ（オーサリング）のみ。実行時 DIFF は非LLM（P6）。

core に属するため api/services.py に依存しない（永続化はここで直接 SQL を発行する）。
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from sqlalchemy import text as sa_text

from core.config import get_settings
from core.llm_usage.context import bind_usage_context
from core.llm_worker.cost_gate import CostGate, today_str
from core.postgres import get_session as _pg_session
from core.reconstruction.claim_context import (
    enrich_claim,
    normalize_claim_text,
    select_authoring_targets,
)
from core.reconstruction.derivation_source import collect_derivation_probes
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
from core.reconstruction.system import SYSTEM

# derivation_source が生成する非LLM モード（CostGate/LLM 日次上限の対象外。symbol と同じ扱い）。
_DERIVATION_ELICIT_MODES = ("regime", "next_step")

logger = logging.getLogger(__name__)

# 実装は core/llm_worker/cost_gate.py の CostGate（core/reconstruction/system.py の
# WorkerSystem が1個だけ持つ。daily のみ・session 上限は使わない）。
# daily_call_counts は同じ dict オブジェクトへのエイリアス。
_cost_gate: CostGate = SYSTEM.gate
_daily_call_counts: dict[str, int] = _cost_gate.daily_counts

_today = today_str

# 文書ごとのオーサリングの直列化（IK-0480）。同じ文書に対して承認フックが続けて
# thread を起こしても、候補の読み出し〜保存が重ならない。
_document_locks: dict[str, threading.Lock] = {}
_document_locks_guard = threading.Lock()

# 直近の LLM オーサリングの結果（文書ごと。created と除外理由別の件数）。
# 監視用の読み取り口で、判定には使わない。
_last_reports: dict[str, dict] = {}

# 候補の読み出し上限（除外・重複排除のあとで RECON_MAX_ITEMS_PER_DOCUMENT の残余に切る）。
_AUTHORABLE_FETCH_CAP = 2000


def _document_lock(document_id: str) -> threading.Lock:
    with _document_locks_guard:
        lock = _document_locks.get(document_id)
        if lock is None:
            lock = threading.Lock()
            _document_locks[document_id] = lock
        return lock


def last_authoring_report(document_id: str) -> dict:
    """直近のオーサリング結果（``{"created": int, "skipped": {reason: count}}``）。"""
    return dict(_last_reports.get(str(document_id or ""), {}))


def _check_and_count_llm_call() -> bool:
    """1 日あたりのオーサリング LLM コール上限内なら True。上限超過なら False。

    上限値の正本は core/reconstruction/system.py の CostSpec
    （recon_max_calls_per_day を settings から読む）。
    """
    return SYSTEM.check_and_count(gate=_cost_gate, settings=get_settings())


def _fetch_authorable_claims(session, document_id: str, limit: int) -> list[dict]:
    """source_backed + 承認済み、かつ非 retired の item を持たない claim を返す。

    記号葉プローブ（elicit_mode='symbol'、descend 時に非LLMで生成）と derivation
    プローブ（elicit_mode='regime'/'next_step'、非LLM・derivation_source.py 生成）は
    「その claim はオーサリング済み」とは数えない（いずれも predict/restate の
    LLM オーサリング対象とは独立した出題系のため）。
    """
    approved = list(APPROVED_REVIEW_STATUSES)
    rows = session.execute(
        sa_text("""
            SELECT c.id::text, c.document_id::text, c.claim_type, c.text, c.normalized_text,
                   c.concepts, c.equation, c.source_scope, c.evidence_text,
                   c.support_status, c.review_status,
                   c.parent_claim_id::text, c.agent_claim_id, c.chunk_id::text
            FROM theory_claims_live c
            WHERE c.document_id = :doc
              AND c.support_status = :backed
              AND c.review_status = ANY(:approved)
              AND NOT EXISTS (
                  SELECT 1 FROM reconstruction_items i
                  WHERE i.claim_id = c.id AND i.status <> 'retired'
                    AND i.elicit_mode NOT IN ('symbol', 'regime', 'next_step')
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
            "parent_claim_id": r[11] or "",
            "agent_claim_id": r[12] or "",
            "chunk_id": r[13] or "",
        })
    return claims


def _fetch_authored_claims(session, document_id: str) -> list[dict]:
    """同じ文書で既に predict/restate の item を持つ live claim（重複判定用）。"""
    rows = session.execute(
        sa_text("""
            SELECT c.id::text, c.parent_claim_id::text, c.text
            FROM theory_claims_live c
            WHERE c.document_id = :doc
              AND EXISTS (
                  SELECT 1 FROM reconstruction_items i
                  WHERE i.claim_id = c.id AND i.status <> 'retired'
                    AND i.elicit_mode NOT IN ('symbol', 'regime', 'next_step')
              )
        """),
        {"doc": document_id},
    ).fetchall()
    return [{"id": r[0], "parent_claim_id": r[1] or "", "text": r[2] or ""} for r in rows]


def _enrich_claims(session, document_id: str, claims: list[dict]) -> list[dict]:
    """live 行の空欄を同じ文書の構造から補う（IK-0479）。失敗しても元の claim を返す。

    補う材料（いずれも同じ document・live 行のみ）:
    親 claim（atomic rewrite の元文・概念・式）/ 同じ block の逐語引用
    （knowledge_evidence）/ 出典チャンクの節見出し（chunks.source_metadata）/
    claim を指す式（knowledge_equations.linked_claim_ids）。
    """
    if not claims:
        return claims
    try:
        parent_ids = sorted({c["parent_claim_id"] for c in claims if c.get("parent_claim_id")})
        parents: dict[str, dict] = {}
        if parent_ids:
            for r in session.execute(
                sa_text("""
                    SELECT id::text, text, concepts, equation, evidence_text, agent_claim_id
                    FROM theory_claims_live
                    WHERE id::text = ANY(:ids)
                """),
                {"ids": parent_ids},
            ).fetchall():
                parents[r[0]] = {
                    "id": r[0], "text": r[1] or "", "concepts": _json(r[2], []),
                    "equation": _json(r[3], {}), "evidence_text": r[4] or "",
                    "agent_claim_id": r[5] or "",
                }
        block_ids = sorted({
            str((c.get("source_scope") or {}).get("block_id") or "")
            for c in claims if isinstance(c.get("source_scope"), dict)
        } - {""})
        evidences: dict[str, list[dict]] = {}
        if block_ids:
            for r in session.execute(
                sa_text("""
                    SELECT block_id, evidence_role, evidence_text
                    FROM knowledge_evidence
                    WHERE document_id = :doc AND superseded_at IS NULL
                      AND block_id = ANY(:blocks)
                """),
                {"doc": document_id, "blocks": block_ids},
            ).fetchall():
                evidences.setdefault(r[0] or "", []).append(
                    {"evidence_role": r[1] or "", "evidence_text": r[2] or ""}
                )
        chunk_ids = sorted({c["chunk_id"] for c in claims if c.get("chunk_id")})
        sections: dict[str, str] = {}
        if chunk_ids:
            for r in session.execute(
                sa_text("""
                    SELECT id::text, COALESCE(source_metadata->>'section_title', section, '')
                    FROM chunks
                    WHERE id::text = ANY(:ids)
                """),
                {"ids": chunk_ids},
            ).fetchall():
                sections[r[0]] = str(r[1] or "").strip()
        equations = [
            {
                "agent_equation_id": r[0] or "", "label": r[1] or "", "latex": r[2] or "",
                "defined_symbols": _json(r[3], []), "linked_claim_ids": _json(r[4], []),
            }
            for r in session.execute(
                sa_text("""
                    SELECT agent_equation_id, label, latex, defined_symbols, linked_claim_ids
                    FROM knowledge_equations
                    WHERE document_id = :doc AND superseded_at IS NULL
                      AND jsonb_array_length(linked_claim_ids) > 0
                """),
                {"doc": document_id},
            ).fetchall()
        ]
    except Exception as exc:
        session.rollback()
        logger.warning("recon authoring enrichment failed for %s: %s", document_id, exc)
        return claims
    out: list[dict] = []
    for claim in claims:
        scope = claim.get("source_scope") if isinstance(claim.get("source_scope"), dict) else {}
        out.append(enrich_claim(
            claim,
            parent=parents.get(claim.get("parent_claim_id") or ""),
            evidences=evidences.get(str(scope.get("block_id") or ""), []),
            equations=equations,
            section_title=sections.get(claim.get("chunk_id") or "", ""),
        ))
    return out


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
    claim = dict(claim)
    for field in ("text", "normalized_text"):
        claim[field] = normalize_claim_text(claim.get(field))
    mode = preferred_elicit_mode(claim)
    content = build_instruction(mode) + "\n\n" + build_user_content(claim)
    return run_with_repair(client, content, claim)


def _persist_item(session, claim: dict, result: ItemAuthoringResult) -> str | None:
    """LLM オーサリングの item を保存する。既に非 retired の item があれば保存しない。

    同じ claim への並走 INSERT を claim 単位の advisory lock で直列化し、その下で
    存在を確かめる（IK-0480）。保存しなかったときは None を返す。
    """
    session.execute(
        sa_text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": "recon_item:" + str(claim["id"])},
    )
    row = session.execute(
        sa_text("""
            INSERT INTO reconstruction_items
            (claim_id, document_id, elicit_mode, prompt, response_space, expected,
             claim_fields_used, author, author_confidence, status)
            SELECT CAST(:claim_id AS uuid), CAST(NULLIF(:document_id, '') AS uuid), :elicit_mode, :prompt,
                   CAST(:response_space AS jsonb), CAST(:expected AS jsonb),
                   CAST(:claim_fields_used AS jsonb), 'llm', :author_confidence, 'auto'
            WHERE NOT EXISTS (
                SELECT 1 FROM reconstruction_items i
                WHERE i.claim_id = CAST(:claim_id AS uuid) AND i.status <> 'retired'
                  AND i.elicit_mode NOT IN ('symbol', 'regime', 'next_step')
            )
            RETURNING id::text
        """),
        {
            "claim_id": claim["id"],
            "document_id": claim.get("document_id") or "",
            "elicit_mode": result.elicit_mode,
            # 問い文にも PDF の行末ハイフネーションを残さない（IK-0481）。
            "prompt": normalize_claim_text(result.prompt),
            "response_space": json.dumps(response_options_to_dicts(result.response_space), ensure_ascii=False),
            "expected": json.dumps(result.expected or {}, ensure_ascii=False),
            "claim_fields_used": json.dumps(result.claim_fields_used or [], ensure_ascii=False),
            "author_confidence": float(result.author_confidence),
        },
    ).fetchone()
    return str(row[0]) if row else None


def _persist_derivation_item(session, probe: dict) -> str | None:
    """derivation_source.py が組み立てた probe（regime/next_step）を永続化する。

    `_persist_item`（LLM オーサリング専用・author を llm に固定）とは別の専用 INSERT。
    author は常に 'system'（非LLM・決定論生成）。
    """
    row = session.execute(
        sa_text("""
            INSERT INTO reconstruction_items
            (claim_id, document_id, elicit_mode, prompt, response_space, expected,
             claim_fields_used, author, author_confidence, status)
            VALUES (CAST(:claim_id AS uuid), CAST(NULLIF(:document_id, '') AS uuid), :elicit_mode, :prompt,
                    CAST(:response_space AS jsonb), CAST(:expected AS jsonb),
                    CAST(:claim_fields_used AS jsonb), 'system', :author_confidence, 'auto')
            RETURNING id::text
        """),
        {
            "claim_id": probe.get("claim_uuid") or "",
            "document_id": probe.get("document_id") or "",
            "elicit_mode": probe.get("elicit_mode") or "",
            "prompt": probe.get("prompt") or "",
            "response_space": json.dumps(probe.get("response_space") or [], ensure_ascii=False),
            "expected": json.dumps(probe.get("expected") or {}, ensure_ascii=False),
            "claim_fields_used": json.dumps(probe.get("claim_fields_used") or [], ensure_ascii=False),
            "author_confidence": float(probe.get("author_confidence") or 0.0),
        },
    ).fetchone()
    return str(row[0]) if row else None


def run_derivation_item_authoring_for_document(document_id: str) -> int:
    """derivation_chain artifact から regime / next_step item を非LLMで生成する。

    CostGate（LLM 日次上限）は通さない（symbol プローブと同じ扱い。§Phase2 裁定）。
    冪等性: (claim_id, elicit_mode) につき非 retired item を最大1件に保つ（既存があれば
    スキップ。symbol の再利用パターンと同型）。
    """
    doc_id = str(document_id or "").strip()
    if not doc_id:
        return 0
    try:
        probes = collect_derivation_probes(doc_id)
    except Exception as exc:
        logger.warning("recon derivation probe collection failed for %s: %s", doc_id, exc)
        return 0
    if not probes:
        return 0

    created = 0
    for probe in probes:
        claim_id = str(probe.get("claim_uuid") or "")
        mode = str(probe.get("elicit_mode") or "")
        if not claim_id or mode not in _DERIVATION_ELICIT_MODES:
            continue
        session = _pg_session()
        try:
            existing = session.execute(
                sa_text("""
                    SELECT 1 FROM reconstruction_items
                    WHERE claim_id = CAST(:cid AS uuid) AND elicit_mode = :mode
                      AND status <> 'retired'
                    LIMIT 1
                """),
                {"cid": claim_id, "mode": mode},
            ).fetchone()
            if existing:
                continue
            item_id = _persist_derivation_item(session, probe)
            session.commit()
        except Exception as exc:
            session.rollback()
            logger.warning("recon derivation item persist failed for claim %s: %s", claim_id, exc)
            continue
        finally:
            session.close()
        if item_id:
            created += 1
            _record_item_event(item_id, claim_id, "", "auto", None, {"author": "system", "mode": mode})
    return created


def run_item_authoring_for_document(document_id: str) -> int:
    """1 ドキュメント分の未オーサリング claim に item を生成する（LLM + 非LLM derivation）。

    LLM オーサリング（predict/restate）は `_run_llm_item_authoring_for_document` に
    委譲し、その戻り値に非LLM derivation オーサリング（regime/next_step、
    `run_derivation_item_authoring_for_document`）の生成数を合算する。derivation
    オーサリングは LLM 側の累積上限（RECON_MAX_ITEMS_PER_DOCUMENT）や日次上限の
    早期リターンに左右されず、claim 承認時トリガー / 手動バッチ API の両方から
    毎回 best-effort で試みる（失敗しても LLM オーサリングの結果は損なわない）。
    """
    created = _run_llm_item_authoring_for_document(document_id)
    derivation_created = 0
    try:
        derivation_created = run_derivation_item_authoring_for_document(document_id)
    except Exception as exc:
        logger.warning("recon derivation item authoring failed for %s: %s", document_id, exc)
    return created + derivation_created


def _run_llm_item_authoring_for_document(document_id: str) -> int:
    """1 ドキュメント分の未オーサリング claim に LLM で item を生成する。戻り値は生成数。

    RECON_MAX_ITEMS_PER_DOCUMENT は**累積**上限（1回の実行あたりではない）。
    既存の非 retired item 数を差し引いた残余分だけ新規オーサリングする。
    記号葉プローブ（非LLM・descend 由来）と derivation プローブ（非LLM・regime/next_step）は
    LLM コスト制御の対象外なので数えない。

    同じ文書に対する実行はプロセス内で直列化する（IK-0480）。対象の選別
    （unknown・短い否定文・同じ本文・親子の重複の除外）は
    ``claim_context.select_authoring_targets``。
    """
    if not document_id:
        return 0
    document_id = str(document_id)
    with _document_lock(document_id):
        bind_usage_context("admin:reconstruction_authoring", document_id=str(document_id))
        max_items = int(getattr(get_settings(), "recon_max_items_per_document", 30))
        session = _pg_session()
        try:
            existing = session.execute(
                sa_text("""
                    SELECT COUNT(*) FROM reconstruction_items
                    WHERE document_id = :doc AND status <> 'retired'
                      AND elicit_mode NOT IN ('symbol', 'regime', 'next_step')
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
            candidates = _fetch_authorable_claims(session, document_id, _AUTHORABLE_FETCH_CAP)
            authored = _fetch_authored_claims(session, document_id) if candidates else []
            claims, skipped = select_authoring_targets(candidates, authored=authored, limit=remaining)
            claims = _enrich_claims(session, document_id, claims)
        except Exception as exc:
            logger.warning("recon authoring fetch failed for %s: %s", document_id, exc)
            return 0
        finally:
            session.close()

        _last_reports[document_id] = {"created": 0, "skipped": dict(skipped)}
        if skipped:
            logger.info("recon item authoring excluded claims: document=%s reasons=%s", document_id, skipped)
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
            else:
                report_skipped = _last_reports[document_id]["skipped"]
                report_skipped["already_authored"] = report_skipped.get("already_authored", 0) + 1
        _last_reports[document_id]["created"] = created
        logger.info("recon item authoring done: document=%s created=%d", document_id, created)
        return created


def maybe_schedule_item_authoring(document_id: str) -> bool:
    """バックグラウンドスレッドでオーサリングを起動する（best-effort・非同期）。"""
    if not document_id:
        return False
    return SYSTEM.spawn(
        run_item_authoring_for_document,
        thread_factory=threading.Thread,
        args=(document_id,),
    )


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
