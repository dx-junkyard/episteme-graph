"""LLM モデル選択（M層）API — migration 061。

実パス: ``/api/admin/llm-models/...``。設計の正本は
``docs/features/llm_model_selection_design.md`` §7・§11（library.py / llm_usage.py と
同型の単独マウント型ルーター、開発ルール2/7）。

- ``GET /catalog?scene=``（TEACHER 以上）: 選択肢（provider / capability で絞り込み済み）
  + 各 scene の**リクエストユーザーにとっての**実効モデルと出所。
- ``GET /pipeline-stages``（TEACHER 以上、Phase 4）: 解析パイプラインの LLM ステージを
  ``orchestrator.PIPELINE_STAGES`` の順序で一覧し、各ステージの ``feature``
  （``pipeline:<stage>``）/ ``label`` / ``vision`` / 本人にとっての実効モデルを返す
  （設計書 §6.1「▸ ステージ別に指定する（詳細）」・§6.6「▸ ステージ別の指定（N件）」向け）。
- ``GET /policies``（SYSTEM_ADMIN のみ）: システム既定一覧 + env 由来の事実併記。各行に
  ``is_feature_level``（``pipeline:<stage>`` 等の feature 単位上書きか）と ``label`` を
  付与する（Phase 4）。
- ``PUT|DELETE /policies/{scene_key}``（SYSTEM_ADMIN のみ）: システム既定の変更・解除。
  ``scene_key`` には scene 名だけでなく ``pipeline:<stage>`` のような feature 単位の
  キーも指定できる（fail-closed 検証は ``llm_policy.validate_model_for_scene`` に委譲）。
- ``PUT|DELETE /my-policies/{scene_key}``（TEACHER 以上）: 本人のユーザー別既定の変更・解除
  （``user_id`` は認証ユーザー固定。body で受け付けない）。

バリデーションはすべてサーバ側で fail-closed（M4/M5）: 未知の scene_key、カタログに
無い model、provider 不一致、capability 不足、カタログ未設定はすべて 422。

``llm_usage_events`` には一切触れない（U6 の append-only を侵さない）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

import services
from dependencies import _require_system_admin, _require_teacher

from core import llm_policy
from core import llm_policy_store
from core.document_pipeline.orchestrator import LLM_STAGE_NAMES, PIPELINE_STAGES
from core.llm_usage.context import usage_context
from core.postgres import get_session
from core.schema import AUDIT_ENTITY_LLM_MODEL_POLICY

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/llm-models", tags=["LLM Models"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class PolicyUpsertRequest(BaseModel):
    model: str
    reasoning_effort: str | None = None
    note: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _uid(current_user: dict) -> str | None:
    return current_user.get("id") or current_user.get("user_id")


def _required_capability(scene_key: str) -> str | None:
    return llm_policy.required_capability_for_scene(scene_key)


def _representative_feature(scene_key: str) -> str:
    """scene 表示に使う代表 feature を返す。scene_key がステージ別 feature 単独
    指定（``pipeline:claim_qualification`` 等）の場合はそれ自身を返す。"""
    entry = llm_policy.SCENES.get(scene_key)
    if entry is not None:
        features = entry.get("features") or ()
        return features[0] if features else scene_key
    return scene_key


def _validate_scene_key(scene_key: str) -> None:
    """scene_key が SCENES のキー、または KNOWN_FEATURES のいずれかであることを要求する（M4）。"""
    if not llm_policy.is_known_scene_key(scene_key):
        raise HTTPException(status_code=422, detail=f"unknown scene_key: {scene_key!r}")


def _validate_model_choice(scene_key: str, model: str) -> None:
    """カタログ収載 + provider 一致 + capability を満たすことを要求する（M4/M5）。

    実体は :func:`core.llm_policy.validate_model_for_scene`（正本, §11-2）。
    カタログ未設定時は検証できないため 422（書かせない）。
    """
    reason = llm_policy.validate_model_for_scene(scene_key, model)
    if reason:
        raise HTTPException(status_code=422, detail=reason)


def _scene_label(scene_key: str) -> str:
    entry = llm_policy.SCENES.get(scene_key)
    return str(entry["label"]) if entry is not None else scene_key


def _label_for_scene_key(scene_key: str) -> str:
    """scene_key（scene 名 or ``pipeline:<stage>`` 等の feature 単位キー）の表示名。

    scene 名は :data:`llm_policy.SCENES` のラベル、ステージ別 feature キーは
    :func:`llm_policy.pipeline_stage_label` のラベル。どちらにも無ければ
    scene_key をそのまま返す（捏造しない、M4 と同じ姿勢）。
    """
    entry = llm_policy.SCENES.get(scene_key)
    if entry is not None:
        return str(entry["label"])
    if scene_key.startswith("pipeline:"):
        stage_name = scene_key.split(":", 1)[1]
        return llm_policy.pipeline_stage_label(stage_name)
    return scene_key


def _build_scene_payload(scene_key: str, *, user_id: str | None, catalog_available: bool) -> dict:
    feature = _representative_feature(scene_key)
    with usage_context(feature, user_id=user_id):
        resolved = llm_policy.resolve_scene_model(feature)

    if catalog_available:
        capability = _required_capability(scene_key)
        candidates = llm_policy.catalog_models(capability=capability)
        options = [
            {
                "id": entry.get("id"),
                "cost_hint": entry.get("cost_hint"),
                "note": entry.get("note", ""),
                "is_current": entry.get("id") == resolved.model,
            }
            for entry in candidates
        ]
    else:
        # M4: カタログが無い環境では現在の実効モデル1件のみ（架空の候補を並べない）。
        options = [
            {"id": resolved.model, "cost_hint": None, "note": "", "is_current": True}
        ]

    return {
        "scene_key": scene_key,
        "label": _scene_label(scene_key),
        "effective": {
            "model": resolved.model,
            "source": resolved.source,
            "source_label": resolved.source_label,
        },
        "options": options,
    }


def _audit(entity_id: str, old_status: str, new_status: str, user_id: str | None, metadata: dict | None = None) -> None:
    services.record_review_event(
        AUDIT_ENTITY_LLM_MODEL_POLICY, entity_id, old_status, new_status, user_id, metadata or {}
    )


# ---------------------------------------------------------------------------
# GET /catalog — 選択肢 + 実効モデル（TEACHER 以上）
# ---------------------------------------------------------------------------


@router.get("/catalog")
def get_catalog(
    scene: str | None = Query(default=None),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    uid = _uid(current_user)
    catalog_available = llm_policy.load_catalog() is not None

    if scene:
        _validate_scene_key(scene)
        scene_keys = [scene]
    else:
        scene_keys = list(llm_policy.SCENES.keys())

    scenes = [
        _build_scene_payload(key, user_id=uid, catalog_available=catalog_available)
        for key in scene_keys
    ]
    return {"scenes": scenes, "catalog_available": catalog_available}


# ---------------------------------------------------------------------------
# GET /pipeline-stages — ステージ別（pipeline:<stage>）詳細 UI 向け一覧（TEACHER 以上）
# ---------------------------------------------------------------------------


@router.get("/pipeline-stages")
def get_pipeline_stages(current_user: dict = Depends(_require_teacher)) -> dict:
    """解析パイプラインの LLM ステージを ``PIPELINE_STAGES`` の順序で一覧する。

    設計書 §6.1「▸ ステージ別に指定する（詳細）」・§6.6「▸ ステージ別の指定（N件）」
    向けの読み取り専用 API。非 LLM ステージ（document_structure 等）は含めない
    （順序の正本は ``orchestrator.PIPELINE_STAGES``、対象の絞り込みは
    ``orchestrator.LLM_STAGE_NAMES`` との交差）。
    """
    uid = _uid(current_user)

    stages = []
    for stage_name in PIPELINE_STAGES:
        if stage_name not in LLM_STAGE_NAMES:
            continue
        feature = f"pipeline:{stage_name}"
        scene_key = llm_policy.scene_for_feature(feature) or llm_policy.SCENE_PIPELINE
        with usage_context(feature, user_id=uid):
            resolved = llm_policy.resolve_scene_model(feature)
        stages.append(
            {
                "stage": stage_name,
                "feature": feature,
                "label": llm_policy.pipeline_stage_label(stage_name),
                "vision": scene_key == llm_policy.SCENE_PIPELINE_VISION,
                "effective": {
                    "model": resolved.model,
                    "source": resolved.source,
                    "source_label": resolved.source_label,
                },
            }
        )

    return {"stages": stages, "catalog_available": llm_policy.load_catalog() is not None}


# ---------------------------------------------------------------------------
# システム既定（SYSTEM_ADMIN のみ）
# ---------------------------------------------------------------------------


@router.get("/policies")
def list_system_policies(current_user: dict = Depends(_require_system_admin)) -> dict:
    session = get_session()
    try:
        rows = llm_policy_store.list_system_policies(session)
    finally:
        session.close()

    # ステージ別（pipeline:<stage>）行は SCENES に無い feature キーとして保存される
    # ので、UI が「システム既定」表と「ステージ別の指定」表を区別できるよう
    # is_feature_level + label を各行に付与する（§6.6「ステージ別の指定（N件）」）。
    for row in rows:
        scene_key = row["scene_key"]
        row["is_feature_level"] = scene_key not in llm_policy.SCENES
        row["label"] = _label_for_scene_key(scene_key)

    scenes = []
    for scene_key, entry in llm_policy.SCENES.items():
        feature = _representative_feature(scene_key)
        # 運用タブの「システム既定」表示は本人（SYSTEM_ADMIN)の個人上書きを無視し、
        # system/env/tier の視点で見せる。usage_context を張らない = user_id なしの
        # resolve_scene_model を呼ぶことで scope='user' 層をスキップさせる。
        resolved = llm_policy.resolve_scene_model(feature)
        env_model, _fallback_tier = llm_policy._env_model_and_tier(feature)  # noqa: SLF001
        scenes.append(
            {
                "scene_key": scene_key,
                "label": entry["label"],
                "env_value": env_model,
                "current": {
                    "model": resolved.model,
                    "source": resolved.source,
                    "source_label": resolved.source_label,
                },
            }
        )
    return {"policies": rows, "scenes": scenes}


@router.put("/policies/{scene_key}")
def put_system_policy(
    scene_key: str,
    payload: PolicyUpsertRequest,
    current_user: dict = Depends(_require_system_admin),
) -> dict:
    uid = _uid(current_user)
    _validate_scene_key(scene_key)
    _validate_model_choice(scene_key, payload.model)

    session = get_session()
    try:
        existing = llm_policy_store.get_policy(session, scope="system", user_id=None, scene_key=scene_key)
        row = llm_policy_store.upsert_policy(
            session,
            scope="system",
            user_id=None,
            scene_key=scene_key,
            model=payload.model,
            reasoning_effort=payload.reasoning_effort,
            updated_by=uid,
            note=payload.note,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    _audit(
        scene_key,
        existing["model"] if existing else "",
        payload.model,
        uid,
        {"scope": "system", "reasoning_effort": payload.reasoning_effort, "note": payload.note},
    )
    return row


@router.delete("/policies/{scene_key}")
def delete_system_policy(
    scene_key: str, current_user: dict = Depends(_require_system_admin)
) -> dict:
    uid = _uid(current_user)
    session = get_session()
    try:
        existing = llm_policy_store.get_policy(session, scope="system", user_id=None, scene_key=scene_key)
        if existing is None:
            raise HTTPException(status_code=404, detail="policy not found")
        llm_policy_store.delete_policy(session, scope="system", user_id=None, scene_key=scene_key)
        session.commit()
    except HTTPException:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    _audit(scene_key, existing["model"], "", uid, {"scope": "system"})
    return {"deleted": True}


# ---------------------------------------------------------------------------
# ユーザー別の既定（TEACHER 以上・本人固定）
# ---------------------------------------------------------------------------


@router.put("/my-policies/{scene_key}")
def put_my_policy(
    scene_key: str,
    payload: PolicyUpsertRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    uid = _uid(current_user)
    if not uid:
        raise HTTPException(status_code=401, detail="user id not found in token")
    _validate_scene_key(scene_key)
    _validate_model_choice(scene_key, payload.model)

    session = get_session()
    try:
        existing = llm_policy_store.get_policy(session, scope="user", user_id=uid, scene_key=scene_key)
        row = llm_policy_store.upsert_policy(
            session,
            scope="user",
            user_id=uid,
            scene_key=scene_key,
            model=payload.model,
            reasoning_effort=payload.reasoning_effort,
            updated_by=uid,
            note=payload.note,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    _audit(
        scene_key,
        existing["model"] if existing else "",
        payload.model,
        uid,
        {"scope": "user", "reasoning_effort": payload.reasoning_effort, "note": payload.note},
    )
    return row


@router.delete("/my-policies/{scene_key}")
def delete_my_policy(scene_key: str, current_user: dict = Depends(_require_teacher)) -> dict:
    uid = _uid(current_user)
    if not uid:
        raise HTTPException(status_code=401, detail="user id not found in token")

    session = get_session()
    try:
        existing = llm_policy_store.get_policy(session, scope="user", user_id=uid, scene_key=scene_key)
        if existing is None:
            raise HTTPException(status_code=404, detail="policy not found")
        llm_policy_store.delete_policy(session, scope="user", user_id=uid, scene_key=scene_key)
        session.commit()
    except HTTPException:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    _audit(scene_key, existing["model"], "", uid, {"scope": "user"})
    return {"deleted": True}
