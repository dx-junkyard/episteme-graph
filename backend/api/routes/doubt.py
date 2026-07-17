"""D層（Doubt Layer）API — 認識的地位台帳・暗黙前提・疑義・検証提案・反実仮想。

実パス:
  - 教員向け: /api/admin/doubt/...（admin router 経由, TEACHER 以上）
  - 学習者向け（読み取り専用）: /api/learning/courses/{course_id}/ledger/... 等

設計原則（docs/features/doubt_layer_issues.md §0）:
  - 記帳・確定・疑義・提案はすべて帰属つきの正式な学術行為として
    theory_review_events に監査記録する（匿名疑義なし）。
  - `directly_verified` への昇格はスコープ 1 件以上のときのみ許可
    （全称検証の構造的禁止 → 422）。
  - LLM 候補（scope_candidates / assumption candidate）は教員の確定なしに
    本体（verification_scopes / confirmed）へ入らない。
  - dismiss / withdraw は行削除でなく status 遷移（P4）。
  - load_score・confidence の**生数値はレスポンスに含めない**（段階ラベルのみ。
    DX-1 の契約テスト対象）。
  - 「空欄」はエラーではなく事実。サマリ・表示文言も欠陥扱いにしない。
"""

from __future__ import annotations

import datetime
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text as sa_text

from dependencies import _get_current_user, _require_system_admin, _require_teacher
from core.doubt.assumption_mining.corpus_audit import run_corpus_audit
from core.doubt.assumption_mining.worker import maybe_schedule_assumption_mining
from core.doubt.counterfactual import compute_counterfactual, snapshot_subgraphs
from core.doubt.load_calculator import load_percentiles, recompute_load_scores
from core.doubt.metrics import collect_doubt_metrics
from core.doubt.naive_signal import aggregate_naive_signals, has_naive_signal
from core.doubt.open_assumptions import compile_open_assumptions, target_label
from core.doubt.schema import (
    ChallengeStatus,
    ChallengeType,
    TargetType,
    VerificationStatus,
    load_level_for_score,
    scope_coverage_level,
)
from core.doubt.scope_candidates.worker import maybe_schedule_scope_candidates
from core.postgres import get_session as _pg_session
from core.status import cross_layer_notify
from core.schema import (
    AUDIT_ENTITY_ASSUMPTION,
    AUDIT_ENTITY_CHALLENGE,
    AUDIT_ENTITY_COUNTERFACTUAL_SESSION,
    AUDIT_ENTITY_LEDGER,
    AUDIT_ENTITY_VERIFICATION_PROPOSAL,
)
from services import get_accessible_course_data, record_review_event

logger = logging.getLogger(__name__)

# admin.router（prefix=/api/admin）に include される
admin_router = APIRouter(prefix="/doubt", tags=["Doubt Layer"])
# main.py で直接 include される学習者向け読み取り専用 router
learning_router = APIRouter(prefix="/api/learning", tags=["Learning"])

_LEDGER_TARGET_TYPES = tuple(t.value for t in TargetType)
_VERIFICATION_STATUSES = tuple(s.value for s in VerificationStatus)
_CHALLENGE_TARGET_TYPES = ("assumption", "claim")
_CHALLENGE_TYPES = tuple(t.value for t in ChallengeType)
_SHARED_SCOPES = ("private", "group", "public")

_VERIFICATION_STATUS_LABELS = {
    "directly_verified": "直接検証の記帳あり",
    "indirectly_supported": "間接的な支持あり",
    "untested": "未検証",
    "refuted": "反証の記帳あり",
    "unknown": "検証情報なし",
}


# ---------------------------------------------------------------------------
# 共通ヘルパ
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _record_doubt_event(
    entity_type: str,
    entity_id: str,
    old_status: str,
    new_status: str,
    user_id: str | None,
    metadata: dict | None = None,
) -> None:
    """theory_review_events への監査記録（実体は services.record_review_event, 提案7）。"""
    record_review_event(entity_type, entity_id, old_status, new_status, user_id, metadata)


def _require_ledger_target_type(target_type: str) -> str:
    if target_type not in _LEDGER_TARGET_TYPES:
        raise HTTPException(status_code=422, detail=f"invalid target_type: {target_type}")
    return target_type


def _jsonb_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def _jsonb_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _fetch_ledger_row(session, target_type: str, target_id: str):
    return session.execute(
        sa_text("""
            SELECT id::text, target_id, target_type, document_id, course_id,
                   verification_status, verification_scopes, scope_candidates,
                   consensus_explicit, consensus_behavioral, load_score,
                   created_at, updated_at
            FROM epistemic_ledger
            WHERE target_id = :tid AND target_type = :ttype
        """),
        {"tid": target_id, "ttype": target_type},
    ).fetchone()


def _get_or_create_ledger_row(session, target_type: str, target_id: str):
    row = _fetch_ledger_row(session, target_type, target_id)
    if row is not None:
        return row
    session.execute(
        sa_text("""
            INSERT INTO epistemic_ledger (target_id, target_type)
            VALUES (:tid, :ttype)
            ON CONFLICT (target_id, target_type) DO NOTHING
        """),
        {"tid": target_id, "ttype": target_type},
    )
    session.commit()
    return _fetch_ledger_row(session, target_type, target_id)


def _scope_out(scope: dict) -> dict:
    """スコープの API 表現（confidence 等の生数値を持たない）。"""
    return {
        "scope_id": str(scope.get("scope_id") or ""),
        "condition": str(scope.get("condition") or ""),
        "domain": str(scope.get("domain") or ""),
        "precision": str(scope.get("precision") or ""),
        "system": str(scope.get("system") or ""),
        "evidence_ids": [str(e) for e in scope.get("evidence_ids") or []],
        "evidence_quote": str(scope.get("evidence_quote") or ""),
        "recorded_by": str(scope.get("recorded_by") or ""),
        "reason": str(scope.get("reason") or ""),
        "recorded_at": str(scope.get("recorded_at") or ""),
    }


def _candidate_out(candidate: dict) -> dict:
    """LLM スコープ候補の API 表現。confidence の生値は返さない（DX-1）。"""
    return {
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "condition": str(candidate.get("condition") or ""),
        "domain": str(candidate.get("domain") or ""),
        "precision": str(candidate.get("precision") or ""),
        "system": str(candidate.get("system") or ""),
        "evidence_quote": str(candidate.get("evidence_quote") or ""),
        "reason": str(candidate.get("reason") or ""),
        "status": str(candidate.get("status") or "candidate"),
    }


def _endorsement_label(consensus_explicit: dict) -> str:
    """明示的合意の段階ラベル（C層の「専門家N名が承認」と同型）。"""
    count = int(consensus_explicit.get("endorser_count") or 0)
    if count <= 0:
        return ""
    return f"教員{count}名が承認"


def _challenge_target_recorder(target_type: str, target_id: str) -> str | None:
    """疑義対象を記帳・確定した教員 id（横断インボックス fan-out の宛先解決, N14）。

    対象種別ごとに「最も自然な記帳者」を辿る:
      - assumption: `assumption_nodes.confirmed_by`（未確定なら手動作成者 `created_by` に
        フォールバック——どちらも本人が記帳する行為に相当する）。
      - claim: `epistemic_ledger.verification_scopes`（JSONB 配列）の直近エントリの
        `recorded_by`。スコープ 0 件（空欄）は D層の正常状態（§0「空欄はエラーではなく
        事実」）なので、その場合は None を返し通知しない（宛先不在は fail-closed）。
    """
    session = _pg_session()
    try:
        if target_type == "assumption":
            row = session.execute(
                sa_text(
                    "SELECT confirmed_by::text, created_by::text FROM assumption_nodes "
                    "WHERE id::text = :aid"
                ),
                {"aid": target_id},
            ).fetchone()
            if not row:
                return None
            return row[0] or row[1] or None
        if target_type == "claim":
            row = session.execute(
                sa_text(
                    "SELECT verification_scopes FROM epistemic_ledger "
                    "WHERE target_type = 'claim' AND target_id = :tid"
                ),
                {"tid": target_id},
            ).fetchone()
            if not row or not row[0]:
                return None
            scopes = row[0] if isinstance(row[0], list) else json.loads(row[0] or "[]")
            for scope in reversed(scopes):
                recorded_by = str((scope or {}).get("recorded_by") or "").strip()
                if recorded_by:
                    return recorded_by
            return None
        return None
    finally:
        session.close()


def _load_level_for_row(session, course_id: str, load_score: Any) -> str:
    if load_score is None:
        return ""
    if not course_id:
        return ""
    p50, p90, p99 = load_percentiles(session, course_id)
    return load_level_for_score(float(load_score), p50, p90, p99)


# ---------------------------------------------------------------------------
# D1-3 / D1-4: 台帳の閲覧・記帳
# ---------------------------------------------------------------------------


class ScopeCreateRequest(BaseModel):
    condition: str = ""
    domain: str = ""
    precision: str = ""
    system: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_quote: str = ""
    reason: str = ""


class ScopePatchRequest(BaseModel):
    condition: str | None = None
    domain: str | None = None
    precision: str | None = None
    system: str | None = None
    evidence_ids: list[str] | None = None
    evidence_quote: str | None = None
    reason: str | None = None


class VerificationStatusRequest(BaseModel):
    verification_status: str
    reason: str = ""


@admin_router.get("/ledger/{target_type}/{target_id}")
def get_ledger_entry(
    target_type: str,
    target_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """台帳行 + スコープ配列 + 出所 + 合意の 2 軸 + 学習者シグナル + 疑義。"""
    _require_ledger_target_type(target_type)
    session = _pg_session()
    try:
        row = _fetch_ledger_row(session, target_type, target_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Ledger entry not found")
        scopes = [_scope_out(s) for s in _jsonb_list(row[6]) if isinstance(s, dict)]
        candidates = [
            _candidate_out(c) for c in _jsonb_list(row[7])
            if isinstance(c, dict) and str(c.get("status") or "candidate") == "candidate"
        ]
        consensus_explicit = _jsonb_dict(row[8])
        course_id = str(row[4] or "")

        challenge_rows = session.execute(
            sa_text("""
                SELECT c.id::text, c.challenge_type, c.reason, c.status,
                       COALESCE(u.display_name, ''), c.created_at
                FROM challenges c
                LEFT JOIN users u ON u.id = c.challenger_id
                WHERE c.target_type = :ttype AND c.target_id = :tid
                ORDER BY c.created_at ASC
            """),
            {"ttype": target_type, "tid": target_id},
        ).fetchall() if target_type in _CHALLENGE_TARGET_TYPES else []

        return {
            "target_id": str(row[1]),
            "target_type": str(row[2]),
            "target_label": target_label(session, target_type, target_id),
            "document_id": str(row[3] or ""),
            "course_id": course_id,
            "verification_status": str(row[5] or "unknown"),
            "verification_status_label": _VERIFICATION_STATUS_LABELS.get(
                str(row[5] or "unknown"), str(row[5] or "unknown")
            ),
            "verification_scopes": scopes,
            "scope_coverage": scope_coverage_level(len(scopes)),
            "scope_candidates": candidates,
            # 合意の 2 軸は検証とは別フィールド（混同不可能に）
            "consensus_explicit_label": _endorsement_label(consensus_explicit),
            "consensus_behavioral_dependents": int(row[9] or 0),
            "load_level": _load_level_for_row(session, course_id, row[10]),
            "naive_signal": {
                "present": has_naive_signal(target_type, target_id, course_id)
                if target_type in ("claim", "equation", "component") else False,
            },
            "challenges": [
                {
                    "id": str(c[0]),
                    "challenge_type": str(c[1]),
                    "reason": str(c[2]),
                    "status": str(c[3]),
                    "challenger_name": str(c[4]),
                }
                for c in challenge_rows
            ],
            "updated_at": str(row[12] or ""),
        }
    finally:
        session.close()


@admin_router.post("/ledger/{target_type}/{target_id}/scopes")
def add_ledger_scope(
    target_type: str,
    target_id: str,
    body: ScopeCreateRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """検証スコープの記帳（帰属・根拠つきの正式な学術行為）。

    必須: 4 軸のうち 1 つ以上 + 根拠（evidence_ids または evidence_quote）+ reason。
    """
    _require_ledger_target_type(target_type)
    axes = [body.condition.strip(), body.domain.strip(), body.precision.strip(), body.system.strip()]
    if not any(axes):
        raise HTTPException(
            status_code=422,
            detail="condition / domain / precision / system のうち 1 つ以上が必要です",
        )
    if not body.evidence_ids and not body.evidence_quote.strip():
        raise HTTPException(status_code=422, detail="根拠（evidence_ids または evidence_quote）が必要です")
    if not body.reason.strip():
        raise HTTPException(status_code=422, detail="reason が必要です")

    scope = {
        "scope_id": str(uuid.uuid4()),
        "condition": body.condition.strip(),
        "domain": body.domain.strip(),
        "precision": body.precision.strip(),
        "system": body.system.strip(),
        "evidence_ids": [str(e) for e in body.evidence_ids],
        "evidence_quote": body.evidence_quote.strip(),
        "recorded_by": str(current_user.get("id") or ""),
        "reason": body.reason.strip(),
        "recorded_at": _now_iso(),
    }
    session = _pg_session()
    try:
        row = _get_or_create_ledger_row(session, target_type, target_id)
        if row is None:
            raise HTTPException(status_code=500, detail="Failed to create ledger entry")
        session.execute(
            sa_text("""
                UPDATE epistemic_ledger
                SET verification_scopes = verification_scopes || CAST(:scope AS jsonb),
                    updated_at = now()
                WHERE target_id = :tid AND target_type = :ttype
            """),
            {"scope": json.dumps([scope], ensure_ascii=False), "tid": target_id, "ttype": target_type},
        )
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        logger.exception("Failed to add scope for %s/%s", target_type, target_id)
        raise HTTPException(status_code=500, detail="Failed to add scope")
    finally:
        session.close()

    _record_doubt_event(
        AUDIT_ENTITY_LEDGER, f"{target_type}:{target_id}", "", "scope_added",
        current_user.get("id"),
        {"action": "scope_add", "scope_id": scope["scope_id"], "reason": scope["reason"]},
    )
    return {"ok": True, "scope": _scope_out(scope)}


@admin_router.patch("/ledger/{target_type}/{target_id}/scopes/{scope_id}")
def patch_ledger_scope(
    target_type: str,
    target_id: str,
    scope_id: str,
    body: ScopePatchRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """スコープの訂正（履歴は review_events に残す）。"""
    _require_ledger_target_type(target_type)
    session = _pg_session()
    try:
        row = _fetch_ledger_row(session, target_type, target_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Ledger entry not found")
        scopes = [s for s in _jsonb_list(row[6]) if isinstance(s, dict)]
        target_scope = None
        for scope in scopes:
            if str(scope.get("scope_id") or "") == scope_id:
                target_scope = scope
                break
        if target_scope is None:
            raise HTTPException(status_code=404, detail="Scope not found")

        old_scope = dict(target_scope)
        for field_name in ("condition", "domain", "precision", "system", "evidence_quote", "reason"):
            value = getattr(body, field_name)
            if value is not None:
                target_scope[field_name] = str(value).strip()
        if body.evidence_ids is not None:
            target_scope["evidence_ids"] = [str(e) for e in body.evidence_ids]

        # 訂正後も「1 軸 + 根拠 + 理由」の成立を保つ
        if not any(
            str(target_scope.get(k) or "").strip()
            for k in ("condition", "domain", "precision", "system")
        ):
            raise HTTPException(status_code=422, detail="スコープには 1 軸以上が必要です")
        if not target_scope.get("evidence_ids") and not str(target_scope.get("evidence_quote") or "").strip():
            raise HTTPException(status_code=422, detail="スコープには根拠が必要です")
        if not str(target_scope.get("reason") or "").strip():
            raise HTTPException(status_code=422, detail="スコープには reason が必要です")

        session.execute(
            sa_text("""
                UPDATE epistemic_ledger
                SET verification_scopes = CAST(:scopes AS jsonb), updated_at = now()
                WHERE target_id = :tid AND target_type = :ttype
            """),
            {"scopes": json.dumps(scopes, ensure_ascii=False), "tid": target_id, "ttype": target_type},
        )
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        logger.exception("Failed to patch scope for %s/%s", target_type, target_id)
        raise HTTPException(status_code=500, detail="Failed to patch scope")
    finally:
        session.close()

    _record_doubt_event(
        AUDIT_ENTITY_LEDGER, f"{target_type}:{target_id}", "scope_updated", "scope_updated",
        current_user.get("id"),
        {"action": "scope_patch", "scope_id": scope_id, "old": old_scope},
    )
    return {"ok": True, "scope": _scope_out(target_scope)}


@admin_router.put("/ledger/{target_type}/{target_id}/verification-status")
def put_verification_status(
    target_type: str,
    target_id: str,
    body: VerificationStatusRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """検証状態の変更。directly_verified への昇格はスコープ 1 件以上のときのみ
    （= 全称検証の構造的禁止, 422）。"""
    _require_ledger_target_type(target_type)
    new_status = str(body.verification_status or "").strip()
    if new_status not in _VERIFICATION_STATUSES:
        raise HTTPException(status_code=422, detail=f"invalid verification_status: {new_status}")

    session = _pg_session()
    try:
        row = _get_or_create_ledger_row(session, target_type, target_id)
        if row is None:
            raise HTTPException(status_code=500, detail="Failed to create ledger entry")
        old_status = str(row[5] or "unknown")
        scopes = [s for s in _jsonb_list(row[6]) if isinstance(s, dict)]
        if new_status == VerificationStatus.DIRECTLY_VERIFIED.value and len(scopes) < 1:
            raise HTTPException(
                status_code=422,
                detail="directly_verified への昇格には検証スコープの記帳が 1 件以上必要です"
                       "（スコープなしの全称検証は記帳できません）",
            )
        session.execute(
            sa_text("""
                UPDATE epistemic_ledger
                SET verification_status = :status, updated_at = now()
                WHERE target_id = :tid AND target_type = :ttype
            """),
            {"status": new_status, "tid": target_id, "ttype": target_type},
        )
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        logger.exception("Failed to update verification status for %s/%s", target_type, target_id)
        raise HTTPException(status_code=500, detail="Failed to update verification status")
    finally:
        session.close()

    _record_doubt_event(
        AUDIT_ENTITY_LEDGER, f"{target_type}:{target_id}", old_status, new_status,
        current_user.get("id"),
        {"action": "verification_status", "reason": body.reason or ""},
    )
    return {"ok": True, "verification_status": new_status}


@admin_router.post("/ledger/{target_type}/{target_id}/scope-candidates/{candidate_id}/confirm")
def confirm_scope_candidate(
    target_type: str,
    target_id: str,
    candidate_id: str,
    body: ScopePatchRequest = Body(default=None),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """LLM スコープ候補の確定。候補の値を **教員の帰属で** verification_scopes に転記する。

    候補行自体は昇格せず status='confirmed' で保持（確定なしに本体へ入らない構造）。
    """
    _require_ledger_target_type(target_type)
    session = _pg_session()
    try:
        row = _fetch_ledger_row(session, target_type, target_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Ledger entry not found")
        candidates = [c for c in _jsonb_list(row[7]) if isinstance(c, dict)]
        candidate = None
        for item in candidates:
            if str(item.get("candidate_id") or "") == candidate_id:
                candidate = item
                break
        if candidate is None:
            raise HTTPException(status_code=404, detail="Scope candidate not found")
        if str(candidate.get("status") or "candidate") != "candidate":
            raise HTTPException(status_code=422, detail="この候補は既に処理済みです")

        overrides = body or ScopePatchRequest()
        scope = {
            "scope_id": str(uuid.uuid4()),
            "condition": (overrides.condition if overrides.condition is not None
                          else str(candidate.get("condition") or "")).strip(),
            "domain": (overrides.domain if overrides.domain is not None
                       else str(candidate.get("domain") or "")).strip(),
            "precision": (overrides.precision if overrides.precision is not None
                          else str(candidate.get("precision") or "")).strip(),
            "system": (overrides.system if overrides.system is not None
                       else str(candidate.get("system") or "")).strip(),
            "evidence_ids": [str(e) for e in (overrides.evidence_ids or [])],
            "evidence_quote": (overrides.evidence_quote if overrides.evidence_quote is not None
                               else str(candidate.get("evidence_quote") or "")).strip(),
            # 確定時に帰属は教員に付く（LLM ではなく人間の記帳になる）
            "recorded_by": str(current_user.get("id") or ""),
            "reason": (overrides.reason if overrides.reason is not None
                       else str(candidate.get("reason") or "")).strip(),
            "recorded_at": _now_iso(),
            "from_candidate_id": candidate_id,
        }
        if not any(scope[k] for k in ("condition", "domain", "precision", "system")):
            raise HTTPException(status_code=422, detail="スコープには 1 軸以上が必要です")
        if not scope["evidence_ids"] and not scope["evidence_quote"]:
            raise HTTPException(status_code=422, detail="スコープには根拠が必要です")

        candidate["status"] = "confirmed"
        candidate["confirmed_by"] = str(current_user.get("id") or "")
        candidate["confirmed_at"] = _now_iso()
        session.execute(
            sa_text("""
                UPDATE epistemic_ledger
                SET verification_scopes = verification_scopes || CAST(:scope AS jsonb),
                    scope_candidates = CAST(:candidates AS jsonb),
                    updated_at = now()
                WHERE target_id = :tid AND target_type = :ttype
            """),
            {
                "scope": json.dumps([scope], ensure_ascii=False),
                "candidates": json.dumps(candidates, ensure_ascii=False),
                "tid": target_id,
                "ttype": target_type,
            },
        )
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        logger.exception("Failed to confirm scope candidate for %s/%s", target_type, target_id)
        raise HTTPException(status_code=500, detail="Failed to confirm scope candidate")
    finally:
        session.close()

    _record_doubt_event(
        AUDIT_ENTITY_LEDGER, f"{target_type}:{target_id}", "candidate", "scope_added",
        current_user.get("id"),
        {"action": "scope_candidate_confirm", "candidate_id": candidate_id, "scope_id": scope["scope_id"]},
    )
    return {"ok": True, "scope": _scope_out(scope)}


@admin_router.post("/ledger/{target_type}/{target_id}/scope-candidates/{candidate_id}/dismiss")
def dismiss_scope_candidate(
    target_type: str,
    target_id: str,
    candidate_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """LLM スコープ候補の却下（P4: 行は dismissed で保持）。"""
    _require_ledger_target_type(target_type)
    session = _pg_session()
    try:
        row = _fetch_ledger_row(session, target_type, target_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Ledger entry not found")
        candidates = [c for c in _jsonb_list(row[7]) if isinstance(c, dict)]
        found = False
        for candidate in candidates:
            if str(candidate.get("candidate_id") or "") == candidate_id:
                candidate["status"] = "dismissed"
                candidate["dismissed_by"] = str(current_user.get("id") or "")
                candidate["dismissed_at"] = _now_iso()
                found = True
                break
        if not found:
            raise HTTPException(status_code=404, detail="Scope candidate not found")
        session.execute(
            sa_text("""
                UPDATE epistemic_ledger
                SET scope_candidates = CAST(:candidates AS jsonb), updated_at = now()
                WHERE target_id = :tid AND target_type = :ttype
            """),
            {"candidates": json.dumps(candidates, ensure_ascii=False), "tid": target_id, "ttype": target_type},
        )
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        logger.exception("Failed to dismiss scope candidate for %s/%s", target_type, target_id)
        raise HTTPException(status_code=500, detail="Failed to dismiss scope candidate")
    finally:
        session.close()

    _record_doubt_event(
        AUDIT_ENTITY_LEDGER, f"{target_type}:{target_id}", "candidate", "dismissed",
        current_user.get("id"),
        {"action": "scope_candidate_dismiss", "candidate_id": candidate_id},
    )
    return {"ok": True, "candidate_id": candidate_id, "status": "dismissed"}


@admin_router.get("/courses/{course_id}/ledger-summary")
def get_ledger_summary(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """コース単位の台帳サマリ（内部/教員向けのみ）。

    空欄は欠陥ではなく事実として返す（文言・警告色にしないのは UI 側の責務）。
    """
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT target_type,
                       COUNT(*),
                       COUNT(*) FILTER (WHERE verification_scopes <> '[]'::jsonb),
                       COUNT(*) FILTER (WHERE verification_scopes = '[]'::jsonb)
                FROM epistemic_ledger
                WHERE course_id = :course
                GROUP BY target_type
            """),
            {"course": course_id},
        ).fetchall()
        by_type = {
            str(r[0]): {
                "targets": int(r[1] or 0),
                "scoped": int(r[2] or 0),
                "unscoped": int(r[3] or 0),
            }
            for r in rows
        }
        totals = {
            "targets": sum(v["targets"] for v in by_type.values()),
            "scoped": sum(v["scoped"] for v in by_type.values()),
            "unscoped": sum(v["unscoped"] for v in by_type.values()),
        }
        return {"course_id": course_id, "totals": totals, "by_type": by_type}
    finally:
        session.close()


@admin_router.post("/courses/{course_id}/scope-candidates/refresh")
def refresh_scope_candidates(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """スコープ候補の非同期リフレッシュ（P6: 同期パスに LLM を入れない）。"""
    maybe_schedule_scope_candidates(course_id=course_id)
    return {"ok": True, "scheduled": True}


# ---------------------------------------------------------------------------
# D1-5: 素朴な問いの計器化
# ---------------------------------------------------------------------------


@admin_router.get("/courses/{course_id}/naive-signals")
def get_naive_signals(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """anchor 単位の k-匿名集計（k=3, n<3 セル非表示、件数はレンジ表示のみ）。"""
    return aggregate_naive_signals(course_id)


# ---------------------------------------------------------------------------
# D2-1: 負荷度
# ---------------------------------------------------------------------------


@admin_router.post("/courses/{course_id}/load/recompute")
def recompute_course_load(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """負荷度の再計算（決定論的バッチ）。レスポンスに生スコアは含めない。"""
    result = recompute_load_scores(course_id=course_id)
    return {"ok": not result.get("failed", False), "updated": int(result.get("updated") or 0)}


# ---------------------------------------------------------------------------
# D2-2 / D2-4 / D3-5: 暗黙前提
# ---------------------------------------------------------------------------


class AssumptionCreateRequest(BaseModel):
    statement: str
    reason: str
    course_id: str = ""
    document_ids: list[str] = Field(default_factory=list)


class AssumptionConfirmRequest(BaseModel):
    statement: str = ""  # 教員が文面を訂正可
    reason: str = ""


def _assumption_out(row) -> dict:
    """assumption_nodes 行の API 表現（confidence の生値は返さない）。"""
    created_from = _jsonb_dict(row[5])
    return {
        "id": str(row[0]),
        "statement": str(row[1] or ""),
        "origin": str(row[2] or ""),
        "status": str(row[3] or ""),
        "cluster_key": str(row[4] or ""),
        "created_from": created_from,
        "evidence_quote": str(row[6] or ""),
        "reason": str(row[7] or ""),
        "course_id": str(row[8] or ""),
        "document_ids": _jsonb_list(row[9]),
        "created_at": str(row[10] or ""),
        "updated_at": str(row[11] or ""),
    }


_ASSUMPTION_SELECT = """
    SELECT id::text, statement, origin, status, cluster_key, created_from,
           evidence_quote, reason, course_id, document_ids, created_at, updated_at
    FROM assumption_nodes
"""


@admin_router.get("/courses/{course_id}/assumptions")
def list_assumptions(
    course_id: str,
    status: str = "candidate",
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """前提一覧（出所つき）。dismissed もフィルタで確認できる（P4: 消えない）。"""
    allowed = ("candidate", "confirmed", "operationalized", "dismissed", "all")
    if status not in allowed:
        raise HTTPException(status_code=422, detail=f"invalid status filter: {status}")
    session = _pg_session()
    try:
        filters = ["(course_id = :course OR course_id = '')"]
        params: dict[str, Any] = {"course": course_id}
        if status != "all":
            filters.append("status = :status")
            params["status"] = status
        rows = session.execute(
            sa_text(_ASSUMPTION_SELECT + f" WHERE {' AND '.join(filters)} ORDER BY created_at ASC"),
            params,
        ).fetchall()
        items = []
        for row in rows:
            item = _assumption_out(row)
            # 学習者シグナル（k-匿名の有無のみ）を 1 カードに合成（D2-4）
            node_ids = [str(n) for n in (item["created_from"].get("node_ids") or [])]
            item["has_naive_signal"] = any(
                has_naive_signal("component", nid, course_id) for nid in node_ids[:5]
            )
            items.append(item)
        return {"course_id": course_id, "status": status, "items": items}
    finally:
        session.close()


@admin_router.post("/assumptions")
def create_assumption_manual(
    body: AssumptionCreateRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """手動登録（origin='manual'）。教員の直接明示化なので即 confirmed になる。"""
    if not body.statement.strip():
        raise HTTPException(status_code=422, detail="statement が必要です")
    if not body.reason.strip():
        raise HTTPException(status_code=422, detail="reason が必要です")
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                INSERT INTO assumption_nodes
                    (statement, origin, status, reason, course_id, document_ids,
                     confirmed_by, confirmed_at, created_by)
                VALUES
                    (:statement, 'manual', 'confirmed', :reason, :course,
                     CAST(:docs AS jsonb), CAST(:uid AS uuid), now(), CAST(:uid AS uuid))
                RETURNING id
            """),
            {
                "statement": body.statement.strip(),
                "reason": body.reason.strip(),
                "course": body.course_id or "",
                "docs": json.dumps([str(d) for d in body.document_ids], ensure_ascii=False),
                "uid": current_user.get("id"),
            },
        ).fetchone()
        assumption_id = str(row[0])
        # 確定と同時に台帳行を自動生成（既定 untested・スコープ空欄）
        session.execute(
            sa_text("""
                INSERT INTO epistemic_ledger (target_id, target_type, course_id, verification_status)
                VALUES (:tid, 'assumption', :course, 'untested')
                ON CONFLICT (target_id, target_type) DO NOTHING
            """),
            {"tid": assumption_id, "course": body.course_id or ""},
        )
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        logger.exception("Failed to create manual assumption")
        raise HTTPException(status_code=500, detail="Failed to create assumption")
    finally:
        session.close()

    _record_doubt_event(
        AUDIT_ENTITY_ASSUMPTION, assumption_id, "", "confirmed",
        current_user.get("id"),
        {"action": "manual_create", "reason": body.reason.strip()},
    )
    return {"ok": True, "assumption_id": assumption_id, "status": "confirmed"}


@admin_router.post("/assumptions/{assumption_id}/confirm")
def confirm_assumption(
    assumption_id: str,
    body: AssumptionConfirmRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """候補の確定 = 「専門家が確かにこれは暗黙の前提だと引き受ける」行為。reason 必須。"""
    if not body.reason.strip():
        raise HTTPException(status_code=422, detail="reason が必要です")
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT status, statement, course_id FROM assumption_nodes WHERE id::text = :aid"),
            {"aid": assumption_id},
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Assumption not found")
        old_status = str(row[0] or "")
        if old_status != "candidate":
            raise HTTPException(status_code=422, detail=f"candidate 以外の前提は確定できません（現在: {old_status}）")
        statement = body.statement.strip() or str(row[1] or "")
        course_id = str(row[2] or "")
        session.execute(
            sa_text("""
                UPDATE assumption_nodes
                SET status = 'confirmed',
                    statement = :statement,
                    confirmed_by = CAST(:uid AS uuid),
                    confirmed_at = now(),
                    updated_at = now()
                WHERE id::text = :aid
            """),
            {"statement": statement, "uid": current_user.get("id"), "aid": assumption_id},
        )
        # 確定時に台帳行を自動生成（既定 untested・スコープ空欄）
        session.execute(
            sa_text("""
                INSERT INTO epistemic_ledger (target_id, target_type, course_id, verification_status)
                VALUES (:tid, 'assumption', :course, 'untested')
                ON CONFLICT (target_id, target_type) DO NOTHING
            """),
            {"tid": assumption_id, "course": course_id},
        )
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        logger.exception("Failed to confirm assumption %s", assumption_id)
        raise HTTPException(status_code=500, detail="Failed to confirm assumption")
    finally:
        session.close()

    _record_doubt_event(
        AUDIT_ENTITY_ASSUMPTION, assumption_id, "candidate", "confirmed",
        current_user.get("id"),
        {"action": "confirm", "reason": body.reason.strip(), "statement_edited": bool(body.statement.strip())},
    )
    return {"ok": True, "assumption_id": assumption_id, "status": "confirmed"}


@admin_router.post("/assumptions/{assumption_id}/dismiss")
def dismiss_assumption(
    assumption_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """候補の却下。dismissed で保持する（P4: 削除しない）。"""
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT status FROM assumption_nodes WHERE id::text = :aid"),
            {"aid": assumption_id},
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Assumption not found")
        old_status = str(row[0] or "")
        session.execute(
            sa_text("""
                UPDATE assumption_nodes
                SET status = 'dismissed',
                    dismissed_by = CAST(:uid AS uuid),
                    updated_at = now()
                WHERE id::text = :aid
            """),
            {"uid": current_user.get("id"), "aid": assumption_id},
        )
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        logger.exception("Failed to dismiss assumption %s", assumption_id)
        raise HTTPException(status_code=500, detail="Failed to dismiss assumption")
    finally:
        session.close()

    _record_doubt_event(
        AUDIT_ENTITY_ASSUMPTION, assumption_id, old_status, "dismissed",
        current_user.get("id"), {"action": "dismiss"},
    )
    return {"ok": True, "assumption_id": assumption_id, "status": "dismissed"}


@admin_router.post("/courses/{course_id}/assumption-mining/run")
def run_assumption_mining_route(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """経路A（導出の隙間の反転）の非同期実行。"""
    maybe_schedule_assumption_mining(course_id=course_id)
    return {"ok": True, "scheduled": True}


@admin_router.post("/courses/{course_id}/corpus-audit/run")
def run_corpus_audit_route(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """経路B（コーパス横断の監査）の実行（非LLM・同期でも軽量）。"""
    result = run_corpus_audit(course_id=course_id)
    return {
        "ok": not result.get("failed", False),
        "skipped": bool(result.get("skipped")),
        "documents": int(result.get("documents") or 0),
        "registered": int(result.get("registered") or 0),
    }


# ---------------------------------------------------------------------------
# D2-3: 前提の地図（Assumption Atlas）
# ---------------------------------------------------------------------------


@admin_router.get("/courses/{course_id}/assumption-atlas")
def get_assumption_atlas(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """負荷度×検証度の散布データ（評価抜きの事実の投影）。

    レスポンスに評価語・生スコア数値を含めない。空欄（スコープ 0 件）の点は
    unscoped=true（UI は塗りなし・点線輪郭で描く）。
    """
    session = _pg_session()
    try:
        p50, p90, p99 = load_percentiles(session, course_id)
        rows = session.execute(
            sa_text("""
                SELECT l.target_id, l.target_type, l.verification_status,
                       l.verification_scopes, l.consensus_explicit, l.load_score,
                       a.status
                FROM epistemic_ledger l
                LEFT JOIN assumption_nodes a
                  ON l.target_type = 'assumption' AND a.id::text = l.target_id
                WHERE l.course_id = :course
                  AND l.load_score IS NOT NULL
            """),
            {"course": course_id},
        ).fetchall()

        points = []
        for row in rows:
            target_type = str(row[1])
            assumption_status = str(row[6] or "")
            if target_type == "assumption":
                # 地図に載るのは confirmed / operationalized の前提のみ
                if assumption_status not in ("confirmed", "operationalized"):
                    continue
            load_level = load_level_for_score(
                float(row[5]) if row[5] is not None else None, p50, p90, p99
            )
            if target_type != "assumption" and load_level not in ("high", "highest"):
                # 前提以外は高負荷のもののみ（地図の焦点を保つ）
                continue
            scopes = [s for s in _jsonb_list(row[3]) if isinstance(s, dict)]
            consensus = _jsonb_dict(row[4])
            points.append({
                "target_id": str(row[0]),
                "target_type": target_type,
                "label": target_label(session, target_type, str(row[0])),
                "verification_status": str(row[2] or "unknown"),
                "scope_coverage": scope_coverage_level(len(scopes)),
                "unscoped": len(scopes) == 0,
                "load_level": load_level,
                "consensus_label": _endorsement_label(consensus),
                "has_naive_signal": (
                    has_naive_signal(target_type, str(row[0]), course_id)
                    if target_type in ("claim", "equation", "component") else False
                ),
            })
        points.sort(key=lambda p: (p["target_type"], p["target_id"]))
        return {
            "course_id": course_id,
            # 軸ラベルは事実記述のみ（ゾーン名・煽り文句を持たない）
            "axes": {"x": "検証スコープの被覆", "y": "依存の広がり"},
            "points": points,
        }
    finally:
        session.close()


# ---------------------------------------------------------------------------
# D3-1: 疑義（Challenge）
# ---------------------------------------------------------------------------


class ChallengeCreateRequest(BaseModel):
    challenge_type: str
    reason: str
    course_id: str = ""


@admin_router.post("/targets/{target_type}/{target_id}/challenges")
def create_challenge(
    target_type: str,
    target_id: str,
    body: ChallengeCreateRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """疑義の作成（型 + 理由必須・匿名不可）。"""
    if target_type not in _CHALLENGE_TARGET_TYPES:
        raise HTTPException(status_code=422, detail=f"invalid target_type: {target_type}")
    if body.challenge_type not in _CHALLENGE_TYPES:
        raise HTTPException(status_code=422, detail=f"invalid challenge_type: {body.challenge_type}")
    if not body.reason.strip():
        raise HTTPException(status_code=422, detail="reason（本人の言葉）が必要です")

    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                INSERT INTO challenges
                    (target_id, target_type, challenger_id, challenge_type, reason, course_id)
                VALUES
                    (:tid, :ttype, CAST(:uid AS uuid), :ctype, :reason, :course)
                RETURNING id
            """),
            {
                "tid": target_id,
                "ttype": target_type,
                "uid": current_user.get("id"),
                "ctype": body.challenge_type,
                "reason": body.reason.strip(),
                "course": body.course_id or "",
            },
        ).fetchone()
        session.commit()
        challenge_id = str(row[0])
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        logger.exception("Failed to create challenge for %s/%s", target_type, target_id)
        raise HTTPException(status_code=500, detail="Failed to create challenge")
    finally:
        session.close()

    _record_doubt_event(
        AUDIT_ENTITY_CHALLENGE, challenge_id, "", "open",
        current_user.get("id"),
        {"action": "create", "target_type": target_type, "target_id": target_id,
         "challenge_type": body.challenge_type},
    )
    # 横断インボックス fan-out（N14, best-effort）: 対象を記帳・確定した教員へ「疑義が
    # 起票された」通知。主語は疑義の型（人格対立にしない）。起票者本人が記帳者なら
    # 通知しない（自己起票への通知は無意味）。
    try:
        recorder_id = _challenge_target_recorder(target_type, target_id)
        actor_id = str(current_user.get("id") or "")
        if recorder_id and recorder_id != actor_id:
            cross_layer_notify.notify_user(
                recorder_id,
                cross_layer_notify.NOTIF_LEDGER_TARGET_CHALLENGED,
                target_type,
                target_id,
                {"challenge_id": challenge_id, "challenge_type": body.challenge_type},
            )
    except Exception:  # noqa: BLE001 — 通知失敗は疑義の起票そのものを止めない
        logger.debug("cross-layer notify (challenge) skipped for %s/%s", target_type, target_id, exc_info=True)
    return {"ok": True, "challenge_id": challenge_id, "status": "open"}


@admin_router.get("/targets/{target_type}/{target_id}/challenges")
def list_challenges(
    target_type: str,
    target_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """疑義の一覧（解釈の並存と同様の併記表示用）。数値スコア化しない。"""
    if target_type not in _CHALLENGE_TARGET_TYPES:
        raise HTTPException(status_code=422, detail=f"invalid target_type: {target_type}")
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT c.id::text, c.challenge_type, c.reason, c.status,
                       COALESCE(u.display_name, ''), c.created_at::text
                FROM challenges c
                LEFT JOIN users u ON u.id = c.challenger_id
                WHERE c.target_type = :ttype AND c.target_id = :tid
                ORDER BY c.created_at ASC
            """),
            {"ttype": target_type, "tid": target_id},
        ).fetchall()
        return {
            "target_type": target_type,
            "target_id": target_id,
            "items": [
                {
                    "id": str(r[0]),
                    "challenge_type": str(r[1]),
                    "reason": str(r[2]),
                    "status": str(r[3]),
                    "challenger_name": str(r[4]),
                    "created_at": str(r[5]),
                }
                for r in rows
            ],
        }
    finally:
        session.close()


@admin_router.post("/challenges/{challenge_id}/withdraw")
def withdraw_challenge(
    challenge_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """疑義の取り下げ（行削除ではなく status 遷移・履歴保持, P4）。本人のみ。"""
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT challenger_id::text, status FROM challenges WHERE id::text = :cid"),
            {"cid": challenge_id},
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Challenge not found")
        if str(row[0]) != str(current_user.get("id")):
            raise HTTPException(status_code=403, detail="疑義の取り下げは本人のみ可能です")
        old_status = str(row[1] or "open")
        session.execute(
            sa_text("""
                UPDATE challenges SET status = 'withdrawn', updated_at = now()
                WHERE id::text = :cid
            """),
            {"cid": challenge_id},
        )
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        logger.exception("Failed to withdraw challenge %s", challenge_id)
        raise HTTPException(status_code=500, detail="Failed to withdraw challenge")
    finally:
        session.close()

    _record_doubt_event(
        AUDIT_ENTITY_CHALLENGE, challenge_id, old_status, "withdrawn",
        current_user.get("id"), {"action": "withdraw"},
    )
    return {"ok": True, "challenge_id": challenge_id, "status": "withdrawn"}


# ---------------------------------------------------------------------------
# D3-2: 検証提案 + 未検証合意リスト
# ---------------------------------------------------------------------------


class ProposalCreateRequest(BaseModel):
    proposal: str


@admin_router.post("/challenges/{challenge_id}/proposals")
def create_verification_proposal(
    challenge_id: str,
    body: ProposalCreateRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """疑義 → 「この実験・この計算で検証可能」への昇格。"""
    if not body.proposal.strip():
        raise HTTPException(status_code=422, detail="proposal が必要です")
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT status FROM challenges WHERE id::text = :cid"),
            {"cid": challenge_id},
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Challenge not found")
        old_status = str(row[0] or "open")
        proposal_row = session.execute(
            sa_text("""
                INSERT INTO verification_proposals (challenge_id, proposal, proposer_id)
                VALUES (CAST(:cid AS uuid), :proposal, CAST(:uid AS uuid))
                RETURNING id
            """),
            {"cid": challenge_id, "proposal": body.proposal.strip(), "uid": current_user.get("id")},
        ).fetchone()
        # 元 challenge を led_to_verification に遷移（双方向参照）
        session.execute(
            sa_text("""
                UPDATE challenges SET status = 'led_to_verification', updated_at = now()
                WHERE id::text = :cid
            """),
            {"cid": challenge_id},
        )
        session.commit()
        proposal_id = str(proposal_row[0])
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        logger.exception("Failed to create proposal for challenge %s", challenge_id)
        raise HTTPException(status_code=500, detail="Failed to create proposal")
    finally:
        session.close()

    _record_doubt_event(
        AUDIT_ENTITY_VERIFICATION_PROPOSAL, proposal_id, "", "proposed",
        current_user.get("id"), {"action": "create", "challenge_id": challenge_id},
    )
    _record_doubt_event(
        AUDIT_ENTITY_CHALLENGE, challenge_id, old_status, ChallengeStatus.LED_TO_VERIFICATION.value,
        current_user.get("id"), {"action": "proposal_promotion", "proposal_id": proposal_id},
    )
    return {"ok": True, "proposal_id": proposal_id, "challenge_status": "led_to_verification"}


@admin_router.get("/courses/{course_id}/open-assumptions")
def get_open_assumptions(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """未検証合意リスト（台帳の投影・編集不可）。高負荷 × 低検証の自動編纂。"""
    session = _pg_session()
    try:
        items = compile_open_assumptions(session, course_id, include_challenger_names=True)
        return {"course_id": course_id, "items": items}
    finally:
        session.close()


# ---------------------------------------------------------------------------
# D3-3: 反実仮想モード
# ---------------------------------------------------------------------------


class CounterfactualComputeRequest(BaseModel):
    toggled_assumption_ids: list[str] = Field(default_factory=list)
    course_id: str = ""
    document_id: str = ""


class CounterfactualSessionCreateRequest(CounterfactualComputeRequest):
    notes: str = ""
    shared_scope: str = "private"
    group_id: str = ""


class CounterfactualSessionPatchRequest(BaseModel):
    notes: str | None = None
    shared_scope: str | None = None
    group_id: str | None = None


@admin_router.post("/counterfactual/compute")
def compute_counterfactual_route(
    body: CounterfactualComputeRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """保存なしの試算。決定論的（同入力同出力）。再構築は計算しない。"""
    if not body.toggled_assumption_ids:
        raise HTTPException(status_code=422, detail="toggled_assumption_ids が必要です")
    session = _pg_session()
    try:
        return compute_counterfactual(
            session,
            toggled_assumption_ids=[str(a) for a in body.toggled_assumption_ids],
            course_id=body.course_id or "",
            document_id=body.document_id or "",
        )
    finally:
        session.close()


@admin_router.post("/counterfactual/sessions")
def save_counterfactual_session(
    body: CounterfactualSessionCreateRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """反実仮想セッションの保存（スナップショット）。既定は private。"""
    if not body.toggled_assumption_ids:
        raise HTTPException(status_code=422, detail="toggled_assumption_ids が必要です")
    if body.shared_scope not in _SHARED_SCOPES:
        raise HTTPException(status_code=422, detail=f"invalid shared_scope: {body.shared_scope}")
    if body.shared_scope == "group" and not body.group_id:
        raise HTTPException(status_code=422, detail="group 共有には group_id が必要です")

    session = _pg_session()
    try:
        payload = compute_counterfactual(
            session,
            toggled_assumption_ids=[str(a) for a in body.toggled_assumption_ids],
            course_id=body.course_id or "",
            document_id=body.document_id or "",
        )
        collapsed, surviving, indeterminate = snapshot_subgraphs(payload)
        row = session.execute(
            sa_text("""
                INSERT INTO counterfactual_sessions
                    (owner_id, course_id, document_id, toggled_assumption_ids,
                     collapsed_subgraph, surviving_subgraph, indeterminate_subgraph,
                     notes, shared_scope, group_id)
                VALUES
                    (CAST(:uid AS uuid), :course, :doc, CAST(:toggled AS jsonb),
                     CAST(:collapsed AS jsonb), CAST(:surviving AS jsonb),
                     CAST(:indeterminate AS jsonb),
                     :notes, :scope, CAST(NULLIF(:gid, '') AS uuid))
                RETURNING id
            """),
            {
                "uid": current_user.get("id"),
                "course": body.course_id or "",
                "doc": body.document_id or "",
                "toggled": json.dumps([str(a) for a in body.toggled_assumption_ids], ensure_ascii=False),
                "collapsed": collapsed,
                "surviving": surviving,
                "indeterminate": indeterminate,
                "notes": body.notes or "",
                "scope": body.shared_scope,
                "gid": body.group_id or "",
            },
        ).fetchone()
        session.commit()
        session_id = str(row[0])
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        logger.exception("Failed to save counterfactual session")
        raise HTTPException(status_code=500, detail="Failed to save counterfactual session")
    finally:
        session.close()

    _record_doubt_event(
        AUDIT_ENTITY_COUNTERFACTUAL_SESSION, session_id, "", body.shared_scope,
        current_user.get("id"),
        {"action": "create", "toggled_count": len(body.toggled_assumption_ids)},
    )
    return {"ok": True, "session_id": session_id, "result": payload}


def _counterfactual_session_out(row) -> dict:
    return {
        "id": str(row[0]),
        "owner_id": str(row[1] or ""),
        "owner_name": str(row[2] or ""),
        "course_id": str(row[3] or ""),
        "document_id": str(row[4] or ""),
        "toggled_assumption_ids": _jsonb_list(row[5]),
        "collapsed_subgraph": _jsonb_dict(row[6]),
        "surviving_subgraph": _jsonb_dict(row[7]),
        "indeterminate_subgraph": _jsonb_dict(row[8]),
        "notes": str(row[9] or ""),
        "shared_scope": str(row[10] or "private"),
        "group_id": str(row[11] or ""),
        "created_at": str(row[12] or ""),
    }


_CF_SELECT = """
    SELECT s.id::text, s.owner_id::text, COALESCE(u.display_name, ''),
           s.course_id, s.document_id, s.toggled_assumption_ids,
           s.collapsed_subgraph, s.surviving_subgraph, s.indeterminate_subgraph,
           s.notes, s.shared_scope, COALESCE(s.group_id::text, ''), s.created_at::text
    FROM counterfactual_sessions s
    LEFT JOIN users u ON u.id = s.owner_id
"""


@admin_router.get("/counterfactual/sessions")
def list_counterfactual_sessions(
    course_id: str = "",
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """自分のセッション + 共有されたセッション（public / 所属 group）。"""
    session = _pg_session()
    try:
        filters = ["""(
            s.owner_id = CAST(:uid AS uuid)
            OR s.shared_scope = 'public'
            OR (s.shared_scope = 'group' AND s.group_id IN (
                SELECT group_id FROM group_members WHERE user_id = CAST(:uid AS uuid)
            ))
        )"""]
        params: dict[str, Any] = {"uid": current_user.get("id")}
        if course_id:
            filters.append("s.course_id = :course")
            params["course"] = course_id
        rows = session.execute(
            sa_text(_CF_SELECT + f" WHERE {' AND '.join(filters)} ORDER BY s.created_at DESC"),
            params,
        ).fetchall()
        return {"items": [_counterfactual_session_out(r) for r in rows]}
    finally:
        session.close()


@admin_router.patch("/counterfactual/sessions/{session_id}")
def patch_counterfactual_session(
    session_id: str,
    body: CounterfactualSessionPatchRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """notes・共有範囲の変更（owner のみ）。"""
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT owner_id::text, shared_scope FROM counterfactual_sessions WHERE id::text = :sid"),
            {"sid": session_id},
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Counterfactual session not found")
        if str(row[0]) != str(current_user.get("id")):
            raise HTTPException(status_code=403, detail="セッションの編集は作成者のみ可能です")
        old_scope = str(row[1] or "private")

        updates = []
        params: dict[str, Any] = {"sid": session_id}
        if body.notes is not None:
            updates.append("notes = :notes")
            params["notes"] = body.notes
        if body.shared_scope is not None:
            if body.shared_scope not in _SHARED_SCOPES:
                raise HTTPException(status_code=422, detail=f"invalid shared_scope: {body.shared_scope}")
            if body.shared_scope == "group" and not (body.group_id or "").strip():
                raise HTTPException(status_code=422, detail="group 共有には group_id が必要です")
            updates.append("shared_scope = :scope")
            params["scope"] = body.shared_scope
        if body.group_id is not None:
            updates.append("group_id = CAST(NULLIF(:gid, '') AS uuid)")
            params["gid"] = body.group_id
        if not updates:
            return {"ok": True, "session_id": session_id}
        session.execute(
            sa_text(f"UPDATE counterfactual_sessions SET {', '.join(updates)}, updated_at = now() WHERE id::text = :sid"),
            params,
        )
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        logger.exception("Failed to patch counterfactual session %s", session_id)
        raise HTTPException(status_code=500, detail="Failed to patch counterfactual session")
    finally:
        session.close()

    if body.shared_scope is not None and body.shared_scope != old_scope:
        _record_doubt_event(
            AUDIT_ENTITY_COUNTERFACTUAL_SESSION, session_id, old_scope, body.shared_scope,
            current_user.get("id"), {"action": "share_scope_change"},
        )
    return {"ok": True, "session_id": session_id}


# ---------------------------------------------------------------------------
# DX-2: KPI 内部計測（SYSTEM_ADMIN のみ・ユーザー非表示）
# ---------------------------------------------------------------------------


@admin_router.get("/metrics")
def get_doubt_metrics(
    current_user: dict = Depends(_require_system_admin),
) -> dict:
    """運用判断用の内部 KPI（数値をユーザーに見せる API・UI は作らない）。"""
    return collect_doubt_metrics()


# ---------------------------------------------------------------------------
# D3-6: 学習者向け（読み取り専用・fail-closed）
# ---------------------------------------------------------------------------


def _learner_fact_line(target_type: str, status: str, scopes: list[dict]) -> str:
    """台帳の正直表示の一行事実文。煽らない中立文言。

    検証済みスコープも未記帳も同じ精度で併記する（§8-1・8-2 —
    相対主義への誤誘導を防ぐ）。
    """
    if scopes:
        first = scopes[0]
        axis = next(
            (str(first.get(k) or "").strip() for k in ("condition", "domain", "precision", "system")
             if str(first.get(k) or "").strip()),
            "",
        )
        if status == "directly_verified" and axis:
            return f"この内容は「{axis}」の範囲で検証されたと記帳されています。"
        if axis:
            return f"この内容には「{axis}」の範囲の検証スコープが記帳されています。"
        return "この内容には検証スコープが記帳されています。"
    if status == "indirectly_supported":
        return "この内容は出典に記載がありますが、検証スコープはまだ記帳されていません。"
    if status == "refuted":
        return "この内容には反証の記帳があります。"
    return "この内容の検証スコープはまだ記帳されていません。"


@learning_router.get("/courses/{course_id}/ledger/{target_type}/{target_id}")
def get_learner_ledger_line(
    course_id: str,
    target_type: str,
    target_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """台帳の正直表示（読み取り専用）。スコープと段階ラベルのみ。

    生スコア・疑義の生一覧は返さない。台帳行が無ければ 404
    （UI はセクション自体を出さない fail-closed）。
    """
    _require_ledger_target_type(target_type)
    course = get_accessible_course_data(str(current_user.get("id")), course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    session = _pg_session()
    try:
        row = _fetch_ledger_row(session, target_type, target_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Ledger entry not found")
        status = str(row[5] or "unknown")
        scopes = [_scope_out(s) for s in _jsonb_list(row[6]) if isinstance(s, dict)]
        # 学習者へは記帳者 ID を出さない（帰属は教員向け表示のみ）
        learner_scopes = [
            {k: v for k, v in scope.items() if k in ("condition", "domain", "precision", "system")}
            for scope in scopes
        ]
        return {
            "target_id": target_id,
            "target_type": target_type,
            "verification_status": status,
            "verification_status_label": _VERIFICATION_STATUS_LABELS.get(status, status),
            "scopes": learner_scopes,
            "scope_coverage": scope_coverage_level(len(scopes)),
            "fact_line": _learner_fact_line(target_type, status, scopes),
        }
    finally:
        session.close()


@learning_router.get("/courses/{course_id}/open-assumptions")
def get_learner_open_assumptions(
    course_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """未検証合意リストの閲覧（読み取り専用）。疑義者の氏名は含めない。"""
    course = get_accessible_course_data(str(current_user.get("id")), course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    session = _pg_session()
    try:
        items = compile_open_assumptions(session, course_id, include_challenger_names=False)
        return {"course_id": course_id, "items": items}
    finally:
        session.close()
