"""カテゴリギャップ候補 — 教員レビュー API（実パス ``/api/admin/cartridges/{id}/atlas/...``）。

正本: ``docs/features/category_gap_candidates_design.md`` §5.4（レビュー UI の裏の API 要件）
/ §5.5（骨格への反映）/ §5.7（監査・ガードレール）。

本ルータの立場（設計書 §2 の不変条項の写像）:

- **KN-3 / AB4 確定は人間**: 本ルータは候補（cluster）に対する教員の**判断**だけを書く。
  骨格 (``atlas_skeletons``) への INSERT / UPDATE は**一切持たない** — 次版下書きへの
  取り込みは「読み取り専用の patch プレビュー → 教員の既存 ``PUT draft``（revision
  楽観ロック）→ ``mark-incorporated`` の刻印」の3手で、書き込むのは常に教員の PUT である
  （ガードレールが構造的に検査する — LS7）。
- **LS9 同期パスに LLM を入れない**: 全エンドポイントが非LLM。patch も
  ``core/atlas_gaps/patching.py`` の決定論生成で、日次コストゲートを持たない。
- **G1 / PN-2 導出であって記録ではない**: レビューキューは ``derive_candidates`` が
  毎回導出する（完了フラグを持たない。次版に概念が入れば候補は自然に消える）。
- **LS5 数値を見せない**: 候補 DTO は ``core/atlas_gaps/store.py`` が組んだものを
  そのまま返す（生 confidence なし・件数フィールドなし。支持論文はタイトル列挙）。
- **P4 / AB3 情報を落とさない**: DELETE ルートを作らない。見送りは ``dismissed``、
  その取り消しは ``candidate`` への状態遷移で表す。
- **§4.1 裁定**: 1論文の主題は分野のカテゴリではない。反復閾値の適用は store 側
  （``MIN_DOCUMENTS_FOR_CANDIDATE``）にあり、本ルータは閾値を再実装しない。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

import services
from core import atlas
from core import atlas_review_display
from core import atlas_store
from core.atlas_gaps import patching as gap_patching
from core.atlas_gaps import schema as gap_schema
from core.atlas_gaps import store as gap_store
from core.schema import AUDIT_ENTITY_CATEGORY_GAP
from dependencies import _require_teacher

logger = logging.getLogger(__name__)

# /api/admin/cartridges/... 配下（main.py が prefix="/api/admin" でフラット登録する。
# CLAUDE.md Tier 3-17c: admin 系子ルーターは admin.router に include しない）
router = APIRouter(prefix="/cartridges", tags=["Admin"])


# ---------------------------------------------------------------------------
# 事実文（LS1 / AB1: 欠陥語彙・督促語彙を使わない。件数・スコアを書かない）
# ---------------------------------------------------------------------------

_DETAIL_NO_FROZEN_SKELETON = "この分野には凍結済みの骨格がありません。"
_DETAIL_UNKNOWN_ACTION = "操作の指定が正しくありません。"
_DETAIL_DOMAIN_MISMATCH = "この候補は別の分野のものです。"
_DETAIL_DECISION_NOT_FOUND = "この候補についての判断は記録されていません。"
_DETAIL_NOT_ACCEPTED = "この候補はまだ採用されていません。先に採用してください。"
_DETAIL_NO_DRAFT = (
    "次版の下書きがありません。「現在の版から次版の下書きを作る」を先に実行してください。"
)
_DETAIL_NODE_NOT_IN_DRAFT = (
    "この項目は次版の下書きにありません。下書きを保存してから記録してください。"
)
_DETAIL_OVERRIDE_REASON_REQUIRED = (
    "ほかの教員が採用した候補を戻すときは、理由を書いてください（記録に残ります）。"
)
_DETAIL_LABEL_UNRESOLVED = "取り込む項目の名前が決まりません。名前を指定してください。"


# ---------------------------------------------------------------------------
# セッション・共通ヘルパー
# ---------------------------------------------------------------------------


def _session():
    """gap 信号・判断・骨格の読み書き用セッション。取得不能は 503。"""
    try:
        from core.postgres import get_session

        return get_session()
    except Exception as exc:  # noqa: BLE001
        logger.error("atlas gap DB session unavailable", exc_info=True)
        raise HTTPException(status_code=503, detail="データベースに接続できません") from exc


def _frozen_or_404(session, cartridge_id: str):
    """現行の凍結骨格（DB 優先・同梱ファイルへフォールバック）。無ければ 404。

    候補は「現行の地図では言い表せなかった主題」なので、比較対象の凍結版が無い分野では
    そもそもレビューが成立しない（既存の atlas 系読み取り API と同じ 404 の流儀）。
    """
    skeleton = atlas_store.load_learner_skeleton(cartridge_id, session)
    if skeleton is None:
        raise HTTPException(status_code=404, detail=_DETAIL_NO_FROZEN_SKELETON)
    return skeleton


def _draft_or_409(session, cartridge_id: str) -> dict:
    """編集中の次版下書き。無ければ 409（先に from-frozen で下書きを作る導線へ）。"""
    draft_row = atlas_store.load_draft(session, cartridge_id)
    if draft_row is None:
        raise HTTPException(status_code=409, detail=_DETAIL_NO_DRAFT)
    return draft_row


def _draft_node_ids(skeleton: atlas.AtlasSkeleton) -> set[str]:
    return set(skeleton.concept_ids()) | set(skeleton.region_ids())


def _assert_cluster_belongs_to_domain(cluster_key: str, cartridge_id: str) -> None:
    """cluster_key の domain とパスの分野が食い違う操作を弾く（fail-closed）。

    cluster_key は ``gap|{domain_key}|{parent_region_id}|{normalized_label}`` で
    domain を含むため、パスの分野と一致しない鍵での判断・取り込みは受け付けない
    （別分野の共同財行を、その分野を開いていない教員が動かせてしまう経路を作らない）。
    """
    domain_key, _parent, _label = gap_schema.parse_cluster_key(cluster_key)
    if not domain_key or domain_key != str(cartridge_id or "").strip():
        raise HTTPException(status_code=422, detail=_DETAIL_DOMAIN_MISMATCH)


def _record_gap_event(
    cluster_key: str,
    old_status: str,
    new_status: str,
    user_id: Any,
    metadata: dict,
) -> None:
    """判断の遷移を監査記録する（§5.7）。DB 不通でも操作自体は落とさない。"""
    try:
        services.record_review_event(
            AUDIT_ENTITY_CATEGORY_GAP,
            cluster_key,
            old_status,
            new_status,
            str(user_id or "") or None,
            metadata,
        )
    except Exception:  # noqa: BLE001
        logger.warning("category gap review event skipped", exc_info=True)


def _overrides_other_teacher(existing: dict | None, actor_id: str) -> bool:
    """既存の判断が別の教員の採用か（IK-0939）。判断者の記録が無い採用も「別の人」と扱う。"""
    if not existing or str(existing.get("status") or "") != gap_schema.DECISION_STATUS_ACCEPTED:
        return False
    return str(existing.get("decided_by") or "").strip() != str(actor_id or "").strip()


def _visible_document_ids(current_user: dict) -> set[str]:
    """本人が閲覧できる document の集合（失敗は空集合 = fail-closed。services 側が保証）。"""
    return services.list_visible_document_ids(str(current_user.get("id") or ""))


def _candidate_for_cluster(
    session, *, cartridge_id: str, cluster_key: str, frozen: Any
) -> dict | None:
    """取り込み対象の cluster を読み時導出から引く（層・親領域・表記の解決用）。"""
    for candidate in gap_store.derive_candidates(
        session,
        domain_key=cartridge_id,
        frozen_skeleton=frozen,
        current_version=str(getattr(frozen, "version", "") or ""),
        include_dismissed=True,
    ):
        if candidate.get("cluster_key") == cluster_key:
            return candidate
    return None


# ---------------------------------------------------------------------------
# リクエストモデル
# ---------------------------------------------------------------------------

ACTION_ACCEPT = "accept"
ACTION_DISMISS = "dismiss"
ACTION_RESTORE = "restore"

#: UI が送れる操作（v1。``merged`` は store 側にあるが導線を作らない — §7 非スコープ）。
DECIDE_ACTIONS = (ACTION_ACCEPT, ACTION_DISMISS, ACTION_RESTORE)

_ACTION_STATUS = {
    ACTION_ACCEPT: gap_schema.DECISION_STATUS_ACCEPTED,
    ACTION_DISMISS: gap_schema.DECISION_STATUS_DISMISSED,
}


class DecideGapCandidateRequest(BaseModel):
    cluster_key: str = Field(description="候補の cluster_key（版非依存キー）")
    action: str = Field(description="accept | dismiss | restore")
    review_note: str = Field(
        default="", description="判断の理由。見送り（dismiss）では必須（空は 422）"
    )


class IncorporatePreviewRequest(BaseModel):
    cluster_key: str = Field(description="採用済み候補の cluster_key")
    proposed_label: str = Field(
        default="",
        description="取り込む項目の表示名（レビュー画面のインライン編集。空なら候補の表記）",
    )


class MarkIncorporatedRequest(BaseModel):
    cluster_key: str = Field(description="採用済み候補の cluster_key")
    draft_node_id: str = Field(description="次版下書きへ追加した node の id")


# ---------------------------------------------------------------------------
# レビューキュー（読み時導出。§4.3 / §5.4）
# ---------------------------------------------------------------------------


@router.get("/{cartridge_id}/atlas/gap-candidates")
def list_atlas_gap_candidates(
    cartridge_id: str,
    include_dismissed: bool = Query(
        False, description="true で見送り済み・統合済みも含める（復帰導線用）"
    ),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """論文の解析から見つかった候補の一覧（毎回導出。行として蓄積しない）。

    返す形: ``{cartridge_id, candidates, skeleton_version, draft_exists, draft_revision}``。
    ``candidates`` の各要素は ``core/atlas_gaps/store.py::derive_candidates`` の DTO
    （生 confidence なし・件数フィールドなし。支持論文は ``documents`` の列挙）。
    ``draft_exists`` / ``draft_revision`` は「次版の下書きに取り込む」導線の活性判断用。
    """
    session = _session()
    try:
        frozen = _frozen_or_404(session, cartridge_id)
        version = str(getattr(frozen, "version", "") or "")
        derive_report: dict = {}
        candidates = gap_store.derive_candidates(
            session,
            domain_key=cartridge_id,
            frozen_skeleton=frozen,
            current_version=version,
            include_dismissed=include_dismissed,
            report=derive_report,
        )
        # 近傍注記（VA層 §7。読み時導出・保存しない）。ベクトル層が使えなければ
        # ``near_anchor`` キーが付かないだけで、候補一覧は従来と完全に同じ形で返る。
        # 照合の状態（IK-0851: 「照合して該当なし」と「索引が無いので照合していない」を
        # 同じ空にしない）は annotate が ``report`` に書く。書かれなければキーを付けない。
        anchor_report: dict = {}
        try:
            from core.atlas_vectors.annotate import annotate_gap_clusters

            candidates = annotate_gap_clusters(
                session, cartridge_id, version, candidates, report=anchor_report
            )
        except Exception:  # noqa: BLE001 — 注記の失敗でレビューキューを壊さない
            logger.warning(
                "atlas gap candidates: anchor annotation unavailable for %s (non-fatal)",
                cartridge_id, exc_info=True,
            )
            anchor_report = {"state": atlas_review_display.NEAR_ANCHOR_UNAVAILABLE}
        # 判断の帰属は表示名に写し、判断者の UUID は返さない（IK-0848）。
        candidates = atlas_review_display.project_decisions(
            session, candidates, origin_prefix=atlas_review_display.ORIGIN_PREFIX_GAP
        )
        draft_row = atlas_store.load_draft(session, cartridge_id)
        # 導出から外れた「採用済み・未反映」の判断行（IK-0884。凍結のゲートが読む行を
        # 一覧にも出す — ゲートは緩めない）。失敗しても一覧は返す（fail-soft）。
        orphaned: list[dict] = []
        try:
            orphaned = atlas_review_display.project_orphaned_decisions(
                session,
                gap_store.list_orphaned_decisions(
                    session,
                    domain_key=cartridge_id,
                    derived_cluster_keys=[
                        c.get("cluster_key") for c in candidates
                    ],
                    cluster_states=derive_report.get("cluster_states"),
                    draft_node_ids=(
                        _draft_node_ids(draft_row["skeleton"]) if draft_row else ()
                    ),
                    current_version=version,
                    # 根拠の論文は閲覧できるものだけ（IK-0937・fail-closed）。
                    visible_document_ids=_visible_document_ids(current_user),
                ),
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "atlas gap candidates: orphaned decisions unavailable for %s",
                cartridge_id, exc_info=True,
            )
            orphaned = []
    finally:
        session.close()

    # 判断済みを先頭の帯に（IK-0938）: 採用済み・未反映 → 下書きに入っている → それ以外。
    draft_nodes = _draft_node_ids(draft_row["skeleton"]) if draft_row else set()
    candidates = atlas_review_display.order_decided_first(
        candidates,
        lambda c: bool(
            (c.get("decision") or {}).get("draft_node_id")
            and (c.get("decision") or {}).get("draft_node_id") in draft_nodes
        ),
    )
    payload = {
        "cartridge_id": cartridge_id,
        "candidates": candidates,
        "skeleton_version": version,
        "draft_exists": draft_row is not None,
        "draft_revision": draft_row["revision"] if draft_row else None,
    }
    check = atlas_review_display.near_anchor_check(anchor_report.get("state") or "")
    if check is not None and candidates:
        payload["near_anchor_check"] = check
    dismissed_note = atlas_review_display.dismissed_none_note(
        candidates,
        include_dismissed=include_dismissed,
        suppressed=gap_schema.SUPPRESSED_DECISION_STATUSES,
    )
    if dismissed_note:
        payload["dismissed_note"] = dismissed_note
    # 候補ゼロの理由（IK-0851 第 2 周。信号なし / 反復に届かない / 重複を畳んだ /
    # 現行版で解消 / すべて判断済み を言い分ける。件数は出さない）。
    if not candidates:
        empty_notes = atlas_review_display.empty_reason_notes(derive_report)
        if empty_notes:
            payload["empty_notes"] = empty_notes
    if orphaned:
        payload["orphaned_decisions"] = orphaned
        payload["orphaned_note"] = atlas_review_display.ORPHANED_NOTE
    return payload


def _project_own_decision(decision: dict | None, current_user: dict) -> dict | None:
    """判断の応答を表示用に写す（判断者の UUID を出さない — IK-0848）。"""
    return atlas_review_display.project_decision_for_response(
        decision,
        current_user,
        session_factory=_session,
        origin_prefix=atlas_review_display.ORIGIN_PREFIX_GAP,
    )


# ---------------------------------------------------------------------------
# 教員の判断（採用 / 見送り / 復帰。確定は人間のみ — KN-3 / AB4）
# ---------------------------------------------------------------------------


@router.post("/{cartridge_id}/atlas/gap-candidates/decide")
def decide_atlas_gap_candidate(
    cartridge_id: str,
    body: DecideGapCandidateRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """候補を採用・見送りにする（または見送りを取り消す）。骨格 draft は変わらない。

    - ``accept``: 「カテゴリとして妥当」の判断のみ（次版下書きへの取り込みは別操作）
    - ``dismiss``: 理由必須（空は 422）。同名の候補は以降キューに出ない（版を跨いで永続）
    - ``restore``: 見送りを取り消して未判断に戻す（**行削除ではなく状態遷移** — P4）
    """
    action = str(body.action or "").strip()
    if action not in DECIDE_ACTIONS:
        raise HTTPException(status_code=422, detail=_DETAIL_UNKNOWN_ACTION)
    cluster_key = str(body.cluster_key or "").strip()
    _assert_cluster_belongs_to_domain(cluster_key, cartridge_id)

    decided_by = str(current_user.get("id") or "").strip()
    session = _session()
    try:
        existing = gap_store.get_decision(session, cluster_key)
        old_status = str((existing or {}).get("status") or "")
        overrides_other = _overrides_other_teacher(existing, decided_by)
        restore_note = str(body.review_note or "").strip()
        if action == ACTION_RESTORE and overrides_other and not restore_note:
            # 他の教員の採用を候補へ戻すときは理由を必須にする（IK-0939。凍結の
            # 規則は緩めない — 戻す判断を誰が何の理由でしたかを監査に残す）。
            raise HTTPException(status_code=422, detail=_DETAIL_OVERRIDE_REASON_REQUIRED)
        if action == ACTION_RESTORE:
            decision = gap_store.restore_decision(
                session, cluster_key=cluster_key, decided_by=decided_by
            )
            if decision is None:
                session.rollback()
                raise HTTPException(status_code=404, detail=_DETAIL_DECISION_NOT_FOUND)
            audit_action = gap_schema.AUDIT_ACTION_RESTORE
        else:
            decision = gap_store.upsert_decision(
                session,
                cluster_key=cluster_key,
                status=_ACTION_STATUS[action],
                decided_by=decided_by,
                review_note=str(body.review_note or ""),
            )
            audit_action = gap_schema.audit_action_for_status(decision["status"])
        session.commit()
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    audit_metadata: dict[str, Any] = {"action": audit_action, "cartridge_id": cartridge_id}
    if overrides_other:
        audit_metadata["overrides_other_teacher"] = True
        if restore_note and action == ACTION_RESTORE:
            audit_metadata["review_note"] = restore_note
    _record_gap_event(
        cluster_key,
        old_status,
        decision["status"],
        current_user.get("id"),
        audit_metadata,
    )
    return {"cartridge_id": cartridge_id, "decision": _project_own_decision(decision, current_user)}


# ---------------------------------------------------------------------------
# 次版下書きへの取り込み（読み取り専用プレビュー → 教員の PUT → 刻印。§5.5）
# ---------------------------------------------------------------------------


@router.post("/{cartridge_id}/atlas/gap-candidates/incorporate-preview")
def preview_atlas_gap_incorporation(
    cartridge_id: str,
    body: IncorporatePreviewRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """採用済み候補を次版下書きへ追加する JSON Patch を**提示するだけ**の読み取り操作。

    **DB を変更しない**（下書きは書かない。適用は教員の既存
    ``PUT /api/admin/cartridges/{id}/atlas/skeleton/draft`` が revision 楽観ロック付きで行う）。

    返す形: ``{cartridge_id, cluster_key, patch, node_id, layer, parent_region_id,
    proposed_label, summary, patched_draft, validation, revision}``。
    ``patched_draft`` は適用後の ``atlas_skeleton`` dict で、そのまま PUT の body.skeleton
    に使える（``revision`` は楽観ロックに使う現在値）。

    未採用は 409 / 下書きなしは 409 / 上限に達している領域・親領域不在は 422（事実文）。
    """
    cluster_key = str(body.cluster_key or "").strip()
    _assert_cluster_belongs_to_domain(cluster_key, cartridge_id)

    session = _session()
    try:
        decision = gap_store.get_decision(session, cluster_key)
        if decision is None:
            raise HTTPException(status_code=409, detail=_DETAIL_NOT_ACCEPTED)
        if decision["status"] != gap_schema.DECISION_STATUS_ACCEPTED:
            raise HTTPException(status_code=409, detail=_DETAIL_NOT_ACCEPTED)

        draft_row = _draft_or_409(session, cartridge_id)
        candidate = _candidate_for_cluster(
            session,
            cartridge_id=cartridge_id,
            cluster_key=cluster_key,
            frozen=atlas_store.load_learner_skeleton(cartridge_id, session),
        )
        # 確認画面に写す帰属と根拠（IK-0939）。写しが作れなくても確認は出す（fail-soft）。
        context: dict = {}
        try:
            items = atlas_review_display.pending_freeze_items(
                session,
                gap_items=[
                    {
                        "proposed_label": "—",
                        "decision": decision,
                        "review_note": decision.get("review_note") or "",
                        "documents": (candidate or {}).get("documents")
                        or gap_store.cluster_documents(
                            session, domain_key=cartridge_id, cluster_key=cluster_key
                        ),
                    }
                ],
                visible_document_ids=_visible_document_ids(current_user),
            )
            context = items[0] if items else {}
        except Exception:  # noqa: BLE001
            logger.warning("atlas gap preview: decision context skipped", exc_info=True)
    finally:
        session.close()

    layer, parent_region_id, label = _resolve_incorporation_target(
        cluster_key, candidate, str(body.proposed_label or "")
    )
    if not label:
        raise HTTPException(status_code=422, detail=_DETAIL_LABEL_UNRESOLVED)

    draft_dict = atlas.skeleton_to_dict(draft_row["skeleton"])["atlas_skeleton"]
    try:
        built = gap_patching.build_gap_patch(
            draft_dict,
            layer=layer,
            parent_region_id=parent_region_id,
            proposed_label=label,
        )
    except gap_patching.GapPatchError as exc:
        # SkeletonCapacityError も含む（どちらも「いま作れない patch を提示しない」）。
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    patched, validation = _apply_and_validate(draft_dict, built["patch"])
    return {
        "cartridge_id": cartridge_id,
        "cluster_key": cluster_key,
        "patch": built["patch"],
        "node_id": built["node_id"],
        "layer": built["layer"],
        "parent_region_id": built["parent_region_id"],
        # 確認画面は親領域を名前で言う（IK-0850。英字キーは保存の内部値）。
        "parent_region_label": built.get("parent_region_label", ""),
        "proposed_label": label,
        "summary": built["summary"],
        # 帰属・根拠・採用のメモ・公開の時点（IK-0939）。
        "decision_line": context.get("decision_line", ""),
        "basis_line": context.get("basis_line", ""),
        "note_line": context.get("note_line", ""),
        "timing_line": atlas_review_display.INCORPORATE_TIMING_GAP,
        "patched_draft": patched,
        "validation": validation,
        "revision": draft_row["revision"],
    }


def _resolve_incorporation_target(
    cluster_key: str, candidate: dict | None, override_label: str
) -> tuple[str, str, str]:
    """``(layer, parent_region_id, proposed_label)`` を決定論的に解決する。

    第一の出所は読み時導出の候補（表記は最新の信号のもの）。信号が再解析で
    superseded になった等で候補が引けない場合は cluster_key から復元する
    （親領域が空なら領域候補 — ``record_signals`` が領域候補の親を '' に正規化するため）。
    教員がレビュー画面で名前を編集した場合は ``override_label`` が最優先。
    """
    domain_key, parent_region_id, normalized_label = gap_schema.parse_cluster_key(
        cluster_key
    )
    del domain_key  # 呼び出し前に _assert_cluster_belongs_to_domain で照合済み
    layer = (
        gap_schema.GAP_LAYER_CONCEPT if parent_region_id else gap_schema.GAP_LAYER_REGION
    )
    label = normalized_label
    if candidate:
        layer = str(candidate.get("layer") or layer)
        parent_region_id = str(candidate.get("parent_region_id") or parent_region_id)
        label = str(candidate.get("proposed_label") or label)
    override = str(override_label or "").strip()
    if override:
        label = override
    return layer, parent_region_id, label


def _apply_and_validate(draft_dict: dict, patch: list[dict]) -> tuple[dict | None, dict]:
    """patch を非破壊適用し、既存の骨格バリデータで検証する（assist_propose と同型）。

    適用不能・スキーマ不適合でも提示は止めない（``patched_draft=None`` +
    ``validation.errors`` で正直に返し、最終的な拒否は教員の PUT の 422 に委ねる）。
    """
    from core.atlas_generator import PatchApplyError, apply_json_patch

    errors: list[dict] = []
    warnings: list[dict] = []
    patched: dict | None = None
    try:
        patched = apply_json_patch(draft_dict, patch)
    except PatchApplyError as exc:
        errors.append({"message": f"パッチを適用できませんでした: {exc}"})

    if patched is not None:
        try:
            report = atlas.validate_skeleton(atlas.parse_skeleton(patched))
            errors.extend(atlas.issue_to_dict(e) for e in report.errors)
            warnings.extend(atlas.issue_to_dict(w) for w in report.warnings)
        except atlas.SkeletonParseError as exc:
            patched = None
            errors.append({"message": f"適用後の骨格を読み取れませんでした: {exc}"})
    return patched, {"errors": errors, "warnings": warnings}


@router.post("/{cartridge_id}/atlas/gap-candidates/mark-incorporated")
def mark_atlas_gap_incorporated(
    cartridge_id: str,
    body: MarkIncorporatedRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """次版下書きへ**保存済み**の node を、採用済み候補の取り込み先として刻印する。

    教員の ``PUT draft`` が成功した**後**に呼ばれる契約。``draft_node_id`` が現在の
    下書きに実在しない場合は 409（PUT より前に呼ばれた誤順序を弾く）。
    ``applied_version``（実際に反映された版）は凍結時に別途刻印される（採用と反映の分離）。
    """
    cluster_key = str(body.cluster_key or "").strip()
    _assert_cluster_belongs_to_domain(cluster_key, cartridge_id)
    draft_node_id = str(body.draft_node_id or "").strip()

    session = _session()
    try:
        decision = gap_store.get_decision(session, cluster_key)
        if decision is None:
            raise HTTPException(status_code=404, detail=_DETAIL_DECISION_NOT_FOUND)
        if decision["status"] != gap_schema.DECISION_STATUS_ACCEPTED:
            raise HTTPException(status_code=409, detail=_DETAIL_NOT_ACCEPTED)

        draft_row = _draft_or_409(session, cartridge_id)
        if draft_node_id not in _draft_node_ids(draft_row["skeleton"]):
            raise HTTPException(status_code=409, detail=_DETAIL_NODE_NOT_IN_DRAFT)

        updated = gap_store.mark_incorporated(
            session, cluster_key=cluster_key, draft_node_id=draft_node_id
        )
        if updated is None:
            session.rollback()
            raise HTTPException(status_code=404, detail=_DETAIL_DECISION_NOT_FOUND)
        session.commit()
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except HTTPException:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    _record_gap_event(
        cluster_key,
        gap_schema.DECISION_STATUS_ACCEPTED,
        gap_schema.DECISION_STATUS_ACCEPTED,
        current_user.get("id"),
        {
            "action": gap_schema.AUDIT_ACTION_INCORPORATE,
            "cartridge_id": cartridge_id,
            "draft_node_id": draft_node_id,
        },
    )
    node_label = ""
    for region in getattr(draft_row["skeleton"], "regions", ()) or ():
        if region.id == draft_node_id:
            node_label = region.label or ""
        for concept in getattr(region, "concepts", ()) or ():
            if concept.id == draft_node_id:
                node_label = concept.label or ""
    payload = {
        "cartridge_id": cartridge_id,
        "decision": _project_own_decision(updated, current_user),
    }
    if node_label:
        # 何を足したかを名指す結果文（IK-0938）。
        payload["result_line"] = atlas_review_display.INCORPORATED_GAP_RESULT.format(
            label=node_label
        )
    return payload
