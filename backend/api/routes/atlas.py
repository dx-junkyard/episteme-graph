"""分野の地図 — 骨格の生成・レビュー・凍結 API (Issue A-2 / A-3)。

- 管理 (教員以上): draft の生成 (明示操作のみ)・修正・承認・凍結
- 学習者向け: 凍結済み骨格のみ返す。draft はいかなる場合も返さない (Issue A-2)
- 状態変更は theory_review_events に entity_type='atlas_skeleton' で監査記録する
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core import atlas
from core import cartridges as cartridges_module
from dependencies import _get_current_user, _require_teacher

logger = logging.getLogger(__name__)

# /api/admin/cartridges/... 配下 (routes/admin.py がインクルード)
router = APIRouter(prefix="/cartridges", tags=["Admin"])

# 学習者向け (main.py がインクルード)
learning_router = APIRouter(prefix="/api/learning/atlas", tags=["Learning"])


# ---------------------------------------------------------------------------
# 監査記録 (theory_components.py と同じ theory_review_events を使う)
# ---------------------------------------------------------------------------


def _record_review_event(
    entity_id: str,
    old_status: str,
    new_status: str,
    user_id: str | None,
    metadata: dict | None = None,
) -> None:
    """凍結・生成などの状態遷移を監査記録する。DB 不通時は警告のみ (非致命)。"""
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
                VALUES ('atlas_skeleton', :entity_id, :old_status, :new_status, CAST(:changed_by AS uuid), CAST(:metadata AS jsonb))
                """
            ),
            {
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
    """
    draft = _load_optional(_draft_path(cartridge_id))
    if draft is None:
        raise HTTPException(status_code=404, detail="凍結対象の draft がありません")

    reviewer = str(current_user.get("id") or "").strip()
    try:
        frozen = atlas.freeze_skeleton(
            draft,
            version=body.version,
            reviewed_by=[reviewer],
            note=body.note,
            credits=body.credits,
        )
    except atlas.SkeletonFreezeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    atlas.write_skeleton(frozen, _frozen_path(cartridge_id))
    _draft_path(cartridge_id).unlink(missing_ok=True)
    cartridges_module.clear_cache()
    _record_review_event(
        cartridge_id,
        atlas.STATUS_DRAFT,
        atlas.STATUS_FROZEN,
        current_user.get("id"),
        {"action": "freeze", "version": body.version, "note": body.note},
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
