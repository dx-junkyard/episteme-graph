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

from display_route import LearnerDisplayRoute  # DP1: 学習者向けルートは表示投影を必ず通る
from dependencies import ROLE_SYSTEM_ADMIN, _get_current_user, _require_system_admin, _require_teacher
# オブジェクトスコープ権限（P0）の正本ゲート。`_require_teacher` は「TEACHER 以上」しか
# 保証しないため、course_id 直指定の経路は編集権限ゲートを必ず通す
# （不在・権限なしはどちらも 404・detail 同一。admin.py の既存5経路と同型）。
from routes.admin import _require_editable_course_or_404, _require_editable_document_or_404
from core.doubt.assumption_mining.corpus_audit import run_corpus_audit
from core.doubt.assumption_mining.worker import maybe_schedule_assumption_mining
from core.doubt.counterfactual import (
    OBSERVATION_OUTSIDE_SCOPE_WARNING_PREFIX,
    compute_counterfactual,
    snapshot_subgraphs,
)
from core.doubt.falsification_conditions.worker import maybe_schedule_falsification_candidates
from core.doubt.load_calculator import load_percentiles, recompute_load_scores
from core.doubt.metrics import collect_doubt_metrics
from core.doubt.naive_signal import aggregate_naive_signals, has_naive_signal
from core.doubt.observation_targets import observation_claim_targets
from core.doubt.open_assumptions import compile_open_assumptions
from core.doubt.population import CLAIM_CHAIN_EXCLUDED_NOTE, ClaimChainExclusion
from core.doubt.target_labels import LedgerTargetLabels, ledger_target_label
from core.doubt.schema import (
    CHALLENGE_MODES,
    EVIDENCE_LINE_KINDS,
    ChallengeStatus,
    ChallengeType,
    FALSIFICATION_KIND_LABELS,
    FALSIFICATION_KINDS,
    ProposalStatus,
    REACHABILITY_LABELS,
    REACHABILITY_LEVELS,
    TargetType,
    VerificationStatus,
    load_level_for_score,
    scope_coverage_level,
)
from core.doubt.scope_candidates.worker import maybe_schedule_scope_candidates
# 学習者向け投影の生数値遮断は、ゼミ前ブリーフ（SB2）と**同じ語彙**を使う
# （数値キーの表を二重に持たない。正本は core/doubt/seminar_brief.py）。
from core.doubt.seminar_brief import (
    _FORBIDDEN_NUMERIC_KEYS as LEARNER_FORBIDDEN_NUMERIC_KEYS,
    _strip_numeric_keys as strip_learner_numeric_keys,
)
from core.doubt.ledger_targets import (
    LEDGER_ATTRIBUTION_CHECK_NOTE,
    LEDGER_DOCUMENT_REQUIRED_NOTE,
    LEDGER_COMPONENT_VIA_NODE_NOTE,
    LEDGER_COMPONENT_VIA_NODE_NOTE_UNNAMED,
    LedgerDocumentRequired,
    ledger_row_filter,
    normalize_document_id,
    resolve_component_ledger_target,
    resolve_ledger_document_id,
    supporting_claims_for_target,
    target_requires_document,
)
from core.doubt.scope import (
    ASSUMPTION_MINING_NOTES,
    CHALLENGE_TARGETS_NONE_NOTE,
    CHALLENGE_TARGETS_NOTE,
    LEDGER_READ_ONLY_NOTE,
    RECORDER_UNKNOWN_LABEL,
    SCOPE_DUPLICATE_NOTE,
    VERIFICATION_STATUS_CHANGED_NOTE,
    VERIFICATION_STATUS_UNCHANGED_NOTE,
    ATLAS_EMPTY_NOTE,
    BATCH_SCHEDULED_NOTE,
    CORPUS_AUDIT_NOTES,
    LEARNER_OPEN_ASSUMPTIONS_EMPTY_NOTE,
    LOAD_RECOMPUTED_NOTE,
    OPEN_ASSUMPTIONS_EMPTY_NOTE,
    SCOPE_NO_DOCUMENTS_NOTE,
    STUDIO_LEDGER_HINT,
)
from core.doubt.support_paths import compute_support_lines
from core.label_vocab import VERIFICATION_STATUS_LABELS_LEDGER
from core.label_vocab import LEDGER_SCOPE_AXIS_LABELS
# P4-5（知識の転用層 §8）の表示ラベル。語彙（enum）の正本は core/doubt/schema.py /
# core/schema.py、ラベルの正本は core/label_vocab.py（ここでは再定義しない）。
from core.label_vocab import CHALLENGE_MODE_LABELS, EVIDENCE_LINE_KIND_LABELS, SHARED_SCOPE_LABELS
from core.postgres import get_session as _pg_session
from core.status import cross_layer_notify
from core.schema import (
    AUDIT_ENTITY_ASSUMPTION,
    AUDIT_ENTITY_CHALLENGE,
    AUDIT_ENTITY_COUNTERFACTUAL_SESSION,
    AUDIT_ENTITY_LEDGER,
    AUDIT_ENTITY_SIMULATION,
    AUDIT_ENTITY_VERIFICATION_PROPOSAL,
)
from core.simulation_bridge import store as simulation_store
from services import (
    get_accessible_course_data,
    list_course_source_document_ids,
    record_review_event,
    resolve_document_access,
)

logger = logging.getLogger(__name__)

# admin.router（prefix=/api/admin）に include される
admin_router = APIRouter(prefix="/doubt", tags=["Doubt Layer"])
# main.py で直接 include される学習者向け読み取り専用 router
learning_router = APIRouter(prefix="/api/learning", tags=["Learning"], route_class=LearnerDisplayRoute)

_LEDGER_TARGET_TYPES = tuple(t.value for t in TargetType)
_VERIFICATION_STATUSES = tuple(s.value for s in VerificationStatus)
_CHALLENGE_TARGET_TYPES = ("assumption", "claim")
_CHALLENGE_TYPES = tuple(t.value for t in ChallengeType)
_SHARED_SCOPES = ("private", "group", "public")

# SL層（賭け金の台帳）— proposal ステータス遷移の許可集合（前進 + 任意時点から withdrawn）。
_PROPOSAL_STATUSES = tuple(s.value for s in ProposalStatus)
_PROPOSAL_ALLOWED_TRANSITIONS = {
    ("proposed", "in_progress"),
    ("in_progress", "completed"),
    ("proposed", "withdrawn"),
    ("in_progress", "withdrawn"),
    ("completed", "withdrawn"),
}

# SL-2: 観測の反実仮想の Duhem 区別（記帳のみ・伝播アルゴリズムには影響しない）。
_OBSERVATION_ASPECTS = ("value", "systematics")

# 訳語の正本は core/label_vocab.py。W層 位置づけレンズ（core/deliberation/
# positioning.py）は同じキーで短い状態名を使う（意図された宛先差 — 統合しない）。
_VERIFICATION_STATUS_LABELS = VERIFICATION_STATUS_LABELS_LEDGER


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


def _fetch_ledger_row(session, target_type: str, target_id: str, *, document_id: str | None):
    """台帳行 1 行（行キーは (document_id, target_type, target_id) — IK-0899・migration 092）。

    ``document_id`` は必須のキーワード引数。論文の中でしか一意でない対象（グラフのノード id・
    式の agent ID）で document が分からなければ 422 で閉じる（推測で別の論文の行を引かない）。
    """
    # falsification_conditions（row[13]）/ falsification_candidates（row[14]）は
    # SL-1（賭け金の台帳）用に末尾へ追加（既存インデックス 0-12 は不変）。
    # evidence_lines（row[15]）は P4-5（根拠の線・migration 083）用にさらに末尾へ追加。
    # attribution_check_needed（row[16]）は migration 092（IK-0899）用にさらに末尾へ追加。
    try:
        where, params = ledger_row_filter(
            target_type=target_type, target_id=target_id, document_id=document_id
        )
    except LedgerDocumentRequired as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return session.execute(
        sa_text(f"""
            SELECT id::text, target_id, target_type, document_id::text AS document_id, course_id,
                   verification_status, verification_scopes, scope_candidates,
                   consensus_explicit, consensus_behavioral, load_score,
                   created_at, updated_at, falsification_conditions, falsification_candidates,
                   evidence_lines, attribution_check_needed
            FROM epistemic_ledger
            WHERE {where}
        """),
        params,
    ).fetchone()


def _row_attribution_check_needed(row) -> bool:
    """migration 092 より前の共有キーで人の記帳が入った行か（row[16]・無ければ False）。"""
    try:
        return bool(row[16]) if len(row) > 16 else False
    except Exception:  # noqa: BLE001
        return False


def _ledger_route_document(session, target_type: str, target_id: str, document_id: str | None) -> str | None:
    """経路が扱う対象の document（IK-0899）。論文の中でしか一意でない対象で無ければ 422。"""
    try:
        return resolve_ledger_document_id(
            session, target_type=target_type, target_id=target_id, document_id=document_id
        )
    except LedgerDocumentRequired as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _get_or_create_ledger_row(session, target_type: str, target_id: str, *, document_id: str | None):
    row = _fetch_ledger_row(session, target_type, target_id, document_id=document_id)
    if row is not None:
        return row
    # migration 080 以降 document_id は UUID NOT NULL（既定なし）。対象から引いて入れる。
    from core.doubt.ledger_targets import ensure_ledger_row

    try:
        ensure_ledger_row(
            session, target_type=target_type, target_id=target_id, document_id=document_id
        )
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return _fetch_ledger_row(session, target_type, target_id, document_id=document_id)


def _challenge_entry(session, target_type: str, target_id: str, document_id: str) -> dict:
    """疑義の区画に添える入口（IK-0904）。前提・主張は ``challenge_allowed`` だけ。"""
    if target_type in _CHALLENGE_TARGET_TYPES:
        return {"challenge_allowed": True}
    try:
        claims = supporting_claims_for_target(
            session, document_id=document_id, target_type=target_type, target_id=target_id
        )
    except Exception:  # noqa: BLE001
        logger.debug("supporting claims lookup failed", exc_info=True)
        claims = []
    return {
        "challenge_allowed": False,
        "challenge_note": CHALLENGE_TARGETS_NOTE if claims else CHALLENGE_TARGETS_NONE_NOTE,
        "supporting_claims": claims,
    }


#: 行の無い台帳（IK-0931）で画面が出す空欄の事実文（空欄は発見 — 警告色にしない）。
LEDGER_NOT_YET_RECORDED_NOTE = "この対象の台帳はまだ記帳されていません。"


def _target_exists_for_ledger(session, target_type: str, target_id: str, document_id: str) -> bool:
    """行の無い台帳 GET で、対象がその論文に実在するか（IK-0931）。

    論文の中でしか一意でない対象（グラフのノード id・式の agent ID）は
    ``_scoped_target_in_document``。全体で一意の対象（主張・部品の UUID・前提）は、閲覧ゲートが
    対象の行から document を引けたこと（``_target_document_ids`` が空でない）を実在とみなす。
    """
    if target_requires_document(target_type, target_id):
        doc = normalize_document_id(document_id)
        return bool(doc) and _scoped_target_in_document(session, target_type, target_id, doc)
    return bool(document_id)


def _empty_ledger_entry(
    session, target_type: str, target_id: str, *, document_id: str, can_edit: bool,
) -> dict:
    """台帳行の無い対象の GET 応答（``exists: false``・IK-0931）。実在しなければ 404。"""
    if not _target_exists_for_ledger(session, target_type, target_id, document_id):
        raise HTTPException(status_code=404, detail="Ledger entry not found")
    return {
        "exists": False,
        "not_recorded_note": LEDGER_NOT_YET_RECORDED_NOTE,
        "target_id": target_id,
        "target_type": target_type,
        "target_label": ledger_target_label(
            session, document_id=document_id, target_type=target_type, target_id=target_id
        ),
        "document_id": document_id,
        **_document_title_entry(session, document_id),
        "verification_status": "unknown",
        "verification_scopes": [],
        "scope_candidates": [],
        "challenges": [],
        "evidence_lines": [],
        "falsification_conditions": [],
        "falsification_candidates": [],
        "simulation_candidates": [],
        "simulation_candidates_decided": [],
        **(_challenge_entry(session, target_type, target_id, document_id)),
        "can_edit": bool(can_edit),
        **({"can_edit_note": LEDGER_READ_ONLY_NOTE} if not can_edit else {}),
    }


#: 論文の題名が引けないときの置き換え語（IK-0934。ファイル名・arXiv 番号を題名にしない）。
DOCUMENT_TITLE_UNKNOWN_LABEL = "題名を引けない論文"


def _document_title_entry(session, document_id: str) -> dict:
    """台帳の見出しに添える論文の題名（IK-0934・``{"document_title": ...}``）。"""
    doc = normalize_document_id(document_id)
    if not doc:
        return {}
    title = ""
    try:
        # 表示題名の正本（ファイル名様の教材名は arXiv の写しの題名へ。API は呼ばない）。
        from core.library.structural_matches import load_display_titles

        titles, _notes = load_display_titles(session, [doc])
        title = str(titles.get(doc) or "").strip()
        from core.paper_discovery.radar import title_looks_like_filename

        if title and title_looks_like_filename(title):
            title = ""
    except Exception:  # noqa: BLE001
        logger.debug("document title lookup failed", exc_info=True)
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
    return {"document_title": title or DOCUMENT_TITLE_UNKNOWN_LABEL}


def _user_display_names(session, user_ids: list[str]) -> dict[str, str]:
    """記帳者 id → 表示名（IK-0905。引けないものはキーごと欠落・失敗は空 dict）。"""
    ids = sorted({str(u or "").strip() for u in user_ids} - {""})
    ids = [u for u in ids if normalize_document_id(u)]  # UUID 形のみ（形が違えば引かない）
    if not ids:
        return {}
    try:
        rows = session.execute(
            sa_text(
                "SELECT id::text, COALESCE(NULLIF(display_name, ''), '') FROM users "
                "WHERE id::text = ANY(:ids)"
            ),
            {"ids": ids},
        ).fetchall()
    except Exception:  # noqa: BLE001
        logger.debug("recorder name lookup failed", exc_info=True)
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return {}
    return {str(r[0]): str(r[1]) for r in rows if r and r[0] and r[1]}


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


def _falsification_condition_out(condition: dict) -> dict:
    """反証条件（確定・SL-1）の API 表現。実 JSONB と同じ形（confidence を元々持たない）。"""
    return {
        "condition_id": str(condition.get("condition_id") or ""),
        "statement": str(condition.get("statement") or ""),
        "kind": str(condition.get("kind") or ""),
        "reachability": str(condition.get("reachability") or "unassessed"),
        "evidence_ids": [str(e) for e in condition.get("evidence_ids") or []],
        "evidence_quote": str(condition.get("evidence_quote") or ""),
        "recorded_by": str(condition.get("recorded_by") or ""),
        "reason": str(condition.get("reason") or ""),
        "recorded_at": str(condition.get("recorded_at") or ""),
        "from_candidate_id": str(condition.get("from_candidate_id") or ""),
    }


def _falsification_candidate_out(candidate: dict) -> dict:
    """LLM 反証条件候補の API 表現。生の確度スコアは返さない（SL4）。"""
    return {
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "statement": str(candidate.get("statement") or ""),
        "kind": str(candidate.get("kind") or ""),
        "evidence_quote": str(candidate.get("evidence_quote") or ""),
        "reason": str(candidate.get("reason") or ""),
        "status": str(candidate.get("status") or "candidate"),
    }


def _simulation_candidate_out(candidate: dict) -> dict:
    """検証シミュレーションの台帳候補（SB層）の API 表現。confidence は持たない（SB6）。"""
    return {
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "run_id": str(candidate.get("run_id") or ""),
        "program_id": str(candidate.get("program_id") or ""),
        "wiring_id": str(candidate.get("wiring_id") or ""),
        "wiring_name": str(candidate.get("wiring_name") or ""),
        "condition": str(candidate.get("condition") or ""),
        "statement": str(candidate.get("statement") or ""),
        "level": str(candidate.get("level") or ""),
        "facts": [str(f) for f in candidate.get("facts") or [] if str(f or "").strip()],
        "reconstructed_inputs": bool(candidate.get("reconstructed_inputs")),
        "status": str(candidate.get("status") or "candidate"),
        "created_at": str(candidate.get("created_at") or ""),
        # 判断済みの候補の控え（教員向け。判断した人の user id は出さない — 帰属は監査に残る）。
        "decided_at": str(candidate.get("confirmed_at") or candidate.get("dismissed_at") or ""),
        "dismiss_reason": str(candidate.get("dismiss_reason") or ""),
    }


def _simulation_candidates_for(
    session, target_type: str, target_id: str, *, decided: bool = False,
    document_id: str | None = None,
) -> list[dict]:
    """``epistemic_ledger.simulation_candidates`` を列名で引く（fail-soft）。

    既定は未処理の候補のみ（``simulation_candidates`` キー）。``decided=True`` は確定・見送り済みの
    候補（``simulation_candidates_decided`` キー。見送りの理由を読めるようにする = RE5）。
    """
    try:
        where, params = ledger_row_filter(
            target_type=target_type, target_id=target_id, document_id=document_id
        )
        row = session.execute(
            sa_text(f"""
                SELECT simulation_candidates
                FROM epistemic_ledger
                WHERE {where}
            """),
            params,
        ).fetchone()
    except Exception:  # noqa: BLE001 — 列が無い旧 DB でも台帳の他の情報は返す
        logger.debug("simulation candidates unavailable", exc_info=True)
        return []
    if row is None:
        return []
    return [
        _simulation_candidate_out(c) for c in _jsonb_list(row[0])
        if isinstance(c, dict) and (str(c.get("status") or "candidate") == "candidate") != decided
    ]


def _validate_falsification_condition_fields(
    statement: str,
    kind: str,
    reason: str,
    evidence_ids: list[str],
    evidence_quote: str,
    reachability: str,
) -> None:
    """反証条件（手動記帳 / 候補確定 / 訂正）に共通する必須項目の検証（SL-1）。

    kind='not_formulable'（人間専用の「定式化できない」記帳）は根拠を要求しない
    （§3.2: これにより反証不可能（記帳あり）と未検討（空配列）が区別できる）。
    """
    if not statement.strip():
        raise HTTPException(status_code=422, detail="statement が必要です")
    if kind not in FALSIFICATION_KINDS:
        raise HTTPException(status_code=422, detail=f"invalid kind: {kind}")
    if not reason.strip():
        raise HTTPException(status_code=422, detail="reason が必要です")
    if kind != "not_formulable" and not evidence_ids and not evidence_quote.strip():
        raise HTTPException(
            status_code=422,
            detail="根拠（evidence_ids または evidence_quote）が必要です"
                   "（kind=not_formulable の記帳を除く）",
        )
    if reachability not in REACHABILITY_LEVELS:
        raise HTTPException(status_code=422, detail=f"invalid reachability: {reachability}")


_TARGET_ELEMENT_REF_KEYS = ("element_type", "element_id", "document_id")


def _normalize_target_element_ref(value: Any) -> dict:
    """疑義の対象要素参照（P4-5 / X-6）を許可キーだけに絞る。

    グラフ上の位置は任意（空 ``{}`` = 位置の記録なし）。想定外のキー（confidence 等の
    数値や自由記述）は**捨てる**（KT7: 数値・内部情報の混入口を作らない）。
    """
    if not isinstance(value, dict):
        return {}
    normalized = {}
    for key in _TARGET_ELEMENT_REF_KEYS:
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip():
            normalized[key] = raw.strip()
    return normalized


def _evidence_line_out(line: dict) -> dict:
    """根拠の線（P4-5 / X-5）の教員向け API 表現。実 JSONB と同じ形 + 種別ラベル。"""
    kind = str(line.get("line_kind") or "")
    return {
        "line_id": str(line.get("line_id") or ""),
        "line_kind": kind,
        "line_kind_label": EVIDENCE_LINE_KIND_LABELS.get(kind, kind),
        "evidence_ids": [str(e) for e in line.get("evidence_ids") or []],
        "claim_ids": [str(c) for c in line.get("claim_ids") or []],
        "equation_ids": [str(e) for e in line.get("equation_ids") or []],
        "recorded_by": str(line.get("recorded_by") or ""),
        "reason": str(line.get("reason") or ""),
        "recorded_at": str(line.get("recorded_at") or ""),
    }


def _validate_evidence_line_fields(
    line_kind: str,
    reason: str,
    evidence_ids: list[str],
    claim_ids: list[str],
    equation_ids: list[str],
) -> None:
    """根拠の線（手動記帳 / 訂正）に共通する必須項目の検証（P4-5）。

    ``line_kind`` は語彙内・``reason`` は非空・根拠 ID は 3 系統のいずれか 1 つ以上。
    「経路が分からない」を根拠なしで記帳できてしまうと、検証記録の不在
    （空配列 = 発見）と区別が付かなくなる（SL-1 の not_formulable と同じ理由で、
    こちらは**空の記帳を許さない**側に倒す）。
    """
    if line_kind not in EVIDENCE_LINE_KINDS:
        raise HTTPException(status_code=422, detail=f"invalid line_kind: {line_kind}")
    if not reason.strip():
        raise HTTPException(status_code=422, detail="reason が必要です")
    if not evidence_ids and not claim_ids and not equation_ids:
        raise HTTPException(
            status_code=422,
            detail="根拠（evidence_ids / claim_ids / equation_ids のいずれか）が必要です",
        )


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


def _load_level_for_row(session, document_ids: list[str], load_score: Any) -> str:
    """台帳 1 行の負荷段階。基準は対象の document の範囲（IK-0852: course_id 列は使わない）。"""
    if load_score is None:
        return ""
    if not document_ids:
        return ""
    p50, p90, p99 = load_percentiles(session, document_ids=document_ids)
    return load_level_for_score(float(load_score), p50, p90, p99)


def _course_scope_document_ids(course_id: str, current_user: dict) -> list[str]:
    """コース単位ルートのゲートと範囲の正本（IK-0852 / IK-0857, 2026-10-23）。

    1. 編集権限ゲート（``_require_editable_course_or_404`` — 不在と権限なしは同一の 404）。
    2. 範囲 = そのコースの教材（sources）から求めた document 集合
       （``services.list_course_source_document_ids``）。成果の行の course_id 列は
       「どのコースの画面で記帳したか」の出所に退き、範囲には使わない。

    `/courses/{course_id}/...` の admin ルートはすべてここを通す（ガードレールが AST で固定）。
    空集合のとき core は SQL を出さない（fail-closed）。
    """
    course_data = _require_editable_course_or_404(course_id, current_user)
    return sorted(list_course_source_document_ids(course_data))



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
    document_id: str | None = None,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """台帳行 + スコープ配列 + 出所 + 合意の 2 軸 + 学習者シグナル + 疑義。

    ``document_id``（query）は開いている論文。グラフのノード id・式の agent ID は論文の中でしか
    一意でないので必須（IK-0899）。閲覧できない論文の台帳は 404。``can_edit`` は記帳できるか
    （IK-0900。画面は偽のとき記帳ボタンを無効にして理由を出す）。
    """
    _require_ledger_target_type(target_type)
    access = _ledger_target_access(target_type, target_id, current_user, document_id=document_id)
    row_document = document_id if target_requires_document(target_type, target_id) else access["document_id"]
    session = _pg_session()
    try:
        row = _fetch_ledger_row(session, target_type, target_id, document_id=row_document)
        if row is None:
            # IK-0931: 行が無いのは「まだ記帳されていない」という事実。閲覧ゲート（上）と
            # 対象の実在の確認を通れば 200 で、対象についての事実（名前・疑義の入口・can_edit）を
            # 行の有無と切り離して返す。404 は対象が実在しない・閲覧できないときだけ。
            return _empty_ledger_entry(
                session, target_type, target_id,
                document_id=str(row_document or access["document_id"] or ""),
                can_edit=bool(access["can_edit"]),
            )
        scopes = [_scope_out(s) for s in _jsonb_list(row[6]) if isinstance(s, dict)]
        # IK-0905: 記帳者は UUID ではなく名前で（取れなければ「記帳者を引けません」）。
        recorder_names = _user_display_names(session, [s["recorded_by"] for s in scopes])
        for scope_item in scopes:
            scope_item["recorded_by_label"] = (
                recorder_names.get(scope_item["recorded_by"]) or RECORDER_UNKNOWN_LABEL
            )
        candidates = [
            _candidate_out(c) for c in _jsonb_list(row[7])
            if isinstance(c, dict) and str(c.get("status") or "candidate") == "candidate"
        ]
        consensus_explicit = _jsonb_dict(row[8])
        course_id = str(row[4] or "")
        document_id = str(row[3] or "")

        falsification_conditions = [
            _falsification_condition_out(c) for c in _jsonb_list(row[13]) if isinstance(c, dict)
        ]
        falsification_candidates = [
            _falsification_candidate_out(c) for c in _jsonb_list(row[14])
            if isinstance(c, dict) and str(c.get("status") or "candidate") == "candidate"
        ]
        # P4-5: 根拠の線（人間の記帳のみ。教員向けは帰属つきの一覧を返す）。
        evidence_lines = [
            _evidence_line_out(line) for line in _jsonb_list(row[15]) if isinstance(line, dict)
        ]
        # SB層（検証シミュレーション, migration 088）: 台帳候補は列名で引く（位置に頼らない）。
        simulation_candidates = _simulation_candidates_for(
            session, target_type, target_id, document_id=row_document
        )
        simulation_candidates_decided = _simulation_candidates_for(
            session, target_type, target_id, decided=True, document_id=row_document
        )
        # SL-3: 独立支持経路（数値非公開・導出失敗はキー自体を付けない fail-soft）。
        # 範囲は対象の document（IK-0852: 台帳行の course_id 列では絞らない）。
        scope_ids = [document_id] if document_id else _target_document_ids(
            session, target_type, target_id, document_id=row_document
        )
        support_lines = None
        try:
            support_lines = compute_support_lines(
                session, target_type, target_id, document_ids=scope_ids,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "support lines computation failed for %s/%s", target_type, target_id, exc_info=True
            )
            support_lines = None

        challenge_rows = session.execute(
            sa_text("""
                SELECT c.id::text, c.challenge_type, c.reason, c.status,
                       COALESCE(u.display_name, ''), c.created_at,
                       c.challenge_mode, c.target_element_ref
                FROM challenges c
                LEFT JOIN users u ON u.id = c.challenger_id
                WHERE c.target_type = :ttype AND c.target_id = :tid
                ORDER BY c.created_at ASC
            """),
            {"ttype": target_type, "tid": target_id},
        ).fetchall() if target_type in _CHALLENGE_TARGET_TYPES else []

        return {
            "exists": True,
            "target_id": str(row[1]),
            "target_type": str(row[2]),
            # IK-0901: 表示名の正本（式は印字番号・ノードは段の日本語名 + 根拠の主張。読めなければ
            # 「名前を引けない対象」。内部 ID を出さない）。
            "target_label": ledger_target_label(
                session, document_id=str(row[3] or ""), target_type=target_type, target_id=target_id
            ),
            "document_id": str(row[3] or ""),
            # IK-0934: 見出し「論文の題名 › 対象の名前」の材料（同じ番号の対象を論文で見分ける）。
            **_document_title_entry(session, str(row[3] or "")),
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
            "load_level": _load_level_for_row(session, scope_ids, row[10]),
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
                    "challenge_mode": str(c[6] or "direct"),
                    "challenge_mode_label": CHALLENGE_MODE_LABELS.get(
                        str(c[6] or "direct"), str(c[6] or "direct")
                    ),
                    "target_element_ref": _normalize_target_element_ref(_jsonb_dict(c[7])),
                }
                for c in challenge_rows
            ],
            "evidence_lines": evidence_lines,
            # IK-0904: 疑義の入口（前提・主張）へ辿る道。式・部品の台帳では支える主張を並べる。
            **(_challenge_entry(session, target_type, target_id, str(row[3] or ""))),
            "falsification_conditions": falsification_conditions,
            "falsification_candidates": falsification_candidates,
            "simulation_candidates": simulation_candidates,
            "simulation_candidates_decided": simulation_candidates_decided,
            **({"support_lines": support_lines} if support_lines is not None else {}),
            "updated_at": str(row[12] or ""),
            # IK-0900: この教員が記帳できるか（真偽値のみ）。偽なら画面は記帳ボタンを無効にする。
            "can_edit": bool(access["can_edit"]),
            **({"can_edit_note": LEDGER_READ_ONLY_NOTE} if not access["can_edit"] else {}),
            # IK-0899: 論文ごとに分かれる前に共有キーで残った記帳は、どの論文のものか確かめさせる。
            **(
                {"attribution_note": LEDGER_ATTRIBUTION_CHECK_NOTE}
                if _row_attribution_check_needed(row) else {}
            ),
        }
    finally:
        session.close()


@admin_router.post("/ledger/{target_type}/{target_id}/scopes")
def add_ledger_scope(
    target_type: str,
    target_id: str,
    body: ScopeCreateRequest,
    document_id: str | None = None,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """検証スコープの記帳（帰属・根拠つきの正式な学術行為）。

    必須: 4 軸のうち 1 つ以上 + 根拠（evidence_ids または evidence_quote）+ reason。
    """
    _require_ledger_target_type(target_type)
    axes = [body.condition.strip(), body.domain.strip(), body.precision.strip(), body.system.strip()]
    if not any(axes):
        # IK-0905: 欄名は画面の語（正本は core/label_vocab.py::LEDGER_SCOPE_AXIS_LABELS）。
        raise HTTPException(
            status_code=422,
            detail="・".join(LEDGER_SCOPE_AXIS_LABELS.values()) + "のうち 1 つ以上を入力してください。",
        )
    if not body.evidence_ids and not body.evidence_quote.strip():
        raise HTTPException(status_code=422, detail="根拠（原文の引用など）を入力してください。")
    if not body.reason.strip():
        raise HTTPException(status_code=422, detail="理由（reason）を入力してください。")

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
    # IK-0900: 対象の教材の編集権限（閲覧のみの教員は書けない）。IK-0899: 行キーの論文。
    doc = _require_editable_ledger_target(
        target_type, target_id, current_user, document_id=document_id
    )
    session = _pg_session()
    try:
        row = _get_or_create_ledger_row(session, target_type, target_id, document_id=doc)
        if row is None:
            raise HTTPException(status_code=500, detail="Failed to create ledger entry")
        # IK-0905: 同じ 4 軸・同じ根拠のスコープが既にあれば、受けたうえで事実文を添える
        # （別の教員の独立の判断として残す意味がある — 弾かない）。
        duplicate = any(
            all(
                str(existing.get(k) or "").strip() == str(scope.get(k) or "").strip()
                for k in ("condition", "domain", "precision", "system", "evidence_quote")
            )
            for existing in _jsonb_list(row[6]) if isinstance(existing, dict)
        )
        session.execute(
            sa_text("""
                UPDATE epistemic_ledger
                SET verification_scopes = verification_scopes || CAST(:scope AS jsonb),
                    updated_at = now()
                WHERE id = CAST(:lid AS uuid)
            """),
            {"scope": json.dumps([scope], ensure_ascii=False), "lid": row[0]},
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
        {"document_id": doc, "action": "scope_add", "scope_id": scope["scope_id"], "reason": scope["reason"]},
    )
    return {
        "ok": True,
        "scope": _scope_out(scope),
        "duplicate": duplicate,
        **({"note": SCOPE_DUPLICATE_NOTE} if duplicate else {}),
    }


@admin_router.patch("/ledger/{target_type}/{target_id}/scopes/{scope_id}")
def patch_ledger_scope(
    target_type: str,
    target_id: str,
    scope_id: str,
    body: ScopePatchRequest,
    document_id: str | None = None,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """スコープの訂正（履歴は review_events に残す）。"""
    _require_ledger_target_type(target_type)
    # IK-0900: 対象の教材の編集権限（閲覧のみの教員は書けない）。IK-0899: 行キーの論文。
    doc = _require_editable_ledger_target(
        target_type, target_id, current_user, document_id=document_id
    )
    session = _pg_session()
    try:
        row = _fetch_ledger_row(session, target_type, target_id, document_id=doc)
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
                WHERE id = CAST(:lid AS uuid)
            """),
            {"scopes": json.dumps(scopes, ensure_ascii=False), "lid": row[0]},
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
        {"document_id": doc, "action": "scope_patch", "scope_id": scope_id, "old": old_scope},
    )
    return {"ok": True, "scope": _scope_out(target_scope)}


@admin_router.put("/ledger/{target_type}/{target_id}/verification-status")
def put_verification_status(
    target_type: str,
    target_id: str,
    body: VerificationStatusRequest,
    document_id: str | None = None,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """検証状態の変更。directly_verified への昇格はスコープ 1 件以上のときのみ
    （= 全称検証の構造的禁止, 422）。"""
    _require_ledger_target_type(target_type)
    new_status = str(body.verification_status or "").strip()
    if new_status not in _VERIFICATION_STATUSES:
        raise HTTPException(status_code=422, detail=f"invalid verification_status: {new_status}")

    # IK-0900: 対象の教材の編集権限（閲覧のみの教員は書けない）。IK-0899: 行キーの論文。
    doc = _require_editable_ledger_target(
        target_type, target_id, current_user, document_id=document_id
    )
    session = _pg_session()
    try:
        row = _get_or_create_ledger_row(session, target_type, target_id, document_id=doc)
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
                WHERE id = CAST(:lid AS uuid)
            """),
            {"status": new_status, "lid": row[0]},
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
        {"document_id": doc, "action": "verification_status", "reason": body.reason or ""},
    )
    # IK-0905: 同じ値の記帳し直しを無反応にしない（変わったかどうかを言い分ける）。
    changed = old_status != new_status
    return {
        "ok": True,
        "verification_status": new_status,
        "changed": changed,
        "note": VERIFICATION_STATUS_CHANGED_NOTE if changed else VERIFICATION_STATUS_UNCHANGED_NOTE,
    }


@admin_router.post("/ledger/{target_type}/{target_id}/scope-candidates/{candidate_id}/confirm")
def confirm_scope_candidate(
    target_type: str,
    target_id: str,
    candidate_id: str,
    body: ScopePatchRequest = Body(default=None),
    document_id: str | None = None,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """LLM スコープ候補の確定。候補の値を **教員の帰属で** verification_scopes に転記する。

    候補行自体は昇格せず status='confirmed' で保持（確定なしに本体へ入らない構造）。
    """
    _require_ledger_target_type(target_type)
    # IK-0900: 対象の教材の編集権限（閲覧のみの教員は書けない）。IK-0899: 行キーの論文。
    doc = _require_editable_ledger_target(
        target_type, target_id, current_user, document_id=document_id
    )
    session = _pg_session()
    try:
        row = _fetch_ledger_row(session, target_type, target_id, document_id=doc)
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
                WHERE id = CAST(:lid AS uuid)
            """),
            {
                "scope": json.dumps([scope], ensure_ascii=False),
                "candidates": json.dumps(candidates, ensure_ascii=False),
                "lid": row[0],
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
        {"document_id": doc, "action": "scope_candidate_confirm", "candidate_id": candidate_id, "scope_id": scope["scope_id"]},
    )
    return {"ok": True, "scope": _scope_out(scope)}


@admin_router.post("/ledger/{target_type}/{target_id}/scope-candidates/{candidate_id}/dismiss")
def dismiss_scope_candidate(
    target_type: str,
    target_id: str,
    candidate_id: str,
    document_id: str | None = None,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """LLM スコープ候補の却下（P4: 行は dismissed で保持）。"""
    _require_ledger_target_type(target_type)
    # IK-0900: 対象の教材の編集権限（閲覧のみの教員は書けない）。IK-0899: 行キーの論文。
    doc = _require_editable_ledger_target(
        target_type, target_id, current_user, document_id=document_id
    )
    session = _pg_session()
    try:
        row = _fetch_ledger_row(session, target_type, target_id, document_id=doc)
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
                WHERE id = CAST(:lid AS uuid)
            """),
            {"candidates": json.dumps(candidates, ensure_ascii=False), "lid": row[0]},
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
        {"document_id": doc, "action": "scope_candidate_dismiss", "candidate_id": candidate_id},
    )
    return {"ok": True, "candidate_id": candidate_id, "status": "dismissed"}


@admin_router.get("/courses/{course_id}/ledger-summary")
def get_ledger_summary(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """コース単位の台帳サマリ（内部/教員向けのみ）。

    空欄は欠陥ではなく事実として返す（文言・警告色にしないのは UI 側の責務）。
    範囲はコースの教材の document 集合（IK-0852）。
    """
    document_ids = _course_scope_document_ids(course_id, current_user)
    session = _pg_session()
    try:
        rows = []
        if document_ids:
            rows = session.execute(
                sa_text("""
                    SELECT target_type,
                           COUNT(*),
                           COUNT(*) FILTER (WHERE verification_scopes <> '[]'::jsonb),
                           COUNT(*) FILTER (WHERE verification_scopes = '[]'::jsonb)
                    FROM epistemic_ledger
                    WHERE document_id::text = ANY(:doc_ids)
                    GROUP BY target_type
                """),
                {"doc_ids": document_ids},
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
    """スコープ候補の非同期リフレッシュ（P6: 同期パスに LLM を入れない）。

    範囲はコースの教材の document 集合（IK-0852）。範囲が空なら起動しない（IK-0853）。
    """
    document_ids = _course_scope_document_ids(course_id, current_user)
    if not document_ids:
        return {"ok": True, "scheduled": False, "reason": "no_documents", "note": SCOPE_NO_DOCUMENTS_NOTE}
    maybe_schedule_scope_candidates(course_id=course_id, document_ids=document_ids)
    return {"ok": True, "scheduled": True, "note": BATCH_SCHEDULED_NOTE}


# ---------------------------------------------------------------------------
# SL-1: 反証条件レジストリ（賭け金の台帳。verification_scopes の双対の別列, SL10）
# ---------------------------------------------------------------------------


class FalsificationConditionCreateRequest(BaseModel):
    statement: str = ""
    kind: str = ""
    reason: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_quote: str = ""
    reachability: str = "unassessed"


class FalsificationConditionPatchRequest(BaseModel):
    statement: str | None = None
    kind: str | None = None
    reason: str | None = None
    evidence_ids: list[str] | None = None
    evidence_quote: str | None = None
    reachability: str | None = None


class FalsificationCandidateConfirmRequest(BaseModel):
    statement: str | None = None
    kind: str | None = None
    reason: str | None = None
    evidence_quote: str | None = None
    reachability: str | None = None


@admin_router.post("/ledger/{target_type}/{target_id}/falsification-conditions")
def add_falsification_condition(
    target_type: str,
    target_id: str,
    body: FalsificationConditionCreateRequest,
    document_id: str | None = None,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """反証条件の手動記帳（人間専用の記帳先, SL-1/SL-3）。"""
    _require_ledger_target_type(target_type)
    _validate_falsification_condition_fields(
        body.statement, body.kind, body.reason, body.evidence_ids, body.evidence_quote,
        body.reachability,
    )
    condition = {
        "condition_id": str(uuid.uuid4()),
        "statement": body.statement.strip(),
        "kind": body.kind,
        "reachability": body.reachability,
        "evidence_ids": [str(e) for e in body.evidence_ids],
        "evidence_quote": body.evidence_quote.strip(),
        "recorded_by": str(current_user.get("id") or ""),
        "reason": body.reason.strip(),
        "recorded_at": _now_iso(),
        "from_candidate_id": "",
    }
    # IK-0900: 対象の教材の編集権限（閲覧のみの教員は書けない）。IK-0899: 行キーの論文。
    doc = _require_editable_ledger_target(
        target_type, target_id, current_user, document_id=document_id
    )
    session = _pg_session()
    try:
        row = _get_or_create_ledger_row(session, target_type, target_id, document_id=doc)
        if row is None:
            raise HTTPException(status_code=500, detail="Failed to create ledger entry")
        session.execute(
            sa_text("""
                UPDATE epistemic_ledger
                SET falsification_conditions = falsification_conditions || CAST(:cond AS jsonb),
                    updated_at = now()
                WHERE id = CAST(:lid AS uuid)
            """),
            {"cond": json.dumps([condition], ensure_ascii=False), "lid": row[0]},
        )
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        logger.exception("Failed to add falsification condition for %s/%s", target_type, target_id)
        raise HTTPException(status_code=500, detail="Failed to add falsification condition")
    finally:
        session.close()

    _record_doubt_event(
        AUDIT_ENTITY_LEDGER, f"{target_type}:{target_id}", "", "falsification_added",
        current_user.get("id"),
        {"document_id": doc, "action": "falsification_add", "condition_id": condition["condition_id"], "reason": condition["reason"]},
    )
    return {"ok": True, "condition": _falsification_condition_out(condition)}


@admin_router.patch("/ledger/{target_type}/{target_id}/falsification-conditions/{condition_id}")
def patch_falsification_condition(
    target_type: str,
    target_id: str,
    condition_id: str,
    body: FalsificationConditionPatchRequest,
    document_id: str | None = None,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """反証条件の訂正（訂正後も必須項目を再検証する）。"""
    _require_ledger_target_type(target_type)
    # IK-0900: 対象の教材の編集権限（閲覧のみの教員は書けない）。IK-0899: 行キーの論文。
    doc = _require_editable_ledger_target(
        target_type, target_id, current_user, document_id=document_id
    )
    session = _pg_session()
    try:
        row = _fetch_ledger_row(session, target_type, target_id, document_id=doc)
        if row is None:
            raise HTTPException(status_code=404, detail="Ledger entry not found")
        conditions = [c for c in _jsonb_list(row[13]) if isinstance(c, dict)]
        target_condition = None
        for condition in conditions:
            if str(condition.get("condition_id") or "") == condition_id:
                target_condition = condition
                break
        if target_condition is None:
            raise HTTPException(status_code=404, detail="Falsification condition not found")

        old_condition = dict(target_condition)
        for field_name in ("statement", "kind", "reason", "evidence_quote", "reachability"):
            value = getattr(body, field_name)
            if value is not None:
                target_condition[field_name] = str(value).strip()
        if body.evidence_ids is not None:
            target_condition["evidence_ids"] = [str(e) for e in body.evidence_ids]

        _validate_falsification_condition_fields(
            str(target_condition.get("statement") or ""),
            str(target_condition.get("kind") or ""),
            str(target_condition.get("reason") or ""),
            list(target_condition.get("evidence_ids") or []),
            str(target_condition.get("evidence_quote") or ""),
            str(target_condition.get("reachability") or "unassessed"),
        )

        session.execute(
            sa_text("""
                UPDATE epistemic_ledger
                SET falsification_conditions = CAST(:conditions AS jsonb), updated_at = now()
                WHERE id = CAST(:lid AS uuid)
            """),
            {
                "conditions": json.dumps(conditions, ensure_ascii=False),
                "lid": row[0],
            },
        )
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        logger.exception("Failed to patch falsification condition for %s/%s", target_type, target_id)
        raise HTTPException(status_code=500, detail="Failed to patch falsification condition")
    finally:
        session.close()

    _record_doubt_event(
        AUDIT_ENTITY_LEDGER, f"{target_type}:{target_id}", "falsification_updated", "falsification_updated",
        current_user.get("id"),
        {"document_id": doc, "action": "falsification_patch", "condition_id": condition_id, "old": old_condition},
    )
    return {"ok": True, "condition": _falsification_condition_out(target_condition)}


@admin_router.post("/ledger/{target_type}/{target_id}/falsification-candidates/{candidate_id}/confirm")
def confirm_falsification_candidate(
    target_type: str,
    target_id: str,
    candidate_id: str,
    body: FalsificationCandidateConfirmRequest = Body(default=None),
    document_id: str | None = None,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """反証条件候補の確定。scope-candidates confirm（543行目）の完全な写し:
    候補行自体は昇格せず status='confirmed' で保持し、教員の帰属で新規
    :class:`FalsificationCondition` を発行する（確定なしに本体へ入らない構造, SL2/SL3）。
    """
    _require_ledger_target_type(target_type)
    # IK-0900: 対象の教材の編集権限（閲覧のみの教員は書けない）。IK-0899: 行キーの論文。
    doc = _require_editable_ledger_target(
        target_type, target_id, current_user, document_id=document_id
    )
    session = _pg_session()
    try:
        row = _fetch_ledger_row(session, target_type, target_id, document_id=doc)
        if row is None:
            raise HTTPException(status_code=404, detail="Ledger entry not found")
        candidates = [c for c in _jsonb_list(row[14]) if isinstance(c, dict)]
        candidate = None
        for item in candidates:
            if str(item.get("candidate_id") or "") == candidate_id:
                candidate = item
                break
        if candidate is None:
            raise HTTPException(status_code=404, detail="Falsification candidate not found")
        if str(candidate.get("status") or "candidate") != "candidate":
            raise HTTPException(status_code=422, detail="この候補は既に処理済みです")

        overrides = body or FalsificationCandidateConfirmRequest()
        statement = (overrides.statement if overrides.statement is not None
                     else str(candidate.get("statement") or "")).strip()
        kind = (overrides.kind if overrides.kind is not None
                else str(candidate.get("kind") or "")).strip()
        reason = (overrides.reason if overrides.reason is not None
                  else str(candidate.get("reason") or "")).strip()
        evidence_quote = (overrides.evidence_quote if overrides.evidence_quote is not None
                          else str(candidate.get("evidence_quote") or "")).strip()
        # reachability は人間専用語彙（SL3）— 候補は持たないため、確定時に必ず人間が
        # 選択する（未選択時は既定 unassessed）。
        reachability = (overrides.reachability if overrides.reachability is not None
                         else "unassessed").strip()

        _validate_falsification_condition_fields(statement, kind, reason, [], evidence_quote, reachability)

        condition = {
            "condition_id": str(uuid.uuid4()),
            "statement": statement,
            "kind": kind,
            "reachability": reachability,
            "evidence_ids": [],
            "evidence_quote": evidence_quote,
            # 確定時に帰属は教員に付く（LLM ではなく人間の記帳になる, SL3）
            "recorded_by": str(current_user.get("id") or ""),
            "reason": reason,
            "recorded_at": _now_iso(),
            "from_candidate_id": candidate_id,
        }
        candidate["status"] = "confirmed"
        candidate["confirmed_by"] = str(current_user.get("id") or "")
        candidate["confirmed_at"] = _now_iso()
        session.execute(
            sa_text("""
                UPDATE epistemic_ledger
                SET falsification_conditions = falsification_conditions || CAST(:cond AS jsonb),
                    falsification_candidates = CAST(:candidates AS jsonb),
                    updated_at = now()
                WHERE id = CAST(:lid AS uuid)
            """),
            {
                "cond": json.dumps([condition], ensure_ascii=False),
                "candidates": json.dumps(candidates, ensure_ascii=False),
                "lid": row[0],
            },
        )
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        logger.exception("Failed to confirm falsification candidate for %s/%s", target_type, target_id)
        raise HTTPException(status_code=500, detail="Failed to confirm falsification candidate")
    finally:
        session.close()

    _record_doubt_event(
        AUDIT_ENTITY_LEDGER, f"{target_type}:{target_id}", "candidate", "falsification_added",
        current_user.get("id"),
        {
            "document_id": doc,
            "action": "falsification_candidate_confirm",
            "candidate_id": candidate_id,
            "condition_id": condition["condition_id"],
        },
    )
    return {"ok": True, "condition": _falsification_condition_out(condition)}


@admin_router.post("/ledger/{target_type}/{target_id}/falsification-candidates/{candidate_id}/dismiss")
def dismiss_falsification_candidate(
    target_type: str,
    target_id: str,
    candidate_id: str,
    document_id: str | None = None,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """反証条件候補の却下（P4: 行は dismissed で保持する）。"""
    _require_ledger_target_type(target_type)
    # IK-0900: 対象の教材の編集権限（閲覧のみの教員は書けない）。IK-0899: 行キーの論文。
    doc = _require_editable_ledger_target(
        target_type, target_id, current_user, document_id=document_id
    )
    session = _pg_session()
    try:
        row = _fetch_ledger_row(session, target_type, target_id, document_id=doc)
        if row is None:
            raise HTTPException(status_code=404, detail="Ledger entry not found")
        candidates = [c for c in _jsonb_list(row[14]) if isinstance(c, dict)]
        found = False
        for candidate in candidates:
            if str(candidate.get("candidate_id") or "") == candidate_id:
                candidate["status"] = "dismissed"
                candidate["dismissed_by"] = str(current_user.get("id") or "")
                candidate["dismissed_at"] = _now_iso()
                found = True
                break
        if not found:
            raise HTTPException(status_code=404, detail="Falsification candidate not found")
        session.execute(
            sa_text("""
                UPDATE epistemic_ledger
                SET falsification_candidates = CAST(:candidates AS jsonb), updated_at = now()
                WHERE id = CAST(:lid AS uuid)
            """),
            {"candidates": json.dumps(candidates, ensure_ascii=False), "lid": row[0]},
        )
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        logger.exception("Failed to dismiss falsification candidate for %s/%s", target_type, target_id)
        raise HTTPException(status_code=500, detail="Failed to dismiss falsification candidate")
    finally:
        session.close()

    _record_doubt_event(
        AUDIT_ENTITY_LEDGER, f"{target_type}:{target_id}", "candidate", "dismissed",
        current_user.get("id"),
        {"document_id": doc, "action": "falsification_candidate_dismiss", "candidate_id": candidate_id},
    )
    return {"ok": True, "candidate_id": candidate_id, "status": "dismissed"}


class _SimulationAuditAdapter:
    """SB層 store の ``record_audit(**kw)`` を ``_record_doubt_event`` へ写す（コミット後に記帳）。"""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def __call__(self, **kwargs: Any) -> None:
        self.events.append(dict(kwargs))

    def flush(self) -> None:
        for event in self.events:
            metadata = dict(event.get("metadata") or {})
            metadata.setdefault("action", event.get("action"))
            _record_doubt_event(
                AUDIT_ENTITY_SIMULATION,
                str(event.get("entity_id") or ""),
                str(event.get("old_status") or ""),
                str(event.get("new_status") or ""),
                event.get("actor_id"),
                metadata,
            )


def _decide_simulation_candidate(
    target_type: str, target_id: str, candidate_id: str, action: str, current_user: dict, *, reason: str = "",
    document_id: str | None = None,
) -> dict:
    _require_ledger_target_type(target_type)
    doc = _require_editable_ledger_target(target_type, target_id, current_user, document_id=document_id)
    audit = _SimulationAuditAdapter()
    session = _pg_session()
    try:
        try:
            outcome = simulation_store.decide_ledger_candidate(
                session,
                target_type=target_type,
                target_id=target_id,
                candidate_id=candidate_id,
                action=action,
                actor_id=str(current_user.get("id") or ""),
                record_audit=audit,
                reason=reason,
                document_id=doc,
            )
        except simulation_store.SimulationNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc) or "台帳候補が見つかりません。") from exc
        except simulation_store.SimulationStateError as exc:
            session.rollback()
            raise HTTPException(status_code=422, detail=str(exc) or "この候補は既に処理済みです。") from exc
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        logger.exception("Failed to decide simulation candidate for %s/%s", target_type, target_id)
        raise HTTPException(status_code=500, detail="台帳候補の判断を保存できませんでした。")
    finally:
        session.close()
    audit.flush()
    return {
        "candidate": _simulation_candidate_out(outcome.get("candidate") or {}),
        "verification_status": str(outcome.get("verification_status") or ""),
    }


@admin_router.post("/ledger/{target_type}/{target_id}/simulation-candidates/{candidate_id}/confirm")
def confirm_simulation_candidate(
    target_type: str,
    target_id: str,
    candidate_id: str,
    document_id: str | None = None,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """検証シミュレーションの台帳候補の確定（SB1: indirectly_supported 止まり）。"""
    return _decide_simulation_candidate(
        target_type, target_id, candidate_id, "confirm", current_user, document_id=document_id
    )


@admin_router.post("/ledger/{target_type}/{target_id}/simulation-candidates/{candidate_id}/dismiss")
def dismiss_simulation_candidate(
    target_type: str,
    target_id: str,
    candidate_id: str,
    body: dict | None = Body(default=None),
    document_id: str | None = None,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """検証シミュレーションの台帳候補の見送り（配列内の status 遷移・削除しない）。

    body の ``reason`` を候補に残す（第 21 周 (g): 見送りの理由の置き場が無かった。空でもよい — 教員側の弁を増やさない）。
    """
    reason = str((body or {}).get("reason") or "") if isinstance(body, dict) else ""
    return _decide_simulation_candidate(
        target_type, target_id, candidate_id, "dismiss", current_user, reason=reason, document_id=document_id
    )


@admin_router.post("/courses/{course_id}/falsification-candidates/refresh")
def refresh_falsification_candidates(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """反証条件候補の非同期リフレッシュ（P6: 同期パスに LLM を入れない）。

    範囲はコースの教材の document 集合（IK-0852）。範囲が空なら起動しない（IK-0853）。
    """
    document_ids = _course_scope_document_ids(course_id, current_user)
    if not document_ids:
        return {"ok": True, "scheduled": False, "reason": "no_documents", "note": SCOPE_NO_DOCUMENTS_NOTE}
    maybe_schedule_falsification_candidates(course_id=course_id, document_ids=document_ids)
    return {"ok": True, "scheduled": True, "note": BATCH_SCHEDULED_NOTE}


# ---------------------------------------------------------------------------
# SL-2: 観測の反実仮想 — 観測系 claim の一覧（多段同定, 非LLM）
# ---------------------------------------------------------------------------


@admin_router.get("/courses/{course_id}/observation-targets")
def get_observation_targets(
    course_id: str,
    document_id: str | None = None,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """観測系 claim の一覧（「観測を仮に倒す」の選択肢。identified_via 併記・数値なし）。

    course_id 直指定なので、集約の前に編集権限ゲートを通す（P0 fail-closed）。
    範囲はコースの教材の document 集合（IK-0852）。``document_id``（query・IK-0903）を
    渡すと、開いているグラフの論文 1 件に絞る（コースの教材に含まれる論文だけ。外なら 404）。
    """
    document_ids = _course_scope_document_ids(course_id, current_user)
    wanted = normalize_document_id(document_id)
    if document_id:
        if not wanted or wanted not in {d.lower() for d in document_ids}:
            raise HTTPException(status_code=404, detail="Document not found in course")
        document_ids = [wanted]
    session = _pg_session()
    try:
        targets = observation_claim_targets(session, document_ids=document_ids)
        return {
            "targets": targets,
            **({} if document_ids else {"reason": "no_documents", "note": SCOPE_NO_DOCUMENTS_NOTE}),
        }
    finally:
        session.close()


# ---------------------------------------------------------------------------
# D1-5: 素朴な問いの計器化
# ---------------------------------------------------------------------------


@admin_router.get("/courses/{course_id}/naive-signals")
def get_naive_signals(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """anchor 単位の k-匿名集計（k=3, n<3 セル非表示、件数はレンジ表示のみ）。

    学習痕跡由来の集約なので、k-匿名の前に編集権限ゲートを通す（P0 fail-closed。
    無関係な教員が他コースの学習者信号を読めないようにする）。
    """
    _require_editable_course_or_404(course_id, current_user)
    return aggregate_naive_signals(course_id)


# ---------------------------------------------------------------------------
# D2-1: 負荷度
# ---------------------------------------------------------------------------


@admin_router.post("/courses/{course_id}/load/recompute")
def recompute_course_load(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """負荷度の再計算（決定論的バッチ）。レスポンスに生スコアは含めない。

    範囲はコースの教材の document 集合（IK-0852）。
    """
    document_ids = _course_scope_document_ids(course_id, current_user)
    result = recompute_load_scores(document_ids=document_ids)
    out = {
        "ok": not result.get("failed", False),
        "updated": int(result.get("updated") or 0),
        "note": LOAD_RECOMPUTED_NOTE,
    }
    if not document_ids:
        out.update({"reason": "no_documents", "note": SCOPE_NO_DOCUMENTS_NOTE})
    return out


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


def _insert_assumption_ledger_row(session, assumption_id: str, *, course_id: str, document_id: str) -> None:
    """確定した前提の台帳行（既定 'untested'・スコープ空欄）。document が無ければ作らない。"""
    if not normalize_document_id(document_id):
        return
    session.execute(
        sa_text("""
            INSERT INTO epistemic_ledger
                (target_id, target_type, document_id, course_id, verification_status)
            VALUES (:tid, 'assumption', CAST(:doc AS uuid), :course, 'untested')
            ON CONFLICT ON CONSTRAINT epistemic_ledger_document_target_key DO NOTHING
        """),
        {"tid": assumption_id, "doc": normalize_document_id(document_id), "course": course_id},
    )


@admin_router.get("/courses/{course_id}/assumptions")
def list_assumptions(
    course_id: str,
    status: str = "candidate",
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """前提一覧（出所つき）。dismissed もフィルタで確認できる（P4: 消えない）。

    範囲（IK-0852）: コースの教材の document に掛かる前提（``document_ids`` が重なる）∪
    このコースの画面で記帳された前提（出所の course_id）∪ どの論文・コースにも
    結ばれていない前提（記帳時に範囲を持たなかったもの。従来どおり見せる — P4）。
    """
    document_ids = _course_scope_document_ids(course_id, current_user)
    allowed = ("candidate", "confirmed", "operationalized", "dismissed", "all")
    if status not in allowed:
        raise HTTPException(status_code=422, detail=f"invalid status filter: {status}")
    session = _pg_session()
    try:
        filters = [
            "(course_id = :course"
            " OR document_ids ?| CAST(:doc_ids AS text[])"
            " OR (course_id = '' AND jsonb_array_length(document_ids) = 0))"
        ]
        params: dict[str, Any] = {"course": course_id, "doc_ids": document_ids}
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
        # IK-0853 / IK-0904: マイニングの結果を受付だけで終わらせない（範囲にマイニング由来の
        # 候補があるかを、一覧を読むたびに事実で添える）。
        mined_present = session.execute(
            sa_text(
                "SELECT 1 FROM assumption_nodes WHERE origin = 'mined_gap' AND status = 'candidate'"
                " AND (course_id = :course OR document_ids ?| CAST(:doc_ids AS text[])) LIMIT 1"
            ),
            {"course": course_id, "doc_ids": document_ids},
        ).fetchone() is not None
        return {
            "course_id": course_id,
            "status": status,
            "items": items,
            "mining_note": ASSUMPTION_MINING_NOTES["found" if mined_present else "none_in_scope"],
        }
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
    # IK-0900: 結ぶコース・論文は編集できるものだけ（不在と権限なしは同一の 404）。
    if body.course_id:
        _require_editable_course_or_404(body.course_id, current_user)
    for doc in body.document_ids:
        if str(doc or "").strip():
            _require_editable_document_or_404(str(doc), current_user)
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
        # 確定と同時に台帳行を自動生成（既定 untested・スコープ空欄）。
        # 行キー (document_id, target_type, target_id)（IK-0899）。前提の行の document は
        # 結ばれた論文の先頭（ledger_targets と同じ規則）。論文に結ばれていない前提は、
        # document の無い行を作らない（記帳の時に作る）。
        _insert_assumption_ledger_row(
            session, assumption_id, course_id=body.course_id or "",
            document_id=next((str(d) for d in body.document_ids if str(d or "").strip()), ""),
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


def _require_editable_assumption(assumption_id: str, current_user: dict) -> None:
    """前提の確定・却下の権限（IK-0900）。

    前提が結ばれたコース（出所）か論文のどれかを編集できる教員だけ。どのコースにも論文にも
    結ばれていない前提（手動の記帳）は TEACHER 以上なら扱える（従来どおり）。不在と権限なしは
    同一の 404。
    """
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT course_id, document_ids FROM assumption_nodes WHERE id::text = :aid"),
            {"aid": assumption_id},
        ).fetchone()
    finally:
        session.close()
    if row is None:
        raise HTTPException(status_code=404, detail="Assumption not found")
    course_id = str(row[0] or "").strip()
    document_ids = [str(d) for d in _jsonb_list(row[1]) if str(d or "").strip()]
    if not course_id and not document_ids:
        return
    if current_user.get("role") == ROLE_SYSTEM_ADMIN:
        return
    if course_id:
        try:
            _require_editable_course_or_404(course_id, current_user)
            return
        except HTTPException:
            pass
    for doc in document_ids:
        try:
            _require_editable_document_or_404(doc, current_user)
            return
        except HTTPException:
            continue
    raise HTTPException(status_code=404, detail="Assumption not found")


@admin_router.post("/assumptions/{assumption_id}/confirm")
def confirm_assumption(
    assumption_id: str,
    body: AssumptionConfirmRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """候補の確定 = 「専門家が確かにこれは暗黙の前提だと引き受ける」行為。reason 必須。"""
    if not body.reason.strip():
        raise HTTPException(status_code=422, detail="reason が必要です")
    _require_editable_assumption(assumption_id, current_user)
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
        # 確定時に台帳行を自動生成（既定 untested・スコープ空欄。行キーは IK-0899）。
        _insert_assumption_ledger_row(
            session, assumption_id, course_id=course_id,
            document_id=resolve_ledger_document_id(
                session, target_type="assumption", target_id=assumption_id, document_id=None
            ) or "",
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
    _require_editable_assumption(assumption_id, current_user)
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
    """経路A（導出の隙間の反転）の非同期実行。

    範囲はコースの教材の document 集合（IK-0852）。範囲が空なら起動しない（IK-0853）。
    """
    document_ids = _course_scope_document_ids(course_id, current_user)
    if not document_ids:
        return {"ok": True, "scheduled": False, "reason": "no_documents", "note": SCOPE_NO_DOCUMENTS_NOTE}
    maybe_schedule_assumption_mining(document_ids=document_ids, course_id=course_id)
    return {"ok": True, "scheduled": True, "note": BATCH_SCHEDULED_NOTE}


@admin_router.post("/courses/{course_id}/corpus-audit/run")
def run_corpus_audit_route(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """経路B（コーパス横断の監査）の実行（非LLM・同期でも軽量）。

    範囲はコースの教材の document 集合（IK-0852）。``reason`` は実行しなかった理由の語彙
    （``no_documents`` = 教材が無い / ``no_analysis`` = 教材に解析グラフが無い /
    ``corpus_too_small`` = 論文が少ない）。画面はこれを事実文にする（IK-0853）。
    """
    document_ids = _course_scope_document_ids(course_id, current_user)
    result = run_corpus_audit(document_ids=document_ids, course_id=course_id)
    out = {
        "ok": not result.get("failed", False),
        "skipped": bool(result.get("skipped")),
        "documents": int(result.get("documents") or 0),
        "registered": int(result.get("registered") or 0),
    }
    if result.get("reason"):
        out["reason"] = str(result["reason"])
    if result.get("failed"):
        note_key = "failed"
    elif out["skipped"]:
        note_key = out.get("reason") or "corpus_too_small"
    else:
        note_key = "registered" if out["registered"] else "none_found"
    out["note"] = CORPUS_AUDIT_NOTES.get(note_key, "")
    return out


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
    範囲はコースの教材の document 集合（IK-0852。台帳行の course_id 列では絞らない）。
    """
    document_ids = _course_scope_document_ids(course_id, current_user)
    session = _pg_session()
    try:
        p50, p90, p99 = load_percentiles(session, document_ids=document_ids)
        rows = []
        if document_ids:
            rows = session.execute(
                sa_text("""
                    SELECT l.target_id, l.target_type, l.verification_status,
                           l.verification_scopes, l.consensus_explicit, l.load_score,
                           a.status, l.document_id::text
                    FROM epistemic_ledger l
                    LEFT JOIN assumption_nodes a
                      ON l.target_type = 'assumption' AND a.id::text = l.target_id
                    WHERE l.document_id::text = ANY(:doc_ids)
                      AND l.load_score IS NOT NULL
                """),
                {"doc_ids": document_ids},
            ).fetchall()

        labels = LedgerTargetLabels(session)
        exclusion = ClaimChainExclusion(session)
        points = []
        for row in rows:
            target_type = str(row[1])
            row_document_id = str(row[7] or "") if len(row) > 7 else ""
            # IK-0933: claim チェーンの導出ステップは前提ではない（理論モジュール層と同じ判定）。
            if exclusion.excludes(target_type, str(row[0]), row_document_id):
                continue
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
                # IK-0901: 表示名の正本。IK-0899: 行キーの論文（台帳詳細を開くときに添える）。
                "label": labels.label(target_type, str(row[0]), document_id=row_document_id),
                "document_id": row_document_id,
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
        points.sort(key=lambda p: (p["target_type"], p["target_id"], p["document_id"]))
        return {
            "course_id": course_id,
            # 範囲が空かどうか（IK-0853: 空の地図の理由を画面が言い分ける。件数は出さない）。
            "has_documents": bool(document_ids),
            **(
                {"empty_note": (ATLAS_EMPTY_NOTE if document_ids else SCOPE_NO_DOCUMENTS_NOTE),
                 "empty_hint": STUDIO_LEDGER_HINT}
                if not points else {}
            ),
            # 軸ラベルは事実記述のみ（ゾーン名・煽り文句を持たない）
            "axes": {"x": "検証スコープの被覆", "y": "依存の広がり"},
            "points": points,
            **({"population_note": CLAIM_CHAIN_EXCLUDED_NOTE} if exclusion.excluded else {}),
        }
    finally:
        session.close()


# ---------------------------------------------------------------------------
# P4-5: 根拠の線（Evidence Lines, migration 083 / X-5）
#
# 正本: docs/features/knowledge_transfer_design.md §8。
# verification_scopes（どこで確かめられたか）/ falsification_conditions（何が起これば
# 覆るか）に続く第3の軸「どの経路で支えられているか」。**人間の記帳専用**で、
# LLM worker / ledger_builder からの書き込み経路は作らない（SL3 と同型）。
# 削除 API を作らず、訂正は PATCH（KT5: 情報を落とさない）。
# ---------------------------------------------------------------------------


#: 台帳対象 → その対象が属する document を引く SQL（live 行のみ）。
#: assumption は複数 document にまたがり得るので配列列を読む（下の解決関数を参照）。
_TARGET_DOCUMENT_SQL: dict[str, str] = {
    "claim": "SELECT document_id::text FROM theory_claims_live WHERE id::text = :tid",
    "component": "SELECT document_id::text FROM theory_components_live WHERE id::text = :tid",
    "equation": (
        "SELECT document_id::text FROM knowledge_equations "
        "WHERE id::text = :tid AND superseded_at IS NULL"
    ),
}

#: 権限が確かめられない書き込みは、不在と同じ 404 に畳む（detail も同一）。
_LEDGER_TARGET_NOT_FOUND = "Ledger target not found"


def _target_document_ids(
    session, target_type: str, target_id: str, *, document_id: str | None = None
) -> list[str]:
    """台帳対象が属する document の id を引く（解決できなければ空）。

    IK-0899: 論文の中でしか一意でない対象（グラフのノード id・式の agent ID）は、
    指定された ``document_id`` の 1 件だけ（無ければ空 = 閉じる）。id だけで引くと
    同じ番号の対象を持つ別の論文に着地する。
    """
    if target_requires_document(target_type, target_id):
        doc = normalize_document_id(document_id)
        return [doc] if doc else []
    ids: list[str] = []
    sql = _TARGET_DOCUMENT_SQL.get(target_type)
    if sql:
        try:
            row = session.execute(sa_text(sql), {"tid": target_id}).fetchone()
        except Exception:  # noqa: BLE001 — id 形が UUID でない等。解決できない = 空。
            logger.debug("ledger target document lookup failed", exc_info=True)
            row = None
        if row and row[0]:
            ids.append(str(row[0]))
    elif target_type == "assumption":
        try:
            row = session.execute(
                sa_text("SELECT document_ids FROM assumption_nodes WHERE id::text = :tid"),
                {"tid": target_id},
            ).fetchone()
        except Exception:  # noqa: BLE001
            logger.debug("assumption document lookup failed", exc_info=True)
            row = None
        if row and row[0]:
            ids.extend(str(v) for v in _jsonb_list(row[0]) if str(v or "").strip())
    # 台帳行に document_id が刻まれていればそれも候補にする（後付けの対象で使う）。
    ledger = _fetch_ledger_row(session, target_type, target_id, document_id=document_id)
    if ledger is not None and ledger[3]:
        ids.append(str(ledger[3]))
    seen: set[str] = set()
    return [i for i in ids if i and not (i in seen or seen.add(i))]


def _scoped_target_in_document(session, target_type: str, target_id: str, document_id: str) -> bool:
    """論文の中でしか一意でない対象が、その論文に実在するか（IK-0899・書き込み前の確認）。

    既存の台帳行・理論操作グラフのノード・式の行のどれかに現れれば実在とみなす。
    推測で別の論文の対象に記帳させない（無ければ 404 に畳む）。
    """
    checks = [
        (
            "SELECT 1 FROM epistemic_ledger WHERE target_id = :tid AND target_type = :ttype "
            "AND document_id = CAST(:doc AS uuid) LIMIT 1"
        ),
        (
            "SELECT 1 FROM theory_component_graphs g, "
            "jsonb_array_elements(COALESCE(g.graph_json -> 'nodes', '[]'::jsonb)) n "
            "WHERE g.document_id = CAST(:doc AS uuid) "
            "AND (n ->> 'component_id' = :tid OR n ->> 'id' = :tid) LIMIT 1"
        ),
    ]
    if target_type == "equation":
        checks.append(
            "SELECT 1 FROM knowledge_equations_live WHERE document_id = CAST(:doc AS uuid) "
            "AND agent_equation_id = :tid LIMIT 1"
        )
    for sql in checks:
        try:
            row = session.execute(
                sa_text(sql), {"tid": target_id, "ttype": target_type, "doc": document_id}
            ).fetchone()
        except Exception:  # noqa: BLE001
            logger.debug("scoped ledger target lookup failed", exc_info=True)
            try:
                session.rollback()
            except Exception:  # noqa: BLE001
                pass
            row = None
        if row is not None:
            return True
    return False


def _require_editable_ledger_target(
    target_type: str, target_id: str, current_user: dict, *, document_id: str | None = None
) -> str:
    """台帳の対象単位の書き込み権限（P4-R8 → IK-0900 で全書き込みルートへ）。

    `_require_teacher` は「TEACHER 以上」しか保証しない。台帳への記帳は対象の
    **教材単位の編集権限**を要求する（doubt.py 冒頭の規律。course 直指定の経路が
    `_require_editable_course_or_404` を通すのと同型）。対象が解決できない・
    編集できないはどちらも同一の 404（存在の有無を漏らさない = fail-closed）。

    IK-0899: 論文の中でしか一意でない対象は ``document_id`` が必須（無ければ 422）で、
    その論文に対象が実在しなければ 404。

    Returns:
        権限を確かめた document_id（行キーと監査の document_id に使う）。
    """
    if target_requires_document(target_type, target_id) and not normalize_document_id(document_id):
        raise HTTPException(status_code=422, detail=LEDGER_DOCUMENT_REQUIRED_NOTE)
    session = _pg_session()
    try:
        document_ids = _target_document_ids(
            session, target_type, target_id, document_id=document_id
        )
        if (
            document_ids
            and target_requires_document(target_type, target_id)
            and not _scoped_target_in_document(session, target_type, target_id, document_ids[0])
        ):
            document_ids = []
    finally:
        session.close()
    if not document_ids:
        raise HTTPException(status_code=404, detail=_LEDGER_TARGET_NOT_FOUND)
    for doc in document_ids:
        try:
            _require_editable_document_or_404(doc, current_user)
        except HTTPException:
            continue
        return doc
    raise HTTPException(status_code=404, detail=_LEDGER_TARGET_NOT_FOUND)


def _ledger_target_access(
    target_type: str, target_id: str, current_user: dict, *, document_id: str | None = None
) -> dict:
    """台帳 GET の閲覧ゲートと ``can_edit``（IK-0900）。

    閲覧できない（対象の document を 1 つも閲覧できない）なら 404。``can_edit`` は対象の
    document のどれかを編集できるか（SYSTEM_ADMIN は常に真）。``document_id`` は
    行キーに使う document（閲覧できたもの）。
    """
    if target_requires_document(target_type, target_id) and not normalize_document_id(document_id):
        raise HTTPException(status_code=422, detail=LEDGER_DOCUMENT_REQUIRED_NOTE)
    session = _pg_session()
    try:
        document_ids = _target_document_ids(
            session, target_type, target_id, document_id=document_id
        )
    finally:
        session.close()
    viewable: str | None = None
    for doc in document_ids:
        access = resolve_document_access(str(current_user.get("id") or ""), doc)
        if not access.found:
            continue
        if access.can_edit or current_user.get("role") == ROLE_SYSTEM_ADMIN:
            return {"document_id": doc, "can_edit": True}
        if access.can_view and viewable is None:
            viewable = doc
    if viewable is None:
        raise HTTPException(status_code=404, detail="Ledger entry not found")
    return {"document_id": viewable, "can_edit": False}


class EvidenceLineCreateRequest(BaseModel):
    line_kind: str = ""
    reason: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    equation_ids: list[str] = Field(default_factory=list)


class EvidenceLinePatchRequest(BaseModel):
    line_kind: str | None = None
    reason: str | None = None
    evidence_ids: list[str] | None = None
    claim_ids: list[str] | None = None
    equation_ids: list[str] | None = None


@admin_router.post("/ledger/{target_type}/{target_id}/evidence-lines")
def add_evidence_line(
    target_type: str,
    target_id: str,
    body: EvidenceLineCreateRequest,
    document_id: str | None = None,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """根拠の線の手動記帳（人間専用の記帳先, P4-5）。帰属は認証ユーザー。"""
    _require_ledger_target_type(target_type)
    # 形の検証は DB を開く前（不正な本文は対象の有無に関わらず 422）。
    _validate_evidence_line_fields(
        body.line_kind, body.reason, body.evidence_ids, body.claim_ids, body.equation_ids,
    )
    # P4-R8: TEACHER であることだけでは足りない（対象教材の編集権限を要求する）。
    doc = _require_editable_ledger_target(
        target_type, target_id, current_user, document_id=document_id
    )
    line = {
        "line_id": str(uuid.uuid4()),
        "line_kind": body.line_kind,
        "evidence_ids": [str(e) for e in body.evidence_ids],
        "claim_ids": [str(c) for c in body.claim_ids],
        "equation_ids": [str(e) for e in body.equation_ids],
        # 帰属は常にサーバ側で認証ユーザーから採る（body の申告は受け取らない）。
        "recorded_by": str(current_user.get("id") or ""),
        "reason": body.reason.strip(),
        "recorded_at": _now_iso(),
    }
    session = _pg_session()
    try:
        row = _get_or_create_ledger_row(session, target_type, target_id, document_id=doc)
        if row is None:
            raise HTTPException(status_code=500, detail="Failed to create ledger entry")
        session.execute(
            sa_text("""
                UPDATE epistemic_ledger
                SET evidence_lines = evidence_lines || CAST(:line AS jsonb),
                    updated_at = now()
                WHERE id = CAST(:lid AS uuid)
            """),
            {"line": json.dumps([line], ensure_ascii=False), "lid": row[0]},
        )
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        logger.exception("Failed to add evidence line for %s/%s", target_type, target_id)
        raise HTTPException(status_code=500, detail="Failed to add evidence line")
    finally:
        session.close()

    _record_doubt_event(
        AUDIT_ENTITY_LEDGER, f"{target_type}:{target_id}", "", "evidence_line_added",
        current_user.get("id"),
        {"document_id": doc, "action": "evidence_line_add", "line_id": line["line_id"], "line_kind": line["line_kind"]},
    )
    return {"ok": True, "evidence_line": _evidence_line_out(line)}


@admin_router.patch("/ledger/{target_type}/{target_id}/evidence-lines/{line_id}")
def patch_evidence_line(
    target_type: str,
    target_id: str,
    line_id: str,
    body: EvidenceLinePatchRequest,
    document_id: str | None = None,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """根拠の線の訂正（訂正後も必須項目を再検証する。削除はしない, KT5）。"""
    _require_ledger_target_type(target_type)
    # P4-R8: 記帳と同じ編集権限ゲート（訂正も書き込み）。
    doc = _require_editable_ledger_target(
        target_type, target_id, current_user, document_id=document_id
    )
    session = _pg_session()
    try:
        row = _fetch_ledger_row(session, target_type, target_id, document_id=doc)
        if row is None:
            raise HTTPException(status_code=404, detail="Ledger entry not found")
        lines = [line for line in _jsonb_list(row[15]) if isinstance(line, dict)]
        target_line = None
        for line in lines:
            if str(line.get("line_id") or "") == line_id:
                target_line = line
                break
        if target_line is None:
            raise HTTPException(status_code=404, detail="Evidence line not found")

        old_line = dict(target_line)
        if body.line_kind is not None:
            target_line["line_kind"] = str(body.line_kind).strip()
        if body.reason is not None:
            target_line["reason"] = str(body.reason).strip()
        for field_name, value in (
            ("evidence_ids", body.evidence_ids),
            ("claim_ids", body.claim_ids),
            ("equation_ids", body.equation_ids),
        ):
            if value is not None:
                target_line[field_name] = [str(v) for v in value]

        _validate_evidence_line_fields(
            str(target_line.get("line_kind") or ""),
            str(target_line.get("reason") or ""),
            list(target_line.get("evidence_ids") or []),
            list(target_line.get("claim_ids") or []),
            list(target_line.get("equation_ids") or []),
        )

        session.execute(
            sa_text("""
                UPDATE epistemic_ledger
                SET evidence_lines = CAST(:lines AS jsonb), updated_at = now()
                WHERE id = CAST(:lid AS uuid)
            """),
            {
                "lines": json.dumps(lines, ensure_ascii=False),
                "lid": row[0],
            },
        )
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        logger.exception("Failed to patch evidence line for %s/%s", target_type, target_id)
        raise HTTPException(status_code=500, detail="Failed to patch evidence line")
    finally:
        session.close()

    _record_doubt_event(
        AUDIT_ENTITY_LEDGER, f"{target_type}:{target_id}", "evidence_line_updated",
        "evidence_line_updated", current_user.get("id"),
        {"document_id": doc, "action": "evidence_line_patch", "line_id": line_id, "old": old_line},
    )
    return {"ok": True, "evidence_line": _evidence_line_out(target_line)}


# ---------------------------------------------------------------------------
# D3-1: 疑義（Challenge）
# ---------------------------------------------------------------------------


class ChallengeCreateRequest(BaseModel):
    challenge_type: str
    reason: str
    course_id: str = ""
    # P4-5（knowledge_transfer_design.md §8 / X-6）: 疑義の**向き**と対象要素。
    # 既定は direct = 主張そのものへ（既存クライアントの body は不変で動く）。
    challenge_mode: str = "direct"
    target_element_ref: dict = Field(default_factory=dict)


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
    if body.challenge_mode not in CHALLENGE_MODES:
        raise HTTPException(status_code=422, detail=f"invalid challenge_mode: {body.challenge_mode}")
    if not body.reason.strip():
        raise HTTPException(status_code=422, detail="reason（本人の言葉）が必要です")
    element_ref = _normalize_target_element_ref(body.target_element_ref)
    # IK-0900: 疑義は帰属必須の公開の学術行為なので、対象の論文を**閲覧できる**教員なら
    # 残せる（編集権限までは求めない）。閲覧できない対象は不在と同じ 404。
    _ledger_target_access(target_type, target_id, current_user)

    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                INSERT INTO challenges
                    (target_id, target_type, challenger_id, challenge_type, reason, course_id,
                     challenge_mode, target_element_ref)
                VALUES
                    (:tid, :ttype, CAST(:uid AS uuid), :ctype, :reason, :course,
                     :cmode, CAST(:element_ref AS jsonb))
                RETURNING id
            """),
            {
                "tid": target_id,
                "ttype": target_type,
                "uid": current_user.get("id"),
                "ctype": body.challenge_type,
                "reason": body.reason.strip(),
                "course": body.course_id or "",
                "cmode": body.challenge_mode,
                "element_ref": json.dumps(element_ref, ensure_ascii=False),
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
         "challenge_type": body.challenge_type, "challenge_mode": body.challenge_mode},
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
    return {
        "ok": True,
        "challenge_id": challenge_id,
        "status": "open",
        "challenge_mode": body.challenge_mode,
        "challenge_mode_label": CHALLENGE_MODE_LABELS.get(
            body.challenge_mode, body.challenge_mode
        ),
        "target_element_ref": element_ref,
    }


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
                       COALESCE(u.display_name, ''), c.created_at::text,
                       c.challenge_mode, c.target_element_ref
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
                    # P4-5: 疑義の向き（既存行は DB の DEFAULT どおり direct）。
                    "challenge_mode": str(r[6] or "direct"),
                    "challenge_mode_label": CHALLENGE_MODE_LABELS.get(
                        str(r[6] or "direct"), str(r[6] or "direct")
                    ),
                    "target_element_ref": _normalize_target_element_ref(_jsonb_dict(r[7])),
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
    # SL-8: 「コーパス外の文献を確認した」旨の人間の記帳（自由記述・非空）。晴れ間は
    # まずコーパスの穴であり、それが空の穴であることを人間が確かめたあとにだけ立つ。
    external_check: str = ""
    reachability: str = "unassessed"


class ProposalPatchRequest(BaseModel):
    status: str | None = None
    reachability: str | None = None


def _require_viewable_challenge_target(challenge_id: str, current_user: dict) -> None:
    """疑義の対象の論文を閲覧できるか（IK-0900）。疑義が無い・閲覧できないは同一の 404。"""
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT target_type, target_id FROM challenges WHERE id::text = :cid"),
            {"cid": challenge_id},
        ).fetchone()
    finally:
        session.close()
    if row is None:
        raise HTTPException(status_code=404, detail="Challenge not found")
    try:
        _ledger_target_access(str(row[0]), str(row[1]), current_user)
    except HTTPException as exc:
        raise HTTPException(status_code=404, detail="Challenge not found") from exc


@admin_router.post("/challenges/{challenge_id}/proposals")
def create_verification_proposal(
    challenge_id: str,
    body: ProposalCreateRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """疑義 → 「この実験・この計算で検証可能」への昇格。

    SL-8: コーパス外の文献確認の記帳（external_check）を必須化。取り下げ済み
    （withdrawn）の疑義からの昇格は記帳整合性バグとして 422 に是正する（§6.2）。
    """
    if not body.proposal.strip():
        raise HTTPException(status_code=422, detail="proposal が必要です")
    if not body.external_check.strip():
        raise HTTPException(status_code=422, detail="コーパス外の文献確認の記録が必要です")
    reachability = str(body.reachability or "unassessed").strip()
    if reachability not in REACHABILITY_LEVELS:
        raise HTTPException(status_code=422, detail=f"invalid reachability: {reachability}")
    # IK-0900: 疑義と同じく、対象の論文を閲覧できる教員なら昇格できる。
    _require_viewable_challenge_target(challenge_id, current_user)

    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT status, course_id FROM challenges WHERE id::text = :cid"),
            {"cid": challenge_id},
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Challenge not found")
        old_status = str(row[0] or "open")
        if old_status == ChallengeStatus.WITHDRAWN.value:
            raise HTTPException(
                status_code=422,
                detail="取り下げ済みの疑義から検証提案へ昇格することはできません",
            )
        course_id = str(row[1] or "")
        proposal_row = session.execute(
            sa_text("""
                INSERT INTO verification_proposals
                    (challenge_id, proposal, proposer_id, course_id, reachability,
                     external_check, external_checked_by)
                VALUES
                    (CAST(:cid AS uuid), :proposal, CAST(:uid AS uuid), :course, :reachability,
                     :external_check, CAST(:uid AS uuid))
                RETURNING id
            """),
            {
                "cid": challenge_id,
                "proposal": body.proposal.strip(),
                "uid": current_user.get("id"),
                "course": course_id,
                "reachability": reachability,
                "external_check": body.external_check.strip(),
            },
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


@admin_router.patch("/proposals/{proposal_id}")
def patch_verification_proposal(
    proposal_id: str,
    body: ProposalPatchRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """検証提案のステータス遷移（proposed→in_progress→completed の前進 +
    任意時点から withdrawn）+ reachability の更新（§6.2、§2-8 の既存欠落を埋める）。
    """
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT status FROM verification_proposals WHERE id::text = :pid"),
            {"pid": proposal_id},
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Verification proposal not found")
        old_status = str(row[0] or ProposalStatus.PROPOSED.value)
        new_status = old_status

        updates = []
        params: dict[str, Any] = {"pid": proposal_id}
        if body.status is not None:
            new_status = str(body.status).strip()
            if new_status not in _PROPOSAL_STATUSES:
                raise HTTPException(status_code=422, detail=f"invalid status: {new_status}")
            if (old_status, new_status) not in _PROPOSAL_ALLOWED_TRANSITIONS:
                raise HTTPException(
                    status_code=422,
                    detail=f"invalid status transition: {old_status} -> {new_status}",
                )
            updates.append("status = :status")
            params["status"] = new_status
        if body.reachability is not None:
            reachability = str(body.reachability).strip()
            if reachability not in REACHABILITY_LEVELS:
                raise HTTPException(status_code=422, detail=f"invalid reachability: {reachability}")
            updates.append("reachability = :reachability")
            params["reachability"] = reachability

        if not updates:
            return {"ok": True, "proposal_id": proposal_id, "status": old_status}

        session.execute(
            sa_text(
                f"UPDATE verification_proposals SET {', '.join(updates)}, updated_at = now() "
                f"WHERE id::text = :pid"
            ),
            params,
        )
        session.commit()
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        logger.exception("Failed to patch verification proposal %s", proposal_id)
        raise HTTPException(status_code=500, detail="Failed to patch verification proposal")
    finally:
        session.close()

    if body.status is not None:
        _record_doubt_event(
            AUDIT_ENTITY_VERIFICATION_PROPOSAL, proposal_id, old_status, new_status,
            current_user.get("id"), {"action": "status_transition"},
        )
    return {"ok": True, "proposal_id": proposal_id, "status": new_status}


@admin_router.get("/courses/{course_id}/open-assumptions")
def get_open_assumptions(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """未検証合意リスト（台帳の投影・編集不可）。高負荷 × 低検証の自動編纂。

    範囲はコースの教材の document 集合（IK-0852）。
    """
    document_ids = _course_scope_document_ids(course_id, current_user)
    session = _pg_session()
    try:
        exclusion = ClaimChainExclusion(session)
        items = compile_open_assumptions(
            session, course_id, document_ids=document_ids, include_challenger_names=True,
            exclusion=exclusion,
        )
        return {
            "course_id": course_id,
            "has_documents": bool(document_ids),
            "items": items,
            # IK-0933: 導出の手順を外したことの事実文（行は台帳に残る — 情報を落とさない）。
            **({"population_note": CLAIM_CHAIN_EXCLUDED_NOTE} if exclusion.excluded else {}),
            **(
                {"empty_note": OPEN_ASSUMPTIONS_EMPTY_NOTE if document_ids else SCOPE_NO_DOCUMENTS_NOTE}
                if not items else {}
            ),
        }
    finally:
        session.close()


# ---------------------------------------------------------------------------
# D3-3: 反実仮想モード
# ---------------------------------------------------------------------------


class ToggledObservation(BaseModel):
    """SL-2: 「観測を仮に倒す」の1件。aspect は Duhem 区別（記帳のみ・伝播に影響しない）。"""

    claim_id: str
    aspect: str = "value"


class CounterfactualComputeRequest(BaseModel):
    toggled_assumption_ids: list[str] = Field(default_factory=list)
    # SL-2: 観測系 claim を仮に倒す（observation_targets から選択）。前提トグルと併用可。
    toggled_observations: list[ToggledObservation] = Field(default_factory=list)
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


def _observation_claim_ids(observations: list) -> list[str]:
    """SL-2: toggled_observations の aspect 語彙を検証し claim_id 列を返す（伝播非改変）。

    aspect（value/systematics）は Duhem 区別の記帳のみで、伝播計算には影響しない
    （観測値を疑うのも較正モデルを疑うのも、依存範囲の計算上は同じ seed, §4.2）。
    """
    ids: list[str] = []
    for obs in observations:
        aspect = str(obs.aspect or "").strip()
        if aspect not in _OBSERVATION_ASPECTS:
            raise HTTPException(status_code=422, detail=f"invalid aspect: {aspect}")
        claim_id = str(obs.claim_id or "").strip()
        if claim_id:
            ids.append(claim_id)
    return ids


def _counterfactual_scope_document_ids(body: "CounterfactualComputeRequest", current_user: dict) -> list[str]:
    """反実仮想の範囲（IK-0852）。

    document_id があればその 1 件（閲覧権限を確かめる。course_id では絞らない — 依存グラフは
    document の成果なので）。無ければ course_id のコースの教材（編集権限ゲート付き）。
    どちらも無ければ空（グラフは空・全トグルが warnings に載る = 失敗を隠さない）。
    """
    document_ref = str(body.document_id or "").strip()
    if document_ref:
        access = resolve_document_access(current_user["id"], document_ref)
        if not access.found or not (
            access.can_view or current_user.get("role") == ROLE_SYSTEM_ADMIN
        ):
            raise HTTPException(status_code=404, detail="Document not found")
        return [str(access.document_id)]
    course_id = str(body.course_id or "").strip()
    if course_id:
        return _course_scope_document_ids(course_id, current_user)
    return []


def _require_at_least_one_toggle(assumption_ids: list[str], observations: list) -> None:
    """既存の「toggled_assumption_ids 空 → 422」を「両方空 → 422」に緩和（§4.2）。"""
    if not assumption_ids and not observations:
        raise HTTPException(
            status_code=422,
            detail="toggled_assumption_ids または toggled_observations のいずれかが必要です",
        )


def _compute_with_observation_scope(
    session, assumption_ids: list, observation_claim_ids: list[str], scope_ids: list[str]
) -> dict:
    """反実仮想の試算（IK-0903: 範囲の外の観測は倒さず、warnings に事実文で残す）。

    倒した観測がこの範囲（開いているグラフの論文）の観測系主張に無いときは、伝播に渡さない。
    主張の agent ID は論文をまたいで同じ番号になりうるので、渡すと別の論文の主張を倒してしまう。
    """
    in_scope: set[str] = set()
    if observation_claim_ids and scope_ids:
        try:
            for item in observation_claim_targets(session, document_ids=scope_ids):
                in_scope.add(str(item.get("claim_id") or ""))
        except Exception:  # noqa: BLE001
            logger.debug("observation scope lookup failed", exc_info=True)
    outside = [c for c in observation_claim_ids if c not in in_scope]
    inside = [c for c in observation_claim_ids if c in in_scope]
    payload = compute_counterfactual(
        session,
        toggled_assumption_ids=[str(a) for a in assumption_ids] + inside,
        document_ids=scope_ids,
    )
    if outside:
        names = []
        for claim_id in outside:
            name = ledger_target_label(
                session, document_id=None, target_type="claim", target_id=claim_id
            )
            if name not in names:
                names.append(name)
        warnings = list(payload.get("warnings") or [])
        warnings.append(OBSERVATION_OUTSIDE_SCOPE_WARNING_PREFIX + "、".join(names))
        payload["warnings"] = warnings
    return payload


@admin_router.post("/counterfactual/compute")
def compute_counterfactual_route(
    body: CounterfactualComputeRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """保存なしの試算。決定論的（同入力同出力）。再構築は計算しない。

    SL-2: toggled_observations（観測系 claim）は toggled_assumption_ids と結合して
    そのまま compute_counterfactual に渡す（seed 解決の既存 fallback が claim id を
    受けられるため、伝播アルゴリズムに変更なし, §4.2）。
    """
    _require_at_least_one_toggle(body.toggled_assumption_ids, body.toggled_observations)
    observation_claim_ids = _observation_claim_ids(body.toggled_observations)
    scope_ids = _counterfactual_scope_document_ids(body, current_user)
    session = _pg_session()
    try:
        return _compute_with_observation_scope(
            session, body.toggled_assumption_ids, observation_claim_ids, scope_ids
        )
    finally:
        session.close()


@admin_router.post("/counterfactual/sessions")
def save_counterfactual_session(
    body: CounterfactualSessionCreateRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """反実仮想セッションの保存（スナップショット）。既定は private。"""
    _require_at_least_one_toggle(body.toggled_assumption_ids, body.toggled_observations)
    observation_claim_ids = _observation_claim_ids(body.toggled_observations)
    if body.shared_scope not in _SHARED_SCOPES:
        raise HTTPException(status_code=422, detail=f"invalid shared_scope: {body.shared_scope}")
    if body.shared_scope == "group" and not body.group_id:
        raise HTTPException(status_code=422, detail="group 共有には group_id が必要です")

    session = _pg_session()
    try:
        payload = _compute_with_observation_scope(
            session, body.toggled_assumption_ids, observation_claim_ids,
            _counterfactual_scope_document_ids(body, current_user),
        )
        collapsed, surviving, indeterminate = snapshot_subgraphs(payload)
        toggled_observations_json = json.dumps(
            [{"claim_id": str(o.claim_id or ""), "aspect": str(o.aspect or "value")}
             for o in body.toggled_observations],
            ensure_ascii=False,
        )
        row = session.execute(
            sa_text("""
                INSERT INTO counterfactual_sessions
                    (owner_id, course_id, document_id, toggled_assumption_ids,
                     toggled_observations,
                     collapsed_subgraph, surviving_subgraph, indeterminate_subgraph,
                     notes, shared_scope, group_id)
                VALUES
                    (CAST(:uid AS uuid), :course, CAST(NULLIF(:doc, '') AS uuid), CAST(:toggled AS jsonb),
                     CAST(:toggled_obs AS jsonb),
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
                "toggled_obs": toggled_observations_json,
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
        {
            "action": "create",
            "toggled_count": len(body.toggled_assumption_ids),
            "toggled_observation_count": len(body.toggled_observations),
        },
    )
    return {
        "ok": True,
        "session_id": session_id,
        "shared_scope_label": SHARED_SCOPE_LABELS.get(body.shared_scope, body.shared_scope),
        "result": payload,
    }


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
        # IK-0854: 画面は内部値ではなくこの日本語を出す（既存の Visibility 語彙）。
        "shared_scope_label": SHARED_SCOPE_LABELS.get(
            str(row[10] or "private"), str(row[10] or "private")
        ),
        "group_id": str(row[11] or ""),
        "created_at": str(row[12] or ""),
        # SL-2: 観測トグルの記録（P4: 情報を落とさない。row[13] は末尾追加のため既存
        # インデックス 0-12 は不変）。
        "toggled_observations": _jsonb_list(row[13]),
    }


_CF_SELECT = """
    SELECT s.id::text, s.owner_id::text, COALESCE(u.display_name, ''),
           s.course_id, s.document_id::text AS document_id, s.toggled_assumption_ids,
           s.collapsed_subgraph, s.surviving_subgraph, s.indeterminate_subgraph,
           s.notes, s.shared_scope, COALESCE(s.group_id::text, ''), s.created_at::text,
           s.toggled_observations
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
    metrics = collect_doubt_metrics()
    # 制度指標カタログへの参照（IG1）。定義は GET /api/indicators/doubt-metrics。
    metrics["indicator_id"] = "doubt-metrics"
    return metrics


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


def _learner_falsification_conditions(conditions: list[dict]) -> list[dict]:
    """SL-1 の学習者向け投影。事実文のみ（記帳者情報・根拠の生データ等は落とす, §8）。"""
    result = []
    for condition in conditions:
        if not isinstance(condition, dict):
            continue
        kind = str(condition.get("kind") or "")
        reachability = str(condition.get("reachability") or "unassessed")
        result.append({
            "statement": str(condition.get("statement") or ""),
            "kind_label": FALSIFICATION_KIND_LABELS.get(kind, kind),
            "reachability_label": REACHABILITY_LABELS.get(reachability, reachability),
            "source_label": "教員の記帳",
        })
    return result


def _learner_evidence_lines_fact(lines: list[dict]) -> str:
    """P4-5 の学習者向け投影。**事実文 1 行**だけ（KT7）。

    記帳者（``recorded_by``）・根拠の ID 群・件数は出さない。種別は記帳された
    ものだけを記帳順の重複なしで並べる（無い種別は言わない = 閉世界）。
    記帳ゼロなら空文字（呼び出し側がキー自体を付けない）。
    """
    kinds: list[str] = []
    for line in lines:
        if not isinstance(line, dict):
            continue
        kind = str(line.get("line_kind") or "")
        label = EVIDENCE_LINE_KIND_LABELS.get(kind)
        if label and label not in kinds:
            kinds.append(label)
    if not kinds:
        return ""
    return f"根拠の線が記帳されています（種類: {'・'.join(kinds)}）。"


def learner_ledger_line(
    session,
    target_type: str,
    target_id: str,
    *,
    document_id: str | None,
    include_support_lines: bool = True,
) -> dict | None:
    """台帳の学習者向け投影1件（読み取り専用）。台帳行が無ければ ``None``。

    ``get_learner_ledger_line``（エンドポイント）と、画面文脈アダプター Phase 4
    （``assistant_screen_adapter_design.md`` §11.3 kind ``verification``）の共通正本。
    **学習者向けの遮断を2箇所に書かない**ため、投影はここだけに置く:
    記帳者 ID を落とし・生スコアを返さず・SL1 の閉世界語彙をそのまま運ぶ。

    ``session`` の open/close は呼び出し側の責務（既存エンドポイントの
    ``try/finally`` 規約をそのまま使う）。

    component（IK-0855）: 部品チップ・画面文脈は theory_components の UUID を、台帳は理論操作
    グラフのノード id を持つ。どちらで来ても ``ledger_targets.resolve_component_ledger_target``
    が台帳を引く対象へ写す（唯一の解決点）。ノードへ写したときは ``target_note`` で
    「どの段の記録か」を 1 行添える。
    """
    target_note = ""
    # IK-0899: ``document_id`` は開いている論文（必須）。グラフのノード id・式の agent ID は
    # 論文の中でしか一意でないので、分からなければ台帳を引かない（None = 一行を出さない）。
    if target_requires_document(target_type, target_id) and not normalize_document_id(document_id):
        return None
    if target_type == "component":
        resolved = resolve_component_ledger_target(session, target_id, document_id=document_id)
        if resolved is None:
            return None
        document_id = str(resolved.get("document_id") or document_id or "")
        if resolved.get("via_graph_node"):
            node_label = str(resolved.get("node_label") or "").strip()
            target_note = (
                LEDGER_COMPONENT_VIA_NODE_NOTE.format(label=node_label)
                if node_label else LEDGER_COMPONENT_VIA_NODE_NOTE_UNNAMED
            )
        target_id = str(resolved["target_id"])
    try:
        row = _fetch_ledger_row(session, target_type, target_id, document_id=document_id)
    except HTTPException:
        return None
    if row is None:
        return None
    status = str(row[5] or "unknown")
    scopes = [_scope_out(s) for s in _jsonb_list(row[6]) if isinstance(s, dict)]
    # 学習者へは記帳者 ID を出さない（帰属は教員向け表示のみ）
    learner_scopes = [
        {k: v for k, v in scope.items() if k in ("condition", "domain", "precision", "system")}
        for scope in scopes
    ]
    falsification_conditions = _learner_falsification_conditions(
        [c for c in _jsonb_list(row[13]) if isinstance(c, dict)]
    )
    # P4-5: 根拠の線は事実文 1 行に畳む（記帳者 ID・根拠 ID・件数を出さない）。
    evidence_lines_fact = _learner_evidence_lines_fact(
        [line for line in _jsonb_list(row[15]) if isinstance(line, dict)]
    )
    # SL-3: 支持線は事実文のみ（内部の構成要素の列挙・記帳者情報は出さない）。
    support_fact_line = ""
    if include_support_lines:
        try:
            # 範囲は台帳行の document（IK-0852: course_id 列では絞らない）。
            row_document = str(row[3] or "")
            support_lines = compute_support_lines(
                session, target_type, target_id,
                document_ids=[row_document] if row_document else [],
            )
            if support_lines:
                support_fact_line = str(support_lines.get("fact_line") or "")
        except Exception:  # noqa: BLE001
            logger.warning(
                "learner support lines computation failed for %s/%s", target_type, target_id,
                exc_info=True,
            )
            support_fact_line = ""
    # IK-0901: 出典タブの行の主語（式は印字番号 / 主張は本文。TeX のラベルや空の主語を出さない）。
    try:
        target_label = ledger_target_label(
            session, document_id=document_id, target_type=target_type, target_id=target_id
        )
    except Exception:  # noqa: BLE001
        target_label = ""
    from core.doubt.target_labels import UNNAMED_TARGET_LABEL as _UNNAMED

    return {
        "target_id": target_id,
        "target_type": target_type,
        **({"target_label": target_label} if target_label and target_label != _UNNAMED else {}),
        "verification_status": status,
        "verification_status_label": _VERIFICATION_STATUS_LABELS.get(status, status),
        "scopes": learner_scopes,
        "scope_coverage": scope_coverage_level(len(scopes)),
        "fact_line": _learner_fact_line(target_type, status, scopes),
        "falsification_conditions": falsification_conditions,
        **({"support_fact_line": support_fact_line} if support_fact_line else {}),
        **({"evidence_lines_fact": evidence_lines_fact} if evidence_lines_fact else {}),
        **({"target_note": target_note} if target_note else {}),
    }


@learning_router.get("/courses/{course_id}/ledger/{target_type}/{target_id}")
def get_learner_ledger_line(
    course_id: str,
    target_type: str,
    target_id: str,
    document_id: str | None = None,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """台帳の正直表示（読み取り専用）。スコープと段階ラベルのみ。

    生スコア・疑義の生一覧は返さない。台帳行が無ければ 404
    （UI はセクション自体を出さない fail-closed）。

    ``document_id``（query）は開いている論文。グラフのノード id・式の agent ID は論文の中でしか
    一意でないので、その対象では必須（無ければ 404 — 別の論文の行を出さない。IK-0899）。
    """
    _require_ledger_target_type(target_type)
    course = get_accessible_course_data(str(current_user.get("id")), course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    # IK-0857: 対象がこのコースの教材に属するときだけ読む（コースの教材 ∩ 対象の document）。
    # 受講中のコースの URL で、教材外（閲覧できない）論文の台帳を読ませない。外なら不在と同じ 404。
    course_documents = list_course_source_document_ids(course)
    session = _pg_session()
    try:
        if target_requires_document(target_type, target_id) and not normalize_document_id(document_id):
            raise HTTPException(status_code=404, detail="Ledger entry not found")
        row_document = normalize_document_id(document_id) or None
        target_documents = set(
            _target_document_ids(session, target_type, target_id, document_id=row_document)
        )
        if target_type == "component":
            resolved = resolve_component_ledger_target(session, target_id, document_id=row_document)
            if resolved is not None and resolved.get("document_id"):
                target_documents.add(str(resolved["document_id"]))
        if not (target_documents & course_documents):
            raise HTTPException(status_code=404, detail="Ledger entry not found")
        line = learner_ledger_line(
            session, target_type, target_id,
            document_id=row_document or next(iter(sorted(target_documents & course_documents)), None),
        )
        if line is None:
            raise HTTPException(status_code=404, detail="Ledger entry not found")
        return line
    finally:
        session.close()


def _fold_learner_open_assumptions(items: list[dict]) -> list[dict]:
    """学習者向けの未検証の前提一覧の整理（IK-0901）。

    - 同じ文面（表示名）の行は先頭の 1 件にまとめ ``merged_note``（ゼミ前ブリーフと同じ規約
      IK-0593 の事実文）を添える。
    - 名前を引けない行は一覧の後ろへ回す（先頭に「名前を引けない対象」を並べない）。
    並びは投影元の決定論順を保つ。件数は出さない。
    """
    from core.doubt.seminar_brief import MERGED_INDISTINCT_NOTE
    from core.doubt.target_labels import UNNAMED_TARGET_LABEL

    seen: dict[tuple, dict] = {}
    named: list[dict] = []
    unnamed: list[dict] = []
    for item in items:
        statement = str(item.get("statement") or "")
        key = (statement, str(item.get("target_type") or ""))
        if statement and key in seen:
            seen[key]["merged_note"] = MERGED_INDISTINCT_NOTE
            continue
        seen[key] = item
        (unnamed if statement in ("", UNNAMED_TARGET_LABEL) else named).append(item)
    return named + unnamed


@learning_router.get("/courses/{course_id}/open-assumptions")
def get_learner_open_assumptions(
    course_id: str,
    current_user: dict = Depends(_get_current_user),
) -> dict:
    """未検証合意リストの閲覧（読み取り専用）。疑義者の氏名は含めない。

    D層「煽らない・数値を見せない」: ``compile_open_assumptions`` は教員向けに
    ``dependent_count``（下流到達数の生値）を載せるため、学習者へ返す前に
    ゼミ前ブリーフ（SB2）と同じ語彙の安全網で落とす。段階は既に
    ``load_level`` / ``scope_coverage`` / 各事実文が持っており、生数値は不要。
    教員向けルート（``/api/admin/doubt/courses/{id}/open-assumptions``）は不変。
    """
    course = get_accessible_course_data(str(current_user.get("id")), course_id)
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    # 範囲はコースの教材の document 集合（IK-0852。台帳行の course_id 列では絞らない）。
    document_ids = sorted(list_course_source_document_ids(course))
    session = _pg_session()
    try:
        exclusion = ClaimChainExclusion(session)
        items = _fold_learner_open_assumptions(compile_open_assumptions(
            session, course_id, document_ids=document_ids, include_challenger_names=False,
            exclusion=exclusion,
        ))
        return strip_learner_numeric_keys({
            "course_id": course_id,
            "has_documents": bool(document_ids),
            "items": items,
            **({"population_note": CLAIM_CHAIN_EXCLUDED_NOTE} if exclusion.excluded else {}),
            **({"empty_note": LEARNER_OPEN_ASSUMPTIONS_EMPTY_NOTE} if not items else {}),
        })
    finally:
        session.close()
