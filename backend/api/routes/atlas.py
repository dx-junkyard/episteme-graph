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
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core import atlas
from core import atlas_reports
from core import cartridges as cartridges_module
from dependencies import _get_current_user, _require_teacher

logger = logging.getLogger(__name__)

# /api/admin/cartridges/... 配下 (routes/admin.py がインクルード)
router = APIRouter(prefix="/cartridges", tags=["Admin"])

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
    entity_type: str = "atlas_skeleton",
) -> None:
    """凍結・生成・報告などの状態遷移を監査記録する。DB 不通時は警告のみ (非致命)。"""
    try:
        from sqlalchemy import text as sa_text

        from core.postgres import get_session

        session = get_session()
    except Exception:  # noqa: BLE001
        logger.warning("atlas review event skipped (no DB session)", exc_info=True)
        return
    try:
        session.execute(
            sa_text(
                """
                INSERT INTO theory_review_events (entity_type, entity_id, old_status, new_status, changed_by, metadata)
                VALUES (:entity_type, :entity_id, :old_status, :new_status, CAST(:changed_by AS uuid), CAST(:metadata AS jsonb))
                """
            ),
            {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "old_status": old_status or "",
                "new_status": new_status or "",
                "changed_by": user_id or None,
                "metadata": json.dumps(metadata or {}, ensure_ascii=False),
            },
        )
        session.commit()
    except Exception:  # noqa: BLE001
        session.rollback()
        logger.warning("Failed to record atlas review event for %s", entity_id, exc_info=True)
    finally:
        session.close()


def _reports_session():
    """修正報告用の DB セッション。取得不能は 503 (報告は永続化が本体のため)。"""
    try:
        from core.postgres import get_session

        return get_session()
    except Exception as exc:  # noqa: BLE001
        logger.error("atlas report DB session unavailable", exc_info=True)
        raise HTTPException(status_code=503, detail="データベースに接続できません") from exc


# ---------------------------------------------------------------------------
# パス解決ヘルパー
# ---------------------------------------------------------------------------


def _cartridge_dir(cartridge_id: str):
    try:
        return cartridges_module.cartridge_directory(cartridge_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"cartridge '{cartridge_id}' not found") from exc


def _draft_path(cartridge_id: str):
    return _cartridge_dir(cartridge_id) / atlas.DRAFT_FILENAME


def _frozen_path(cartridge_id: str):
    return _cartridge_dir(cartridge_id) / atlas.SKELETON_FILENAME


def _load_optional(path) -> atlas.AtlasSkeleton | None:
    if not path.exists():
        return None
    try:
        return atlas.load_skeleton(path)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"骨格の読み込みに失敗しました: {exc}") from exc


def _skeleton_payload(skeleton: atlas.AtlasSkeleton | None) -> dict[str, Any] | None:
    if skeleton is None:
        return None
    report = atlas.validate_skeleton(skeleton)
    return {
        "skeleton": atlas.skeleton_to_dict(skeleton)["atlas_skeleton"],
        "validation": {"errors": list(report.errors), "warnings": list(report.warnings)},
    }


# ---------------------------------------------------------------------------
# リクエストモデル
# ---------------------------------------------------------------------------


class GenerateSkeletonRequest(BaseModel):
    force: bool = Field(default=False, description="既存 draft がある場合に上書き再生成するか")
    model: str | None = Field(default=None, description="使用モデルの上書き (省略時は設定値)")


class SaveDraftRequest(BaseModel):
    skeleton: dict = Field(description="skeleton.yaml 相当の dict (atlas_skeleton キー可)")


class FreezeSkeletonRequest(BaseModel):
    version: str = Field(description="凍結版の版数 (例: 2026.1)")
    note: str = Field(default="", description="changelog に残すメモ")
    credits: list[str] = Field(default_factory=list, description="修正報告者などの帰属")


# ---------------------------------------------------------------------------
# 管理エンドポイント (教員以上)
# ---------------------------------------------------------------------------


@router.get("/{cartridge_id}/atlas/skeleton")
def get_atlas_skeleton_state(
    cartridge_id: str, current_user: dict = Depends(_require_teacher)
) -> dict:
    """骨格のレビュー状態 (draft / 凍結済み) を返す。"""
    return {
        "cartridge_id": cartridge_id,
        "draft": _skeleton_payload(_load_optional(_draft_path(cartridge_id))),
        "frozen": _skeleton_payload(_load_optional(_frozen_path(cartridge_id))),
    }


@router.post("/{cartridge_id}/atlas/skeleton/generate")
def generate_atlas_skeleton(
    cartridge_id: str,
    body: GenerateSkeletonRequest | None = None,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """骨格 draft を LLM バッチ生成する。一度だけ実行し、再実行は force 指定の明示操作。"""
    body = body or GenerateSkeletonRequest()
    draft_path = _draft_path(cartridge_id)
    if draft_path.exists() and not body.force:
        raise HTTPException(
            status_code=409,
            detail="draft が既に存在します。再生成する場合は force を指定してください",
        )

    from core.atlas_generator import generate_skeleton_draft

    try:
        skeleton = generate_skeleton_draft(cartridge_id, model=body.model)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    atlas.write_skeleton(skeleton, draft_path)
    _record_review_event(
        cartridge_id,
        "",
        atlas.STATUS_DRAFT,
        current_user.get("id"),
        {"action": "generate", "generated_by": skeleton.generated_by, "force": body.force},
    )
    return {"cartridge_id": cartridge_id, "draft": _skeleton_payload(skeleton)}


@router.put("/{cartridge_id}/atlas/skeleton/draft")
def save_atlas_skeleton_draft(
    cartridge_id: str,
    body: SaveDraftRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """教員レビューによる draft の修正を保存する (領域・概念・配置・エッジ・seed_status)。"""
    draft_path = _draft_path(cartridge_id)
    existing = _load_optional(draft_path)
    try:
        edited = atlas.parse_skeleton(body.skeleton)
    except atlas.SkeletonParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # 来歴は編集で失わせない。status は凍結エンドポイント以外では draft のまま
    generated_by = edited.generated_by or (existing.generated_by if existing else "")
    draft = atlas.AtlasSkeleton(
        cartridge=cartridge_id,
        status=atlas.STATUS_DRAFT,
        version="",
        generated_by=generated_by,
        reviewed_by=(),
        changelog=existing.changelog if existing else (),
        regions=edited.regions,
        edges=edited.edges,
        concept_bindings=edited.concept_bindings,
        id_migrations=edited.id_migrations,
    )
    report = atlas.validate_skeleton(draft)
    if not report.ok:
        raise HTTPException(
            status_code=422,
            detail={"message": "骨格がスキーマに適合しません", "errors": list(report.errors)},
        )
    atlas.write_skeleton(draft, draft_path)
    _record_review_event(
        cartridge_id,
        atlas.STATUS_DRAFT,
        atlas.STATUS_DRAFT,
        current_user.get("id"),
        {"action": "review_edit"},
    )
    return {"cartridge_id": cartridge_id, "draft": _skeleton_payload(draft)}


@router.post("/{cartridge_id}/atlas/skeleton/freeze")
def freeze_atlas_skeleton(
    cartridge_id: str,
    body: FreezeSkeletonRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """承認された draft を凍結して版を付与し、カートリッジに同梱する。

    - 承認で reviewed_by に帰属を記録する (受け入れ条件2)
    - 凍結後は不変。修正は次版で行う
    - 採用済みの修正報告 (Issue D) の報告者を changelog[].credits に自動で合流し、
      当該報告に applied_version を刻印してクローズする。pending の報告は
      新版へ引き継ぎ、id_migrations に従って対象 node_id を付け替える
    """
    draft = _load_optional(_draft_path(cartridge_id))
    if draft is None:
        raise HTTPException(status_code=404, detail="凍結対象の draft がありません")

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

    atlas.write_skeleton(frozen, _frozen_path(cartridge_id))
    _draft_path(cartridge_id).unlink(missing_ok=True)
    cartridges_module.clear_cache()

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
    return {"cartridge_id": cartridge_id, "frozen": _skeleton_payload(frozen)}


# ---------------------------------------------------------------------------
# 学習者向けエンドポイント (凍結済みのみ。draft は決して返さない)
# ---------------------------------------------------------------------------


@learning_router.get("/{cartridge_id}/skeleton")
def get_learner_atlas_skeleton(
    cartridge_id: str, current_user: dict = Depends(_get_current_user)
) -> dict:
    """学習者向けの骨格。凍結・レビュー済みの版のみ返す。

    骨格未同梱・draft のみのカートリッジでは 404 (地図機能を出さない)。
    """
    try:
        cartridge = cartridges_module.load_cartridge(cartridge_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"cartridge '{cartridge_id}' not found") from exc
    except ValueError as exc:
        # 同梱骨格が不正 (draft 同梱など) — 学習者には存在しないものとして扱う
        logger.error("invalid bundled atlas skeleton for %s: %s", cartridge_id, exc)
        raise HTTPException(status_code=404, detail="atlas skeleton not available") from exc

    skeleton = cartridge.learner_atlas_skeleton
    if skeleton is None:
        raise HTTPException(status_code=404, detail="atlas skeleton not available")
    return atlas.learner_view(skeleton)


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
        entity_type="atlas_report",
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
    _cartridge_dir(cartridge_id)  # 存在チェック (404)
    if status and status not in atlas_reports.REPORT_STATUSES:
        raise HTTPException(status_code=422, detail=f"不明な status です: {status}")
    frozen = _load_optional(_frozen_path(cartridge_id))
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
    _cartridge_dir(cartridge_id)  # 存在チェック (404)
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
        entity_type="atlas_report",
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

    frozen = _load_optional(_frozen_path(cartridge_id))
    if frozen is None:
        try:
            cartridge = cartridges_module.load_cartridge(cartridge_id)
            frozen = cartridge.atlas_skeleton
        except (FileNotFoundError, ValueError):
            frozen = None
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
