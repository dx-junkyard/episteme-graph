"""分野マップのベクトル係留層 — 索引の状態・再構築と別名レジストリの管理 API
（実パス ``/api/admin/cartridges/{cartridge_id}/atlas/...``）。

正本: ``docs/features/atlas_vector_anchoring_design.md`` §5（構築トリガーと API）/
§7（別名レジストリ）。migration 074（``atlas_anchor_embeddings`` /
``atlas_anchor_aliases``）。

本ルータの立場（設計書 §2 の不変条項の写像）:

- **VA1 確定は常に人間**: ベクトル類似から別名・骨格ノードが自動確定する経路を持たない。
  別名の登録・見送りはいずれも教員の明示 POST であり、近傍注記（読み時導出）は
  本ルータでは扱わない（注記は ``atlas_gaps`` のレビューキュー側で付与される）。
- **VA2 数値非表示**: 返す数値は索引カバレッジ（``total_nodes`` / ``embedded_nodes``）
  だけで、これは設計書 §2 が明示する例外（評価数値ではなく運用状態の事実）。
  cosine / similarity をレスポンスに載せない。
- **VA3 埋め込みは教員起点のみ**: 埋め込みを起こしうるのは本ルータの refresh と
  別名登録後の単ノード再構築だけで、いずれも ``_require_teacher`` の配下にある。
- **VA4 fail-soft**: 別名登録後のプロトタイプ再構築は best-effort（失敗しても登録は
  成功のまま）。骨格が無い分野の status は 404 ではなく ``{"available": false}``。
- **VA6 情報を落とさない**: DELETE ルートを作らない。別名の見送りは
  ``status='dismissed'`` への遷移で、同じ表記の再登録が復帰になる。
- **VA9 骨格へ書き込まない**: 骨格は ``atlas_store`` 経由で**読むだけ**。

エラーの ``detail`` は日本語の事実文で、件数・内部の例外文言・接続先を書かない。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text as sa_text

import services
from core import atlas_store
from core.atlas_vectors import builder as vector_builder
from core.atlas_vectors import schema as vector_schema
from core.atlas_vectors import store as vector_store
from core.schema import AUDIT_ENTITY_ATLAS_VECTOR
from dependencies import _require_teacher

logger = logging.getLogger(__name__)

# /api/admin/cartridges/... 配下（main.py が prefix="/api/admin" でフラット登録する。
# CLAUDE.md Tier 3-17c: admin 系子ルーターは admin.router に include しない）
router = APIRouter(prefix="/cartridges", tags=["Admin"])


# ---------------------------------------------------------------------------
# 事実文（VA2 / VA8: 数値・内部情報を書かない。原因と次の一手だけを述べる）
# ---------------------------------------------------------------------------

_DETAIL_RETIRED_DOMAIN = (
    "この分野は廃止されています。復帰してから索引を作り直してください。"
)
_DETAIL_REFRESH_FAILED = (
    "索引を作り直せませんでした。しばらく時間をおいてからもう一度お試しください。"
)
_DETAIL_ALIAS_EMPTY = "別の表記を入力してください。"
_DETAIL_ALIAS_SOURCE = "別名の出所の指定が正しくありません。"
_DETAIL_NODE_NOT_IN_SKELETON = (
    "この項目は公開中の骨格にありません。骨格にある項目を指定してください。"
)
_DETAIL_NO_FROZEN_SKELETON = "この分野には凍結済みの骨格がありません。"
_DETAIL_ALIAS_NOT_FOUND = "この別名は見つかりません。"
_DETAIL_ALIAS_REJECTED = "別名を登録できませんでした。入力内容をご確認ください。"


# ---------------------------------------------------------------------------
# セッション・共通ヘルパー（atlas_gaps.py と同じ流儀）
# ---------------------------------------------------------------------------


def _session():
    """アンカー索引・別名の読み書き用セッション。取得不能は 503。"""
    try:
        from core.postgres import get_session

        return get_session()
    except Exception as exc:  # noqa: BLE001
        logger.error("atlas vector DB session unavailable", exc_info=True)
        raise HTTPException(status_code=503, detail="データベースに接続できません") from exc


def _record_vector_event(
    entity_id: str,
    old_status: str,
    new_status: str,
    user_id: Any,
    metadata: dict,
) -> None:
    """本層の操作を監査記録する（設計書 §7）。DB 不通でも操作自体は落とさない。"""
    try:
        services.record_review_event(
            AUDIT_ENTITY_ATLAS_VECTOR,
            entity_id,
            old_status,
            new_status,
            str(user_id or "") or None,
            metadata,
        )
    except Exception:  # noqa: BLE001
        logger.warning("atlas vector review event skipped", exc_info=True)


def _node_labels(skeleton: Any) -> dict[str, str]:
    """現行凍結骨格の ``{node_id: label}``（region / concept を同じ辞書に混ぜる）。

    別名一覧の ``node_label`` 補完と、登録時のノード実在検査の両方が使う。
    """
    labels: dict[str, str] = {}
    for region in getattr(skeleton, "regions", ()) or ():
        region_id = str(getattr(region, "id", "") or "").strip()
        if not region_id:
            continue
        labels[region_id] = str(getattr(region, "label", "") or "") or region_id
        for concept in getattr(region, "concepts", ()) or ():
            concept_id = str(getattr(concept, "id", "") or "").strip()
            if not concept_id:
                continue
            labels[concept_id] = (
                str(getattr(concept, "label", "") or "") or concept_id
            )
    return labels


def _frozen_skeleton(session, cartridge_id: str):
    """現行凍結骨格（無ければ ``None``）。読みは常に ``atlas_store`` 経由（VA9）。"""
    return atlas_store.load_frozen_skeleton(session, cartridge_id)


def _has_any_embeddings(session, cartridge_id: str) -> bool:
    """このドメインに（版を問わず）アンカー行が1件でもあるか。

    ``stale``（骨格が更新され索引が古い版のまま）の判定に使う。読めなければ
    False = 「古い索引があるとは言わない」の慎重側へ倒す（VA4 / VA8）。
    """
    try:
        row = session.execute(
            sa_text(
                "SELECT 1 FROM atlas_anchor_embeddings "
                "WHERE domain_key = :domain_key LIMIT 1"
            ),
            {"domain_key": str(cartridge_id or "").strip()},
        ).fetchone()
    except Exception:  # noqa: BLE001 — 状態表示のための補助照会
        logger.warning("atlas anchor row probe failed (non-fatal)", exc_info=True)
        return False
    return row is not None


def _rebuild_node_in_background(cartridge_id: str, node_id: str) -> None:
    """1ノードのプロトタイプを best-effort で作り直す（VA4）。

    別名の登録は「教員の確定操作」であって埋め込みの成否に依存しない。埋め込みは
    外部 API を呼ぶため daemon thread に逃がし、どんな例外も警告ログで飲み込む
    （main.py の help_kb ベクトル同期と同じ形）。
    """

    def _run() -> None:
        try:
            result = vector_builder.build_anchor_embeddings(
                cartridge_id, node_ids=[node_id]
            )
            logger.info("atlas anchor rebuild after alias registration: %s", result)
        except Exception:  # noqa: BLE001
            logger.warning("atlas anchor rebuild after alias failed", exc_info=True)

    try:
        threading.Thread(
            target=_run, name="atlas-anchor-embed-alias", daemon=True
        ).start()
    except Exception:  # noqa: BLE001 — スレッドすら起こせなくても登録は成功のまま
        logger.warning("atlas anchor rebuild scheduling skipped", exc_info=True)


# ---------------------------------------------------------------------------
# リクエストモデル
# ---------------------------------------------------------------------------


class RegisterAliasRequest(BaseModel):
    node_id: str = Field(description="現行凍結骨格の region / concept の id")
    alias: str = Field(description="同じものを指す別の表記")
    source: str = Field(
        default="manual", description="gap_signal（候補からの登録）| manual（手入力）"
    )
    evidence: Optional[dict] = Field(
        default=None, description="出所の手がかり（cluster_key / note など）"
    )


# ---------------------------------------------------------------------------
# 索引の状態（§5 status）
# ---------------------------------------------------------------------------


@router.get("/{cartridge_id}/atlas/vectors/status")
def get_atlas_vector_status(
    cartridge_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """アンカー索引の状態（骨格なしは ``{"available": false}``）。

    返す形: ``{available, domain_key, skeleton_version, total_nodes, embedded_nodes,
    built_at, stale}``。件数は運用状態の事実（VA2 が明示する例外）で、類似度の類は
    一切返さない。``stale`` は「現行凍結版の索引が無く、別の版の索引だけがある」
    ＝骨格が更新されて索引が追随していない状態。
    """
    session = _session()
    try:
        skeleton = _frozen_skeleton(session, cartridge_id)
        if skeleton is None:
            return {"available": False}
        version = str(getattr(skeleton, "version", "") or "")
        labels = _node_labels(skeleton)
        coverage = vector_store.coverage_status(session, cartridge_id, version)
        stale = coverage["total_rows"] == 0 and _has_any_embeddings(
            session, cartridge_id
        )
    finally:
        session.close()

    built_at = coverage.get("built_at")
    return {
        "available": True,
        "domain_key": cartridge_id,
        "skeleton_version": version,
        "total_nodes": len(labels),
        "embedded_nodes": int(coverage.get("embedded_rows") or 0),
        "built_at": built_at.isoformat() if hasattr(built_at, "isoformat") else None,
        "stale": bool(stale),
    }


# ---------------------------------------------------------------------------
# 手動 refresh（§5。既存凍結骨格のバックフィルの非常口）
# ---------------------------------------------------------------------------


@router.post("/{cartridge_id}/atlas/vectors/refresh")
def refresh_atlas_vectors(
    cartridge_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """現行凍結骨格のアンカー索引を作り直す（教員の明示操作）。

    運用の主経路は凍結時の自動再構築で、本 API はその**前**に凍結された骨格を
    埋めるための手段。retired ドメインは 409（読み取り専用 — atlas の freeze と同じ）。
    ``builder`` の要約（``completed`` / ``skipped`` + 理由）をそのまま返し、
    スキップの理由を隠さない。
    """
    session = _session()
    try:
        if atlas_store.domain_lifecycle(session, cartridge_id) == "retired":
            raise HTTPException(status_code=409, detail=_DETAIL_RETIRED_DOMAIN)
    finally:
        session.close()

    try:
        summary = vector_builder.build_anchor_embeddings(cartridge_id)
    except Exception as exc:  # noqa: BLE001
        # 例外の中身（接続先・スタック）は detail に載せない。ログにだけ残す。
        logger.warning("atlas anchor refresh failed for %s", cartridge_id, exc_info=True)
        raise HTTPException(status_code=422, detail=_DETAIL_REFRESH_FAILED) from exc

    status = str(summary.get("status") or "")
    _record_vector_event(
        cartridge_id,
        "",
        status,
        current_user.get("id"),
        {
            "action": vector_schema.AUDIT_ACTIONS[0],
            "cartridge_id": cartridge_id,
            "result": summary,
        },
    )
    return summary


# ---------------------------------------------------------------------------
# 別名レジストリ（§7。確定は教員の明示操作のみ — VA1）
# ---------------------------------------------------------------------------


@router.get("/{cartridge_id}/atlas/aliases")
def list_atlas_aliases(
    cartridge_id: str,
    include_dismissed: bool = Query(
        False, description="true で見送り済みも含める（復帰導線用）"
    ),
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """登録済みの別名一覧。``node_label`` は現行凍結骨格から補う（引けなければ空）。"""
    session = _session()
    try:
        rows = vector_store.list_aliases(
            session, cartridge_id, include_dismissed=include_dismissed
        )
        skeleton = _frozen_skeleton(session, cartridge_id)
    finally:
        session.close()

    labels = _node_labels(skeleton) if skeleton is not None else {}
    aliases = [dict(row, node_label=labels.get(row.get("node_id"), "")) for row in rows]
    return {"cartridge_id": cartridge_id, "aliases": aliases}


@router.post("/{cartridge_id}/atlas/aliases")
def register_atlas_alias(
    cartridge_id: str,
    body: RegisterAliasRequest,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """別名を登録する（見送り済みの同じ表記があれば復帰させる — VA6）。

    ``node_id`` は現行凍結骨格に実在するものだけを受ける（骨格に無い項目への別名は
    どこにも係留されないため 422）。登録後、そのノードのプロトタイプを best-effort で
    作り直す（失敗しても登録は成功のまま — VA4）。
    """
    node_id = str(body.node_id or "").strip()
    alias = str(body.alias or "").strip()
    source = str(body.source or "").strip()
    if not alias or not vector_schema.normalize_label(alias):
        raise HTTPException(status_code=422, detail=_DETAIL_ALIAS_EMPTY)
    if source not in vector_schema.ALIAS_SOURCES:
        raise HTTPException(status_code=422, detail=_DETAIL_ALIAS_SOURCE)

    session = _session()
    try:
        skeleton = _frozen_skeleton(session, cartridge_id)
        if skeleton is None:
            raise HTTPException(status_code=422, detail=_DETAIL_NO_FROZEN_SKELETON)
        labels = _node_labels(skeleton)
        if node_id not in labels:
            raise HTTPException(status_code=422, detail=_DETAIL_NODE_NOT_IN_SKELETON)

        row = vector_store.upsert_alias(
            session,
            domain_key=cartridge_id,
            node_id=node_id,
            alias=alias,
            source=source,
            evidence=body.evidence or {},
            user_id=str(current_user.get("id") or ""),
        )
        session.commit()
    except ValueError as exc:
        # store 側の検証（帰属必須・語彙・正規化）はここで事実文に変換する。
        # 例外文言は内部の英語メッセージなので detail に載せない。
        session.rollback()
        logger.info("atlas alias rejected by store validation", exc_info=True)
        raise HTTPException(status_code=422, detail=_DETAIL_ALIAS_REJECTED) from exc
    except HTTPException:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    _record_vector_event(
        cartridge_id,
        "",
        str(row.get("status") or ""),
        current_user.get("id"),
        {
            "action": vector_schema.AUDIT_ACTIONS[1],
            "cartridge_id": cartridge_id,
            "node_id": node_id,
            "alias": alias,
            "source": source,
        },
    )
    _rebuild_node_in_background(cartridge_id, node_id)
    return dict(row, node_label=labels.get(node_id, ""))


@router.post("/{cartridge_id}/atlas/aliases/{alias_id}/dismiss")
def dismiss_atlas_alias(
    cartridge_id: str,
    alias_id: str,
    current_user: dict = Depends(_require_teacher),
) -> dict:
    """別名を見送りにする（**行は消さない**。同じ表記の再登録で戻る — VA6）。

    別分野の別名 id を渡された場合は 404（不在と同じ応答にして、他分野に何が
    登録されているかを漏らさない）。
    """
    session = _session()
    try:
        existing = vector_store.get_alias(session, alias_id)
        if existing is None or existing.get("domain_key") != cartridge_id:
            raise HTTPException(status_code=404, detail=_DETAIL_ALIAS_NOT_FOUND)
        old_status = str(existing.get("status") or "")
        row = vector_store.dismiss_alias(
            session, alias_id, user_id=str(current_user.get("id") or "")
        )
        if row is None:
            session.rollback()
            raise HTTPException(status_code=404, detail=_DETAIL_ALIAS_NOT_FOUND)
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

    _record_vector_event(
        cartridge_id,
        old_status,
        str(row.get("status") or ""),
        current_user.get("id"),
        {
            "action": vector_schema.AUDIT_ACTIONS[2],
            "cartridge_id": cartridge_id,
            "node_id": str(row.get("node_id") or ""),
        },
    )
    return row
