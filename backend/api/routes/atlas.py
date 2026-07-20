"""分野の地図 — 骨格の生成・レビュー・凍結 API (Issue A-2 / A-3) と修正報告 (Issue D)。

- 管理 (教員以上): draft の生成 (明示操作のみ)・修正・承認・凍結、修正報告のレビュー処理
- 学習者向け: 凍結済み骨格のみ返す。draft はいかなる場合も返さない (Issue A-2)
- 修正報告 (Issue D): 帰属つき(匿名不可)で受け付け、既存の C層教員レビュー導線へ流す。
  採用は骨格次版の changelog[].credits に報告者の帰属を残す
- 状態変更は theory_review_events に entity_type='atlas_skeleton' / 'atlas_report' で
  監査記録する
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import services
from core import atlas
from core import atlas_lifecycle
from core import atlas_reports
from core import atlas_store
from core import cartridges as cartridges_module
from core.course_data import course_atlas_binding_pending, course_cartridge_id, course_topics
from core.schema import (
    AUDIT_ENTITY_ATLAS_ASSIST,
    AUDIT_ENTITY_ATLAS_BINDING,
    AUDIT_ENTITY_ATLAS_REPORT,
    AUDIT_ENTITY_ATLAS_SKELETON,
)
from core.status import cross_layer_notify
from dependencies import _get_current_user, _require_teacher

logger = logging.getLogger(__name__)

# /api/admin/cartridges/... 配下 (routes/admin.py がインクルード)
router = APIRouter(prefix="/cartridges", tags=["Admin"])

# /api/admin/atlas/... 配下 (routes/admin.py がインクルード。ドメイン一覧など
# cartridge_id パスに馴染まない管理エンドポイント)
admin_atlas_router = APIRouter(prefix="/atlas", tags=["Admin"])

# /api/admin/courses/{course_id}/atlas-binding (routes/admin.py がインクルード。
# コース ⇄ 地図バインディングの提案・承認保存)
binding_router = APIRouter(prefix="/courses", tags=["Admin"])

# 学習者向け (main.py がインクルード)
learning_router = APIRouter(prefix="/api/learning/atlas", tags=["Learning"])

# 修正報告 (仕様書 §11: POST /api/atlas/report。main.py がインクルード)
report_router = APIRouter(prefix="/api/atlas", tags=["Atlas"])


# ---------------------------------------------------------------------------
# 監査記録 (theory_components.py と同じ theory_review_events を使う)
# ---------------------------------------------------------------------------


def _record_review_event(
    entity_id: str,
    old_status: str,
    new_status: str,
    user_id: str | None,
    metadata: dict | None = None,
    entity_type: str = AUDIT_ENTITY_ATLAS_SKELETON,
) -> None:
    """凍結・生成・報告などの状態遷移を監査記録する。DB 不通時は警告のみ (非致命)。

    実体は services.record_review_event（提案7の一本化）。DB セッション自体が
    確立できない場合でも致命的にしない atlas 固有のフォールバックはここで維持する。
    """
    try:
        services.record_review_event(entity_type, entity_id, old_status, new_status, user_id, metadata)
    except Exception:  # noqa: BLE001
        logger.warning("atlas review event skipped (no DB session)", exc_info=True)


def _reports_session():
    """修正報告用の DB セッション。取得不能は 503 (報告は永続化が本体のため)。"""
    try:
        from core.postgres import get_session

        return get_session()
    except Exception as exc:  # noqa: BLE001
        logger.error("atlas report DB session unavailable", exc_info=True)
        raise HTTPException(status_code=503, detail="データベースに接続できません") from exc


# ---------------------------------------------------------------------------
# 骨格ストアヘルパー (migration 027: DB が正本。ファイルは同梱シードのみ)
# ---------------------------------------------------------------------------


def _skeleton_session():
    """骨格 draft/凍結の DB セッション。取得不能は 503 (永続化が本体のため)。"""
    try:
        from core.postgres import get_session

        return get_session()
    except Exception as exc:  # noqa: BLE001
        logger.error("atlas skeleton DB session unavailable", exc_info=True)
        raise HTTPException(status_code=503, detail="データベースに接続できません") from exc


def _ensure_domain_exists(cartridge_id: str) -> None:
    """domain の存在チェック (404)。カートリッジディレクトリまたは DB 骨格のどちらかで成立。

    migration 027 以降、カートリッジファイルを持たない DB 管理の domain がありうる。
    """
    try:
        cartridges_module.cartridge_directory(cartridge_id)
        return
    except FileNotFoundError:
        pass
    session = _skeleton_session()
    try:
        if atlas_store.load_frozen_skeleton(session, cartridge_id) is not None:
            return
        if atlas_store.load_draft(session, cartridge_id) is not None:
            return
    finally:
        session.close()
    raise HTTPException(status_code=404, detail=f"domain '{cartridge_id}' not found")


def _skeleton_payload(skeleton: atlas.AtlasSkeleton | None) -> dict[str, Any] | None:
    if skeleton is None:
        return None
    report = atlas.validate_skeleton(skeleton)
    return {
        "skeleton": atlas.skeleton_to_dict(skeleton)["atlas_skeleton"],
        "validation": {
            # errors/warnings は後方互換のためメッセージ文字列のまま返す。
            # *_issues は骨格エディタのインラインハイライト用に region_id/concept_id
            # /edge を機械可読で載せる (P2)。
            "errors": list(report.errors),
            "warnings": list(report.warnings),
            "error_issues": [atlas.issue_to_dict(e) for e in report.errors],
            "warning_issues": [atlas.issue_to_dict(w) for w in report.warnings],
        },
    }


# ---------------------------------------------------------------------------
# リクエストモデル
# ---------------------------------------------------------------------------


class GenerateSkeletonRequest(BaseModel):
    force: bool = Field(default=False, description="既存 draft がある場合に上書き再生成するか")
    model: str | None = Field(default=None, description="使用モデルの上書き (省略時は設定値)")
    domain: dict | None = Field(
        default=None,
        description=(
            "新分野メタデータ {name, description, target_domain[], concept_vocabulary}。"
            "カートリッジファイルを持たない domain の骨格生成に使う (migration 027)"
        ),
    )


class SaveDraftRequest(BaseModel):
    skeleton: dict = Field(description="skeleton.yaml 相当の dict (atlas_skeleton キー可)")
    revision: int | None = Field(
        default=None,
        description="楽観ロック。現在の draft revision。省略は新規作成のみ許可 (migration 027)",
    )


class FreezeSkeletonRequest(BaseModel):
    version: str = Field(description="凍結版の版数 (例: 2026.1)")
    note: str = Field(default="", description="changelog に残すメモ")
    credits: list[str] = Field(default_factory=list, description="修正報告者などの帰属")


class AssistInterpretRequest(BaseModel):
    """§4.2 意図解釈ステップ。まだ骨格を変更しない。"""

    message: str = Field(description="教員の発言")
    revision: int | None = Field(
        default=None, description="会話の基準にした draft revision (楽観ロック・古ければ 409)"
    )
    history: list[dict] = Field(
        default_factory=list,
        description="[{role: teacher|agent, content, resolved_target?}] のチャット履歴",
    )
    selection_hint: dict | None = Field(
        default=None, description="直前にクリックしたノード {region_id?, concept_id?}"
    )


class AssistProposeRequest(BaseModel):
    """§4.3 差分提案ステップ。確定済みの解釈から編集案を生成する (再解釈しない)。"""

    confirmed_interpretation: dict = Field(description="§4.2 で教員が確定した interpretation")
    revision: int | None = Field(
        default=None, description="基準にした draft revision (古ければ 409)"
    )


class SaveAtlasBindingRequest(BaseModel):
    cartridge_id: str = Field(description="バインドする domain_key (空文字で解除)")
    topic_bindings: list[dict] = Field(
        default_factory=list,
        description="topic → 骨格概念の明示 binding [{topic_id, atlas_node_id}]。"
        "atlas_node_id が空の要素は該当 topic の binding を解除する",
    )


# ---------------------------------------------------------------------------
# 管理エンドポイント (教員以上)
# ---------------------------------------------------------------------------


@router.get("/{cartridge_id}/atlas/skeleton")
def get_atlas_skeleton_state(
    cartridge_id: str, current_user: dict = Depends(_require_teacher)
) -> dict:
    """骨格のレビュー状態 (draft / 凍結済み) を返す (migration 027: DB が正本)。"""
    session = _skeleton_session()
    try:
        draft_row = atlas_store.load_draft(session, cartridge_id)
        frozen = atlas_store.load_learner_skeleton(cartridge_id, session)
    finally:
        session.close()
    draft_payload = _skeleton_payload(draft_row["skeleton"]) if draft_row else None
    if draft_payload is not None:
        draft_payload["revision"] = draft_row["revision"]
    return {
        "cartridge_id": cartridge_id,
        "draft": draft_payload,
        "frozen": _skeleton_payload(frozen),
    }


@admin_atlas_router.get("/domains")
def list_atlas_domains(current_user: dict = Depends(_require_teacher)) -> dict:
    """骨格を持つ (または draft 中の) domain の一覧 (migration 027)。"""
    session = _skeleton_session()
    try:
        domains = atlas_store.list_domains(session)
    finally:
        session.close()
    return {"domains": domains}


@router.post("/{cartridge_id}/atlas/skeleton/generate")
def generate_atlas_skeleton(
    cartridge_id: str,
    body: GenerateSkeletonRequest | None = None,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """骨格 draft を LLM バッチ生成する。一度だけ実行し、再実行は force 指定の明示操作。

    migration 027: draft は DB 保存。カートリッジファイルの無い新分野は
    body.domain のメタデータで生成する。
    """
    body = body or GenerateSkeletonRequest()
    session = _skeleton_session()
    try:
        if atlas_store.domain_lifecycle(session, cartridge_id) == "retired":
            raise HTTPException(
                status_code=409,
                detail="この分野は廃止済みです。復帰してから編集してください",
            )
        existing = atlas_store.load_draft(session, cartridge_id)
        domain_meta = body.domain or atlas_store.load_domain_meta(session, cartridge_id)
    finally:
        session.close()
    if existing is not None and not body.force:
        raise HTTPException(
            status_code=409,
            detail="draft が既に存在します。再生成する場合は force を指定してください",
        )

    from core.atlas_generator import generate_skeleton_draft

    try:
        skeleton = generate_skeleton_draft(
            cartridge_id, model=body.model, domain_meta=domain_meta
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"{exc} — カートリッジファイルの無い新分野は body.domain に"
            " {name, description} を指定してください",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    session = _skeleton_session()
    try:
        revision = atlas_store.save_draft(
            session,
            cartridge_id,
            skeleton,
            expected_revision=existing["revision"] if existing else None,
            user_id=str(current_user.get("id") or "") or None,
            generated_by=skeleton.generated_by,
        )
        if body.domain:
            atlas_store.save_domain_meta(
                session, cartridge_id, body.domain,
                user_id=str(current_user.get("id") or "") or None,
            )
        session.commit()
    except atlas_store.DraftRevisionConflict as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "current_revision": exc.current_revision},
        ) from exc
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    _record_review_event(
        cartridge_id,
        "",
        atlas.STATUS_DRAFT,
        current_user.get("id"),
        {"action": "generate", "generated_by": skeleton.generated_by, "force": body.force},
    )
    payload = _skeleton_payload(skeleton)
    if payload is not None:
        payload["revision"] = revision
    return {"cartridge_id": cartridge_id, "draft": payload}


@router.put("/{cartridge_id}/atlas/skeleton/draft")
def save_atlas_skeleton_draft(
    cartridge_id: str,
    body: SaveDraftRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """教員レビューによる draft の修正を保存する (領域・概念・配置・エッジ・seed_status)。

    migration 027: DB 保存 + 楽観ロック。body.revision が現在の draft と
    ズレていれば 409 (最新を読み込み直して再編集)。
    """
    try:
        edited = atlas.parse_skeleton(body.skeleton)
    except atlas.SkeletonParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    session = _skeleton_session()
    try:
        if atlas_store.domain_lifecycle(session, cartridge_id) == "retired":
            raise HTTPException(
                status_code=409,
                detail="この分野は廃止済みです。復帰してから編集してください",
            )
        existing = atlas_store.load_draft(session, cartridge_id)
        existing_skeleton = existing["skeleton"] if existing else None

        # 来歴は編集で失わせない。status は凍結エンドポイント以外では draft のまま
        generated_by = edited.generated_by or (
            existing_skeleton.generated_by if existing_skeleton else ""
        )
        draft = atlas.AtlasSkeleton(
            cartridge=cartridge_id,
            status=atlas.STATUS_DRAFT,
            version="",
            generated_by=generated_by,
            reviewed_by=(),
            changelog=existing_skeleton.changelog if existing_skeleton else (),
            regions=edited.regions,
            edges=edited.edges,
            concept_bindings=edited.concept_bindings,
            id_migrations=edited.id_migrations,
        )
        report = atlas.validate_skeleton(draft)
        if not report.ok:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "骨格がスキーマに適合しません",
                    "errors": list(report.errors),
                    "error_issues": [atlas.issue_to_dict(e) for e in report.errors],
                },
            )
        revision = atlas_store.save_draft(
            session,
            cartridge_id,
            draft,
            expected_revision=body.revision,
            user_id=str(current_user.get("id") or "") or None,
        )
        session.commit()
    except atlas_store.DraftRevisionConflict as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "current_revision": exc.current_revision},
        ) from exc
    except HTTPException:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    _record_review_event(
        cartridge_id,
        atlas.STATUS_DRAFT,
        atlas.STATUS_DRAFT,
        current_user.get("id"),
        {"action": "review_edit", "revision": revision},
    )
    payload = _skeleton_payload(draft)
    if payload is not None:
        payload["revision"] = revision
    return {"cartridge_id": cartridge_id, "draft": payload}


@router.post("/{cartridge_id}/atlas/skeleton/freeze")
def freeze_atlas_skeleton(
    cartridge_id: str,
    body: FreezeSkeletonRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """承認された draft を凍結して版を付与する (migration 027: DB へ保存)。

    - 承認で reviewed_by に帰属を記録する (受け入れ条件2)
    - 凍結後は不変。修正は次版で行う
    - 採用済みの修正報告 (Issue D) の報告者を changelog[].credits に自動で合流し、
      当該報告に applied_version を刻印してクローズする。pending の報告は
      新版へ引き継ぎ、id_migrations に従って対象 node_id を付け替える
    - 凍結処理の前に凍結前の現行版 (DB) との影響プレビュー (§3.2/§4.4) を計算し、
      レスポンスに含める。凍結成功後は関係教員へ通知する (§3.4, best-effort)
    """
    session = _skeleton_session()
    try:
        if atlas_store.domain_lifecycle(session, cartridge_id) == "retired":
            raise HTTPException(
                status_code=409,
                detail="この分野は廃止済みです。復帰してから編集してください",
            )
        draft_row = atlas_store.load_draft(session, cartridge_id)
        if draft_row is None:
            raise HTTPException(status_code=404, detail="凍結対象の draft がありません")
        current_frozen_for_impact = atlas_store.load_frozen_skeleton(session, cartridge_id)
        freeze_impact = atlas_lifecycle.compute_freeze_impact(
            session, cartridge_id, draft_row["skeleton"], current_frozen_for_impact
        )
    finally:
        session.close()
    draft = draft_row["skeleton"]

    # 採用済み報告の帰属を credits に合流する (受け入れ条件3)。
    # DB 不通時は明示 credits のみで凍結を続行する (報告の刻印は次版凍結時に再試行される)。
    report_session = None
    accepted_reports: list[dict] = []
    try:
        from core.postgres import get_session

        report_session = get_session()
        accepted_reports = atlas_reports.accepted_unapplied_reports(
            report_session, cartridge_id
        )
    except Exception:  # noqa: BLE001
        logger.warning("atlas report credits skipped (no DB session)", exc_info=True)

    credits = list(body.credits)
    for name in atlas_reports.credits_from_reports(accepted_reports):
        if name not in credits:
            credits.append(name)

    try:
        frozen = atlas.freeze_skeleton(
            draft,
            version=body.version,
            reviewed_by=[str(current_user.get("id") or "").strip()],
            note=body.note,
            credits=credits,
        )
    except atlas.SkeletonFreezeError as exc:
        if report_session is not None:
            report_session.close()
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # DB へ凍結版を追加し draft を消す (migration 027: DB が正本。ファイルは書かない)
    session = _skeleton_session()
    try:
        atlas_store.insert_frozen(
            session,
            cartridge_id,
            frozen,
            user_id=str(current_user.get("id") or "") or None,
        )
        atlas_store.delete_draft(session, cartridge_id)
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        if report_session is not None:
            report_session.close()
        # (domain_key, version) の一意制約 = 同じ版の二重凍結
        raise HTTPException(
            status_code=409, detail=f"この版は既に凍結済みの可能性があります: {exc}"
        ) from exc
    finally:
        session.close()
    cartridges_module.clear_cache()
    # 新版の overlay cache はコールドスタートになるため非同期リフレッシュを予約する
    try:
        from core import atlas_state

        atlas_state.schedule_overlay_refresh(cartridge_id)
    except Exception:  # noqa: BLE001
        logger.warning("atlas overlay refresh scheduling failed", exc_info=True)

    report_summary = {"applied": 0, "migrated": 0}
    if report_session is not None:
        try:
            report_summary = atlas_reports.apply_freeze_to_reports(
                report_session,
                cartridge_id=cartridge_id,
                version=body.version,
                id_migrations=frozen.id_migrations,
            )
            report_session.commit()
        except Exception:  # noqa: BLE001
            report_session.rollback()
            logger.warning("Failed to apply atlas reports on freeze", exc_info=True)
        finally:
            report_session.close()

    _record_review_event(
        cartridge_id,
        atlas.STATUS_DRAFT,
        atlas.STATUS_FROZEN,
        current_user.get("id"),
        {
            "action": "freeze",
            "version": body.version,
            "note": body.note,
            "report_credits": atlas_reports.credits_from_reports(accepted_reports),
            "reports_applied": report_summary.get("applied", 0),
            "reports_migrated": report_summary.get("migrated", 0),
        },
    )

    # 凍結時の通知 (§3.4)。best-effort — 通知の成否は凍結の成否に影響させない。
    notified = 0
    try:
        from core.postgres import get_session as _get_notify_session

        notify_session = _get_notify_session()
        try:
            notified = atlas_lifecycle.notify_atlas_event(
                notify_session,
                cartridge_id,
                cross_layer_notify.NOTIF_ATLAS_SKELETON_FROZEN,
                {
                    "domain_key": cartridge_id,
                    "version": body.version,
                    "removed_node_count": len(freeze_impact.get("removed_node_ids") or []),
                    "affected_course_count": len(freeze_impact.get("affected_courses") or []),
                },
                actor_id=str(current_user.get("id") or "") or None,
            )
        finally:
            notify_session.close()
    except Exception:  # noqa: BLE001
        logger.warning("atlas skeleton freeze notify failed", exc_info=True)
        notified = 0

    return {
        "cartridge_id": cartridge_id,
        "frozen": _skeleton_payload(frozen),
        "impact": freeze_impact,
        "notified": notified,
    }


# ---------------------------------------------------------------------------
# ドメインライフサイクル (migration 057, §3〜§4.3/§4.4)
#
# - retire / restore は状態遷移のみ (AB3: 削除しない)。retired ドメインは
#   generate / draft 保存 / freeze が 409 (読み取り専用)。binding propose の照合対象
#   からも除外される (照合ロジックは binding propose 側)。学習者向け読み取りは不変。
# - freeze-impact は draft と現行凍結版 (DB) の node_id 差分 + 影響コースを返す読み取り
#   専用 API。凍結エンドポイント自体も同じ計算を凍結前に行いレスポンスへ同梱する。
# ---------------------------------------------------------------------------


@router.get("/{cartridge_id}/atlas/freeze-impact")
def get_atlas_freeze_impact(
    cartridge_id: str, current_user: dict = Depends(_require_teacher)
) -> dict:
    """凍結前の影響プレビュー (§4.4)。draft と現行凍結版 (DB) の node_id 差分を返す。

    draft が無ければ 404。現行凍結版が無ければ (初回凍結) removed は必ず空。
    """
    session = _skeleton_session()
    try:
        draft_row = atlas_store.load_draft(session, cartridge_id)
        if draft_row is None:
            raise HTTPException(status_code=404, detail="draft がありません")
        frozen = atlas_store.load_frozen_skeleton(session, cartridge_id)
        impact = atlas_lifecycle.compute_freeze_impact(
            session, cartridge_id, draft_row["skeleton"], frozen
        )
    finally:
        session.close()
    return impact


class RetireDomainRequest(BaseModel):
    note: str = Field(default="", description="退場理由のメモ (任意)")


@router.post("/{cartridge_id}/atlas/retire")
def retire_atlas_domain(
    cartridge_id: str,
    body: RetireDomainRequest | None = None,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """domain を retired にする (§4.3)。

    削除ではなく状態遷移のみ (AB3)。以後 binding propose の照合対象・候補から除外され、
    generate / draft 保存 / freeze は 409 (読み取り専用) になる。既存の凍結版履歴・
    バインド済みコースの学習者向け表示は不変。関係教員 (バインド中コース所有者 ∪
    骨格編集履歴のある教員) へ通知する (§3.4, best-effort)。
    """
    body = body or RetireDomainRequest()
    _ensure_domain_exists(cartridge_id)

    session = _skeleton_session()
    try:
        if atlas_store.domain_lifecycle(session, cartridge_id) == "retired":
            raise HTTPException(status_code=409, detail="この分野は既に廃止済みです")
        atlas_store.retire_domain(
            session,
            cartridge_id,
            user_id=str(current_user.get("id") or "") or None,
            note=body.note,
        )
        session.commit()
    except HTTPException:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    _record_review_event(
        cartridge_id,
        "active",
        "retired",
        current_user.get("id"),
        {"action": "retire", "note": body.note},
    )

    notified = 0
    try:
        from core.postgres import get_session as _get_notify_session

        notify_session = _get_notify_session()
        try:
            notified = atlas_lifecycle.notify_atlas_event(
                notify_session,
                cartridge_id,
                cross_layer_notify.NOTIF_ATLAS_DOMAIN_RETIRED,
                {"domain_key": cartridge_id, "note": body.note},
                actor_id=str(current_user.get("id") or "") or None,
            )
        finally:
            notify_session.close()
    except Exception:  # noqa: BLE001
        logger.warning("atlas domain retire notify failed", exc_info=True)
        notified = 0

    return {"cartridge_id": cartridge_id, "lifecycle": "retired", "notified": notified}


@router.post("/{cartridge_id}/atlas/restore")
def restore_atlas_domain(
    cartridge_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """domain を active に戻す (§4.3)。監査のみ・通知はしない (pull 型の復帰操作)。"""
    _ensure_domain_exists(cartridge_id)

    session = _skeleton_session()
    try:
        if atlas_store.domain_lifecycle(session, cartridge_id) != "retired":
            raise HTTPException(status_code=409, detail="この分野は廃止されていません")
        atlas_store.restore_domain(
            session, cartridge_id, user_id=str(current_user.get("id") or "") or None
        )
        session.commit()
    except HTTPException:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    _record_review_event(
        cartridge_id,
        "retired",
        "active",
        current_user.get("id"),
        {"action": "restore"},
    )
    return {"cartridge_id": cartridge_id, "lifecycle": "active"}


# ---------------------------------------------------------------------------
# AIアシスト編集 (skeleton editor upgrade P3/P4) — interpret / propose
#
# - どちらも draft を書き換えない。interpret は解釈のみ、propose は JSON Patch 案のみ返す。
# - コスト上限は theory_review_events の当日 assist 呼び出し数で判定する
#   (専用カウンタテーブルを持たない — D層 KPI と同じ思想)。呼び出し自体も監査記録する。
# - revision が現行 draft とズレていれば 409 (会話が古い draft に基づくため再読込を促す。§4.1)。
# ---------------------------------------------------------------------------


def _assist_calls_today(session, user_id: str) -> int:
    from sqlalchemy import text as sa_text

    row = session.execute(
        sa_text(
            """
            SELECT COUNT(*) FROM theory_review_events
             WHERE entity_type = 'atlas_assist'
               AND changed_by = CAST(:uid AS uuid)
               AND created_at >= date_trunc('day', now())
            """
        ),
        {"uid": user_id or None},
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _load_assist_draft(cartridge_id: str, revision: int | None) -> tuple[dict, int]:
    """assist 用に現行 draft を取得し (atlas_skeleton dict, revision) を返す。

    draft 無しは 404、revision がズレていれば 409 (会話が古い draft に基づく)。
    """
    session = _skeleton_session()
    try:
        draft_row = atlas_store.load_draft(session, cartridge_id)
    finally:
        session.close()
    if draft_row is None:
        raise HTTPException(status_code=404, detail="編集対象の draft がありません")
    current_revision = draft_row["revision"]
    if revision is not None and revision != current_revision:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "draft が更新されています。最新を読み込み直してから続けてください",
                "current_revision": current_revision,
            },
        )
    skeleton_dict = atlas.skeleton_to_dict(draft_row["skeleton"])["atlas_skeleton"]
    return skeleton_dict, current_revision


def _guard_assist_cost(user_id: str) -> None:
    from core.config import get_settings

    limit = get_settings().atlas_assist_max_calls_per_day
    session = _skeleton_session()
    try:
        used = _assist_calls_today(session, user_id)
    except Exception:  # noqa: BLE001
        logger.warning("atlas assist cost check failed", exc_info=True)
        return
    finally:
        session.close()
    if used >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"本日の AI アシスト編集の上限 ({limit} 回) に達しました。明日以降に再開してください",
        )


@router.post("/{cartridge_id}/atlas/skeleton/assist/interpret")
def assist_interpret(
    cartridge_id: str,
    body: AssistInterpretRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """§4.2: 教員の発言を対象・要望・継続性に解釈する (まだ編集しない)。"""
    if not (body.message or "").strip():
        raise HTTPException(status_code=422, detail="発言が空です")
    user_id = str(current_user.get("id") or "").strip()
    _guard_assist_cost(user_id)
    skeleton_dict, revision = _load_assist_draft(cartridge_id, body.revision)

    from core.atlas_generator import interpret_skeleton_instruction

    try:
        interpretation = interpret_skeleton_instruction(
            skeleton_dict,
            body.message,
            history=body.history,
            selection_hint=body.selection_hint,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("atlas assist interpret failed", exc_info=True)
        raise HTTPException(status_code=502, detail=f"意図解釈に失敗しました: {exc}") from exc

    _record_review_event(
        cartridge_id, atlas.STATUS_DRAFT, atlas.STATUS_DRAFT, current_user.get("id"),
        {"action": "assist_interpret", "revision": revision},
        entity_type=AUDIT_ENTITY_ATLAS_ASSIST,
    )
    return {
        "cartridge_id": cartridge_id,
        "revision": revision,
        "interpretation": interpretation.model_dump(),
    }


@router.post("/{cartridge_id}/atlas/skeleton/assist/propose")
def assist_propose(
    cartridge_id: str,
    body: AssistProposeRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """§4.3: 確定済み解釈から編集案 (JSON Patch) を生成して返す (draft は書き換えない)。"""
    if not body.confirmed_interpretation:
        raise HTTPException(status_code=422, detail="confirmed_interpretation が空です")
    user_id = str(current_user.get("id") or "").strip()
    _guard_assist_cost(user_id)
    skeleton_dict, revision = _load_assist_draft(cartridge_id, body.revision)

    from core.atlas_generator import (
        PatchApplyError,
        apply_json_patch,
        propose_skeleton_edit,
    )

    try:
        proposal = propose_skeleton_edit(skeleton_dict, body.confirmed_interpretation)
    except Exception as exc:  # noqa: BLE001
        logger.error("atlas assist propose failed", exc_info=True)
        raise HTTPException(status_code=502, detail=f"編集案の生成に失敗しました: {exc}") from exc

    patch = [op.model_dump() for op in proposal.patch]
    result_skeleton: dict | None = None
    val_errors: list[dict] = []
    val_warnings: list[dict] = []
    try:
        result_skeleton = apply_json_patch(skeleton_dict, patch)
    except PatchApplyError as exc:
        # patch が適用不能: 提案は返すが適用ボタンは出さない (result_skeleton=None)
        val_errors.append({"message": f"提案パッチを適用できませんでした: {exc}"})

    if result_skeleton is not None:
        try:
            parsed = atlas.parse_skeleton(result_skeleton)
            report = atlas.validate_skeleton(parsed)
            val_errors.extend(atlas.issue_to_dict(e) for e in report.errors)
            val_warnings.extend(atlas.issue_to_dict(w) for w in report.warnings)
        except atlas.SkeletonParseError as exc:
            result_skeleton = None
            val_errors.append({"message": f"提案適用後の骨格が壊れています: {exc}"})

    _record_review_event(
        cartridge_id, atlas.STATUS_DRAFT, atlas.STATUS_DRAFT, current_user.get("id"),
        {"action": "assist_propose", "revision": revision, "ops": len(patch)},
        entity_type=AUDIT_ENTITY_ATLAS_ASSIST,
    )
    return {
        "cartridge_id": cartridge_id,
        "revision": revision,
        "proposal": {"summary": proposal.summary, "patch": patch},
        # 教員が「適用」で PUT する前提のプレビュー用。検証エラーがあっても提示はする
        # (最終的に PUT の 422 で二重チェックされる。§4.3)
        "result_skeleton": result_skeleton,
        "validation": {"errors": val_errors, "warnings": val_warnings},
    }


# ---------------------------------------------------------------------------
# コース ⇄ 地図バインディング (S2: 提案・教員承認保存)
#
# - 提案は決定論的 (match_topic_to_concept のラベル/明示一致のみ。LLM を使わない)
# - 保存で learning_courses.data.cartridge_id + topics[].atlas_node_id を更新する。
#   明示 cartridge_id は atlas_view の妥当性ゲートを免除される (authoring-time の意思)
# - 対象コースはオーナー教員または SYSTEM_ADMIN のみ操作できる
# ---------------------------------------------------------------------------


def _load_course_for_teacher(session, course_id: str, current_user: dict) -> dict:
    from sqlalchemy import text as sa_text

    row = session.execute(
        sa_text(
            "SELECT data, user_id FROM learning_courses WHERE id = :cid LIMIT 1"
        ),
        {"cid": course_id},
    ).fetchone()
    if not row or row[0] is None:
        raise HTTPException(status_code=404, detail="course not found")
    data = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    owner_id = str(row[1] or "")
    role = str(current_user.get("role") or "")
    if owner_id != str(current_user.get("id") or "") and role != "SYSTEM_ADMIN":
        raise HTTPException(status_code=403, detail="course owner or admin required")
    return data


def _skeleton_node_index(skeleton: atlas.AtlasSkeleton) -> dict[str, str]:
    """骨格の binding 先候補 (概念 + 領域) → 表示ラベルの索引。"""
    index: dict[str, str] = {}
    for region in skeleton.regions:
        index[region.id] = region.label
        for concept in region.concepts:
            index[concept.id] = concept.label
    return index


@binding_router.post("/{course_id}/atlas-binding/propose")
def propose_course_atlas_binding(
    course_id: str, current_user: dict = Depends(_require_teacher)
) -> dict:
    """コースの地図配置を決定論的に提案する (教員が確認して保存するまで確定しない)。

    全 domain (retired を除く) の凍結骨格に対し topic → 概念のカバレッジを算出して返す。
    """
    session = _skeleton_session()
    try:
        course_data = _load_course_for_teacher(session, course_id, current_user)
        topics = [t for t in course_topics(course_data) if isinstance(t, dict)]
        retired_keys = atlas_store.retired_domain_keys(session)
        domains_checked = 0
        retired_skipped = 0
        proposals: list[dict] = []
        for domain in atlas_store.list_domains(session):
            domain_key = domain["domain_key"]
            skeleton = atlas_store.load_learner_skeleton(domain_key, session)
            if skeleton is None:
                continue
            if domain_key in retired_keys:
                # retired ドメインは照合対象・候補から除外する (AB3・§4.1)。
                retired_skipped += 1
                continue
            domains_checked += 1
            node_index = _skeleton_node_index(skeleton)
            bindings: list[dict] = []
            matched = 0
            for topic in topics:
                node_id = atlas.match_topic_to_concept(topic, skeleton) or ""
                if node_id:
                    matched += 1
                bindings.append(
                    {
                        "topic_id": str(topic.get("id") or ""),
                        "topic_title": str(topic.get("title") or ""),
                        "atlas_node_id": node_id,
                        "node_label": node_index.get(node_id, node_id),
                        "current": str(topic.get("atlas_node_id") or ""),
                    }
                )
            proposals.append(
                {
                    "domain_key": domain_key,
                    "frozen_version": skeleton.version,
                    "matched": matched,
                    "topic_count": len(topics),
                    "bindings": bindings,
                    "concepts": [
                        {"id": c.id, "label": c.label, "region_label": r.label}
                        for r in skeleton.regions
                        for c in r.concepts
                    ],
                }
            )
    finally:
        session.close()

    proposals.sort(key=lambda p: (-p["matched"], p["domain_key"]))
    recommended = ""
    if proposals and proposals[0]["matched"] > 0:
        recommended = proposals[0]["domain_key"]
    current_key = course_cartridge_id(course_data)
    return {
        "course_id": course_id,
        "current_cartridge_id": current_key,
        # 現行バインド先が retired のとき候補 (proposals) に現れないため、フロントが
        # 「維持される・解除は明示操作」の事実文を出せるよう真偽を明示する。
        "current_retired": bool(current_key and current_key in retired_keys),
        "recommended": recommended,
        "proposals": proposals,
        "domains_checked": domains_checked,
        "retired_skipped": retired_skipped,
        "atlas_binding_pending": course_atlas_binding_pending(course_data),
    }


@binding_router.put("/{course_id}/atlas-binding")
def save_course_atlas_binding(
    course_id: str,
    body: SaveAtlasBindingRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """教員承認済みのバインディングを保存する。

    cartridge_id="" で解除 (地図は導出+妥当性ゲートに戻る)。骨格に存在しない
    atlas_node_id は適用しない (該当 topic の binding は解除)。
    """
    from sqlalchemy import text as sa_text

    new_key = str(body.cartridge_id or "").strip()
    session = _skeleton_session()
    try:
        course_data = _load_course_for_teacher(session, course_id, current_user)
        known_nodes: dict[str, str] = {}
        if new_key:
            if atlas_store.domain_lifecycle(session, new_key) == "retired":
                raise HTTPException(
                    status_code=422,
                    detail=f"domain '{new_key}' は廃止済みです。復帰してから指定してください",
                )
            skeleton = atlas_store.load_learner_skeleton(new_key, session)
            if skeleton is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"domain '{new_key}' に凍結済みの骨格がありません",
                )
            known_nodes = _skeleton_node_index(skeleton)

        old_key = str(course_data.get("cartridge_id") or "")
        if new_key:
            course_data["cartridge_id"] = new_key
        else:
            course_data.pop("cartridge_id", None)
        # バインド保存が成功したら pending は自動クリアする (§2.3: 意思決定がなされたため)。
        course_data.pop("atlas_binding_pending", None)

        requested = {
            str(b.get("topic_id") or ""): str(b.get("atlas_node_id") or "")
            for b in body.topic_bindings
            if isinstance(b, dict) and b.get("topic_id")
        }
        applied = 0
        skipped: list[str] = []
        for topic in course_topics(course_data):
            if not isinstance(topic, dict):
                continue
            topic_id = str(topic.get("id") or "")
            if topic_id not in requested:
                continue
            node_id = requested[topic_id]
            if node_id and new_key and node_id in known_nodes:
                topic["atlas_node_id"] = node_id
                applied += 1
            else:
                if node_id:
                    skipped.append(topic_id)
                topic.pop("atlas_node_id", None)

        session.execute(
            sa_text(
                "UPDATE learning_courses SET data = CAST(:data AS jsonb), "
                "updated_at = now() WHERE id = :cid"
            ),
            {"cid": course_id, "data": json.dumps(course_data, ensure_ascii=False)},
        )
        session.commit()
    except HTTPException:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    _record_review_event(
        course_id,
        old_key,
        new_key,
        current_user.get("id"),
        {
            "action": "course_atlas_binding",
            "bindings_applied": applied,
            "bindings_skipped": skipped,
        },
        entity_type=AUDIT_ENTITY_ATLAS_BINDING,
    )
    return {
        "course_id": course_id,
        "cartridge_id": new_key,
        "bindings_applied": applied,
        "bindings_skipped": skipped,
    }


# ---------------------------------------------------------------------------
# 凍結待ち仮予約 (pending binding, §2.3/§4.2) — コース起点で新分野を作成した際の
# 「意思の記録」。バインド保存の確定とは独立し、バインド保存が成功したら自動クリアする。
# ---------------------------------------------------------------------------

_DOMAIN_KEY_SLUG_RE = re.compile(r"^[a-z0-9_]+$")


class SetAtlasBindingPendingRequest(BaseModel):
    domain_key: str = Field(description="仮予約する domain_key (スラッグ [a-z0-9_]+)")


@binding_router.put("/{course_id}/atlas-binding/pending")
def set_course_atlas_binding_pending(
    course_id: str,
    body: SetAtlasBindingPendingRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """凍結待ちドメインを仮予約する (§4.2)。コースの地図表示には一切影響しない。"""
    from sqlalchemy import text as sa_text

    domain_key = str(body.domain_key or "").strip()
    if not domain_key or not _DOMAIN_KEY_SLUG_RE.match(domain_key):
        raise HTTPException(
            status_code=422,
            detail="domain_key は英小文字・数字・アンダースコアのみ使えます",
        )

    session = _skeleton_session()
    try:
        course_data = _load_course_for_teacher(session, course_id, current_user)
        if atlas_store.domain_lifecycle(session, domain_key) == "retired":
            raise HTTPException(
                status_code=422,
                detail=f"domain '{domain_key}' は廃止済みです。復帰してから指定してください",
            )
        old_pending = course_atlas_binding_pending(course_data)
        course_data["atlas_binding_pending"] = domain_key
        session.execute(
            sa_text(
                "UPDATE learning_courses SET data = CAST(:data AS jsonb), "
                "updated_at = now() WHERE id = :cid"
            ),
            {"cid": course_id, "data": json.dumps(course_data, ensure_ascii=False)},
        )
        session.commit()
    except HTTPException:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    _record_review_event(
        course_id,
        old_pending,
        domain_key,
        current_user.get("id"),
        {"action": "pending_set", "domain_key": domain_key},
        entity_type=AUDIT_ENTITY_ATLAS_BINDING,
    )
    return {"course_id": course_id, "atlas_binding_pending": domain_key}


@binding_router.delete("/{course_id}/atlas-binding/pending")
def clear_course_atlas_binding_pending(
    course_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """凍結待ち仮予約を取り消す (§4.2)。予約が無くても 200 (冪等)。"""
    from sqlalchemy import text as sa_text

    session = _skeleton_session()
    try:
        course_data = _load_course_for_teacher(session, course_id, current_user)
        old_pending = course_atlas_binding_pending(course_data)
        course_data.pop("atlas_binding_pending", None)
        session.execute(
            sa_text(
                "UPDATE learning_courses SET data = CAST(:data AS jsonb), "
                "updated_at = now() WHERE id = :cid"
            ),
            {"cid": course_id, "data": json.dumps(course_data, ensure_ascii=False)},
        )
        session.commit()
    except HTTPException:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    _record_review_event(
        course_id,
        old_pending,
        "",
        current_user.get("id"),
        {"action": "pending_clear"},
        entity_type=AUDIT_ENTITY_ATLAS_BINDING,
    )
    return {"course_id": course_id, "atlas_binding_pending": ""}


# ---------------------------------------------------------------------------
# 学習者向けエンドポイント (凍結済みのみ。draft は決して返さない)
# ---------------------------------------------------------------------------


@learning_router.get("/{cartridge_id}/skeleton")
def get_learner_atlas_skeleton(
    cartridge_id: str, current_user: dict = Depends(_get_current_user)
) -> dict:
    """学習者向けの骨格。凍結・レビュー済みの版のみ返す。

    骨格なし・draft のみの domain では 404 (地図機能を出さない)。
    migration 027: DB 凍結版が正本 (同梱ファイルはフォールバック)。
    """
    skeleton = atlas_store.load_learner_skeleton(cartridge_id)
    if skeleton is None:
        raise HTTPException(status_code=404, detail="atlas skeleton not available")
    return atlas.learner_view(skeleton)


# ---------------------------------------------------------------------------
# 見晴らしの導線 (Issue F) — 初回自動表示フラグと内部計測
# ---------------------------------------------------------------------------
#
# - 計測は内部のみ (Stage 2 ゲート判断の材料)。数値をユーザーへ返す API は作らない
# - 初回ログイン自動表示 (導線4) の一度きりフラグは
#   (user_id, cue='first_login', event='opened') 行の存在で表す (端末を跨いで永続)

CUE_KINDS = frozenset(
    {"minimap", "topbar", "topic_complete", "chapter_end", "detour_return", "first_login"}
)
CUE_EVENTS = frozenset({"shown", "opened", "dwell", "learn_reached"})
_CUE_PAYLOAD_MAX_CHARS = 2000


class AtlasCueEventRequest(BaseModel):
    cue: str = Field(description="導線の種別 (minimap / topic_complete / ... / first_login)")
    event: str = Field(description="shown / opened / dwell / learn_reached")
    payload: dict = Field(default_factory=dict, description="補足 (duration_ms など)")


@learning_router.get("/cues/state")
def get_atlas_cue_state(current_user: dict = Depends(_get_current_user)) -> dict:
    """導線の永続状態。first_login_seen が真なら初回自動表示は済んでいる。

    DB 不通時は fail-closed (seen=True) — 確認できないときに自動表示を繰り返さない。
    """
    user_id = str(current_user.get("id") or "").strip()
    if not user_id:
        return {"first_login_seen": True}
    try:
        from sqlalchemy import text as sa_text

        from core.postgres import get_session

        session = get_session()
    except Exception:  # noqa: BLE001
        logger.warning("atlas cue state DB session unavailable", exc_info=True)
        return {"first_login_seen": True}
    try:
        row = session.execute(
            sa_text(
                """
                SELECT 1 FROM atlas_cue_events
                 WHERE user_id = CAST(:uid AS uuid)
                   AND cue = 'first_login' AND event = 'opened'
                 LIMIT 1
                """
            ),
            {"uid": user_id},
        ).fetchone()
        return {"first_login_seen": row is not None}
    except Exception:  # noqa: BLE001
        logger.warning("atlas cue state query failed", exc_info=True)
        return {"first_login_seen": True}
    finally:
        session.close()


@learning_router.post("/cues/events", status_code=201)
def record_atlas_cue_event(
    body: AtlasCueEventRequest, current_user: dict = Depends(_get_current_user)
) -> dict:
    """導線イベントを内部計測として記録する (帰属は JWT から)。"""
    if body.cue not in CUE_KINDS:
        raise HTTPException(status_code=422, detail=f"未知の導線種別: {body.cue}")
    if body.event not in CUE_EVENTS:
        raise HTTPException(status_code=422, detail=f"未知のイベント種別: {body.event}")
    user_id = str(current_user.get("id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=403, detail="帰属(ユーザーID)が確認できません")

    payload_json = json.dumps(body.payload or {}, ensure_ascii=False)
    if len(payload_json) > _CUE_PAYLOAD_MAX_CHARS:
        payload_json = "{}"

    session = _reports_session()
    try:
        from sqlalchemy import text as sa_text

        session.execute(
            sa_text(
                """
                INSERT INTO atlas_cue_events (user_id, cue, event, payload)
                VALUES (CAST(:uid AS uuid), :cue, :event, CAST(:payload AS jsonb))
                """
            ),
            {"uid": user_id, "cue": body.cue, "event": body.event, "payload": payload_json},
        )
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.error("Failed to record atlas cue event", exc_info=True)
        raise HTTPException(status_code=500, detail="導線イベントの記録に失敗しました") from exc
    finally:
        session.close()
    return {"ok": True}


# ---------------------------------------------------------------------------
# 修正報告 (Issue D) — 学習者向け: 送信・自分の報告・結果通知の既読
# ---------------------------------------------------------------------------


class AtlasReportRequest(BaseModel):
    """仕様書 §11: body = { node_id | region_id, level, skeleton_version, text }。

    帰属は JWT (current_user) から取る。匿名の送信経路は存在しない (受け入れ条件2)。
    """

    text: str = Field(description="この配置・状態のどこが実際と違うか")
    node_id: str = Field(default="", description="対象ノード id (region_id と択一)")
    region_id: str = Field(default="", description="対象領域 id (node_id と択一)")
    level: int = Field(default=1, description="報告時のズームレベル (1..3)")
    skeleton_version: str = Field(default="", description="どの版への指摘か (自動添付)")
    cartridge_id: str = Field(default="", description="骨格のカートリッジ id")
    node_label: str = Field(default="", description="報告時点の表示ラベル (レビュー画面用)")


class AtlasReportResolveRequest(BaseModel):
    action: str = Field(description="accept / decline / merge")
    note: str = Field(default="", description="処理メモ。見送り(decline)では理由として必須")
    merge_into: str = Field(default="", description="重複統合(merge)の統合先 report_id")


class AtlasReportIncorporateRequest(BaseModel):
    note: str = Field(default="", description="次版で行った対応のメモ")


@report_router.post("/report", status_code=201)
def create_atlas_report(
    body: AtlasReportRequest, current_user: dict = Depends(_get_current_user)
) -> dict:
    """地図上からの修正報告を帰属つきで記録し、教員レビューキューへ投入する。"""
    errors = atlas_reports.validate_report_input(
        text=body.text,
        node_id=body.node_id,
        region_id=body.region_id,
        level=body.level,
        skeleton_version=body.skeleton_version,
    )
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})

    reporter_id = str(current_user.get("id") or "").strip()
    if not reporter_id:
        # 帰属が取れないトークンでは受け付けない (匿名不可)
        raise HTTPException(status_code=403, detail="帰属(ユーザーID)が確認できません")

    session = _reports_session()
    try:
        report_id = atlas_reports.create_report(
            session,
            cartridge_id=body.cartridge_id,
            skeleton_version=body.skeleton_version,
            node_id=body.node_id,
            region_id=body.region_id,
            level=body.level,
            node_label=body.node_label,
            text=body.text,
            reporter_id=reporter_id,
        )
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.error("Failed to create atlas report", exc_info=True)
        raise HTTPException(status_code=500, detail="修正報告の保存に失敗しました") from exc
    finally:
        session.close()

    _record_review_event(
        report_id,
        "",
        atlas_reports.STATUS_PENDING,
        reporter_id,
        {
            "action": "report",
            "cartridge_id": body.cartridge_id,
            "skeleton_version": body.skeleton_version,
            "node_id": body.node_id,
            "region_id": body.region_id,
            "level": body.level,
        },
        entity_type=AUDIT_ENTITY_ATLAS_REPORT,
    )
    return {"report_id": report_id, "status": atlas_reports.STATUS_PENDING}


@report_router.get("/reports/mine")
def list_my_atlas_reports(
    unacked: bool = False, current_user: dict = Depends(_get_current_user)
) -> dict:
    """自分の報告一覧。unacked=true で「処理済みだが未読の結果」のみ (通知用)。"""
    user_id = str(current_user.get("id") or "").strip()
    session = _reports_session()
    try:
        reports = atlas_reports.fetch_reports_for_user(
            session, user_id, unacked_only=unacked
        )
    finally:
        session.close()
    return {"reports": reports}


@report_router.post("/reports/{report_id}/ack")
def ack_atlas_report(
    report_id: str, current_user: dict = Depends(_get_current_user)
) -> dict:
    """採用/見送りの結果通知を既読にする (報告者本人のみ)。"""
    user_id = str(current_user.get("id") or "").strip()
    session = _reports_session()
    try:
        acked = atlas_reports.ack_report(session, report_id=report_id, user_id=user_id)
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(status_code=500, detail="既読の記録に失敗しました") from exc
    finally:
        session.close()
    if not acked:
        raise HTTPException(status_code=404, detail="未読の処理結果が見つかりません")
    return {"report_id": report_id, "acked": True}


# ---------------------------------------------------------------------------
# 修正報告 (Issue D) — 教員レビュー (既存 C層レビュー導線の一部として表示・処理)
# ---------------------------------------------------------------------------


@router.get("/{cartridge_id}/atlas/reports")
def list_atlas_reports(
    cartridge_id: str,
    status: str | None = None,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """修正報告のレビューキュー。

    - 報告本文 + 対象ノード/領域 + 骨格バージョン + 報告者を返す
    - 現行凍結版との不一致 (旧版への報告) を version_mismatch で識別 (受け入れ条件5)
    - 同一対象への報告蓄積 (未クローズ件数) と改版検討ヒントを返す (issue A の閾値と接続)
    """
    _ensure_domain_exists(cartridge_id)  # 存在チェック (404)
    if status and status not in atlas_reports.REPORT_STATUSES:
        raise HTTPException(status_code=422, detail=f"不明な status です: {status}")
    # migration 027: 現行凍結版は DB 優先 (同梱ファイルはフォールバック)
    frozen = atlas_store.load_learner_skeleton(cartridge_id)
    session = _reports_session()
    try:
        reports = atlas_reports.fetch_reports(
            session, cartridge_id=cartridge_id, status=status
        )
    finally:
        session.close()
    summary = atlas_reports.summarize_queue(
        reports, frozen.version if frozen is not None else ""
    )
    summary["cartridge_id"] = cartridge_id
    return summary


@router.post("/{cartridge_id}/atlas/reports/{report_id}/resolve")
def resolve_atlas_report(
    cartridge_id: str,
    report_id: str,
    body: AtlasReportResolveRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """報告のレビュー処理: 採用 (次版へ反映予定) / 見送り (理由つき) / 重複統合。

    処理結果は報告者本人への通知対象になる (notified_at を未読に戻す)。
    """
    _ensure_domain_exists(cartridge_id)  # 存在チェック (404)
    resolver_id = str(current_user.get("id") or "").strip()
    session = _reports_session()
    try:
        transition = atlas_reports.resolve_report(
            session,
            report_id=report_id,
            action=body.action,
            note=body.note,
            resolver_id=resolver_id,
            merge_into=body.merge_into or None,
        )
        session.commit()
    except LookupError as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.error("Failed to resolve atlas report %s", report_id, exc_info=True)
        raise HTTPException(status_code=500, detail="レビュー処理に失敗しました") from exc
    finally:
        session.close()

    _record_review_event(
        report_id,
        transition["old_status"],
        transition["new_status"],
        resolver_id,
        {
            "action": body.action,
            "note": body.note,
            "merge_into": body.merge_into,
            "cartridge_id": cartridge_id,
        },
        entity_type=AUDIT_ENTITY_ATLAS_REPORT,
    )
    return {"report_id": report_id, **transition}


@router.post("/{cartridge_id}/atlas/reports/{report_id}/incorporate")
def incorporate_atlas_report(
    cartridge_id: str,
    report_id: str,
    body: AtlasReportIncorporateRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """採用済み報告を、編集中の次版で対応済みとして記録する。"""
    _ensure_domain_exists(cartridge_id)
    resolver_id = str(current_user.get("id") or "").strip()
    session = _reports_session()
    try:
        transition = atlas_reports.mark_report_incorporated(
            session, report_id=report_id, resolver_id=resolver_id, note=body.note
        )
        session.commit()
    except LookupError as exc:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.error("Failed to mark atlas report incorporated %s", report_id, exc_info=True)
        raise HTTPException(status_code=500, detail="次版での対応記録に失敗しました") from exc
    finally:
        session.close()

    _record_review_event(
        report_id,
        transition["old_status"],
        transition["new_status"],
        resolver_id,
        {"action": "incorporate", "note": body.note, "cartridge_id": cartridge_id},
        entity_type=AUDIT_ENTITY_ATLAS_REPORT,
    )
    return {"report_id": report_id, **transition}


# ---------------------------------------------------------------------------
# 状態導出バッチ (Issue E-1) — 明示実行
# ---------------------------------------------------------------------------


@router.post("/{cartridge_id}/atlas/overlay/refresh")
def refresh_atlas_overlay(
    cartridge_id: str, current_user: dict = Depends(_require_teacher)
) -> dict:
    """`atlas_overlay_cache` の状態導出バッチを明示実行する (教員以上)。

    更新契機 (論文取り込み完了 / 承認・解釈追加 / 行間確定) は通常
    core.atlas_state のコーパス署名の変化として検知され、GET /api/atlas 時に
    非同期リフレッシュが走る。本エンドポイントは初期構築・検証用の同期実行。
    """
    from core import atlas_state

    # migration 027: DB 凍結版が正本 (同梱ファイルはフォールバック)
    frozen = atlas_store.load_learner_skeleton(cartridge_id)
    if frozen is None or not frozen.is_learner_visible:
        raise HTTPException(status_code=404, detail="凍結済みの骨格がありません")

    session = _reports_session()
    try:
        summary = atlas_state.refresh_overlay_cache(
            session, frozen, cartridge_id=cartridge_id
        )
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.error("Failed to refresh atlas overlay for %s", cartridge_id, exc_info=True)
        raise HTTPException(status_code=500, detail="状態導出バッチに失敗しました") from exc
    finally:
        session.close()
    return summary
