"""LLM モデル選択（M層）— DB ポリシーバックエンド（migration 061 `llm_model_policies`）。

正本ドキュメント: docs/features/llm_model_selection_design.md §3・§4。
Phase 0 の :mod:`core.llm_policy` が定義する :class:`~core.llm_policy.PolicyBackend`
Protocol の DB 実装を提供する。``core.llm_policy`` の解決順序・precedence 定義は
一切変更しない（本モジュールは lookup の中身だけを提供する差し込み部品）。

FastAPI 非 import・LLM SDK 非 import（開発ルール2 / M1）。

セッション規約: :class:`DbPolicyBackend` の ``lookup()`` は ``core.postgres.get_session()``
を自前で開閉する（呼び出し元は生成呼び出しのホットパスの奥深くにあり、セッションを
渡せないため — ``core/schema_registry.py`` の TTL キャッシュと同型）。一方、CRUD 関数
（``list_system_policies`` / ``upsert_policy`` / ``delete_policy`` / ``seed_env_policies``）は
``core/atlas_store.py`` と同じ規約で **呼び出し側がセッションを管理する**
（``get_session()`` + try/finally + commit は呼び出し側の責務）。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from core import llm_policy
from core.postgres import get_session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TTL キャッシュ（LLM 1コールごとの SELECT を避ける）
# ---------------------------------------------------------------------------

_CACHE_TTL_SECONDS = 20.0

_cache_lock = threading.Lock()
# key = (scene_key, feature, user_id) -> (expires_at, PolicyRow | None)
_cache: dict[tuple[str, str, str | None], tuple[float, "llm_policy.PolicyRow | None"]] = {}


def invalidate() -> None:
    """キャッシュを全消去する。書き込み API（upsert/delete/seed）から呼ぶこと。"""
    with _cache_lock:
        _cache.clear()


def _cache_get(key: tuple[str, str, str | None]) -> tuple[bool, "llm_policy.PolicyRow | None"]:
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return False, None
        expires_at, value = entry
        if expires_at < time.monotonic():
            del _cache[key]
            return False, None
        return True, value


def _cache_set(key: tuple[str, str, str | None], value: "llm_policy.PolicyRow | None") -> None:
    with _cache_lock:
        _cache[key] = (time.monotonic() + _CACHE_TTL_SECONDS, value)


# ---------------------------------------------------------------------------
# DbPolicyBackend
# ---------------------------------------------------------------------------


class DbPolicyBackend:
    """``llm_model_policies`` を読む :class:`~core.llm_policy.PolicyBackend` 実装。

    優先順位（設計書 §4 コメント）: user+feature 行 > user+scene 行 >
    system+feature 行 > system+scene 行。``scene_key`` 列には scene キーと
    feature キーの両方が入りうるため、1 回の SELECT で両方を引いてから
    Python 側で優先順位づけする。

    fail-open: DB 例外時は warning ログを出して None を返す（LLM 呼び出しを
    止めない。``llm_model_policies`` テーブルが未作成の古い環境でも壊れない）。
    """

    def lookup(
        self, scene_key: str, feature: str, user_id: str | None
    ) -> "llm_policy.PolicyRow | None":
        cache_key = (scene_key, feature, user_id)
        hit, cached_value = _cache_get(cache_key)
        if hit:
            return cached_value

        try:
            row = self._lookup_uncached(scene_key, feature, user_id)
        except Exception:  # noqa: BLE001 — fail-open (M1 に反しない: 呼び出しを止めない)
            logger.warning(
                "llm_policy_store: lookup failed for scene_key=%r feature=%r user_id=%r",
                scene_key,
                feature,
                user_id,
                exc_info=True,
            )
            row = None

        _cache_set(cache_key, row)
        return row

    def _lookup_uncached(
        self, scene_key: str, feature: str, user_id: str | None
    ) -> "llm_policy.PolicyRow | None":
        session = get_session()
        try:
            rows = session.execute(
                sa_text(
                    """
                    SELECT scope, user_id::text AS user_id, scene_key, model, reasoning_effort
                    FROM llm_model_policies
                    WHERE scene_key IN (:feature, :scene_key)
                      AND (
                        scope = 'system'
                        OR (:user_id IS NOT NULL AND user_id = CAST(:user_id AS uuid))
                      )
                    """
                ),
                {"feature": feature, "scene_key": scene_key, "user_id": user_id},
            ).mappings().all()
        finally:
            session.close()

        return _pick_priority_row(rows, scene_key=scene_key, feature=feature, user_id=user_id)


def _pick_priority_row(
    rows: list[Any], *, scene_key: str, feature: str, user_id: str | None
) -> "llm_policy.PolicyRow | None":
    """user+feature > user+scene > system+feature > system+scene の優先順で1件選ぶ。"""
    if user_id:
        priority = [("user", feature), ("user", scene_key), ("system", feature), ("system", scene_key)]
    else:
        priority = [("system", feature), ("system", scene_key)]

    by_key: dict[tuple[str, str], Any] = {}
    for row in rows:
        if row["scope"] == "user" and row["user_id"] != user_id:
            continue
        by_key.setdefault((row["scope"], row["scene_key"]), row)

    for scope, key in priority:
        row = by_key.get((scope, key))
        if row is not None:
            return llm_policy.PolicyRow(
                model=row["model"], scope=row["scope"], reasoning_effort=row["reasoning_effort"]
            )
    return None


# ---------------------------------------------------------------------------
# CRUD（呼び出し側がセッションを管理する。core/atlas_store.py と同型）
# ---------------------------------------------------------------------------


def _row_to_dict(row: Any) -> dict:
    return {
        "id": str(row["id"]),
        "scope": row["scope"],
        "user_id": str(row["user_id"]) if row["user_id"] else None,
        "scene_key": row["scene_key"],
        "model": row["model"],
        "reasoning_effort": row["reasoning_effort"],
        "updated_by": str(row["updated_by"]) if row["updated_by"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        "note": row["note"] or "",
    }


def list_system_policies(session: Session) -> list[dict]:
    """``scope='system'`` の全行を一覧する（運用タブ「AIモデル」向け）。"""
    rows = session.execute(
        sa_text(
            """
            SELECT id, scope, user_id::text AS user_id, scene_key, model, reasoning_effort,
                   updated_by::text AS updated_by, updated_at, note
            FROM llm_model_policies
            WHERE scope = 'system'
            ORDER BY scene_key
            """
        )
    ).mappings().all()
    return [_row_to_dict(row) for row in rows]


def get_policy(session: Session, *, scope: str, user_id: str | None, scene_key: str) -> dict | None:
    """指定 ``(scope, user_id?, scene_key)`` の1行を返す（無ければ None）。

    API 側の 404 判定・監査記録の before スナップショット取得に使う。
    """
    if scope == "system":
        row = session.execute(
            sa_text(
                """
                SELECT id, scope, user_id::text AS user_id, scene_key, model, reasoning_effort,
                       updated_by::text AS updated_by, updated_at, note
                FROM llm_model_policies
                WHERE scope = 'system' AND scene_key = :scene_key
                """
            ),
            {"scene_key": scene_key},
        ).mappings().first()
    else:
        row = session.execute(
            sa_text(
                """
                SELECT id, scope, user_id::text AS user_id, scene_key, model, reasoning_effort,
                       updated_by::text AS updated_by, updated_at, note
                FROM llm_model_policies
                WHERE scope = 'user' AND user_id = CAST(:user_id AS uuid) AND scene_key = :scene_key
                """
            ),
            {"user_id": user_id, "scene_key": scene_key},
        ).mappings().first()
    return _row_to_dict(row) if row is not None else None


def list_user_policies(session: Session, user_id: str) -> list[dict]:
    """指定ユーザーの ``scope='user'`` 行を一覧する（本人の既定の確認用）。"""
    rows = session.execute(
        sa_text(
            """
            SELECT id, scope, user_id::text AS user_id, scene_key, model, reasoning_effort,
                   updated_by::text AS updated_by, updated_at, note
            FROM llm_model_policies
            WHERE scope = 'user' AND user_id = CAST(:user_id AS uuid)
            ORDER BY scene_key
            """
        ),
        {"user_id": user_id},
    ).mappings().all()
    return [_row_to_dict(row) for row in rows]


def upsert_policy(
    session: Session,
    *,
    scope: str,
    user_id: str | None,
    scene_key: str,
    model: str,
    reasoning_effort: str | None = None,
    updated_by: str | None,
    note: str = "",
) -> dict:
    """``(scope, user_id?, scene_key)`` の行を作成/更新する（部分 UNIQUE index に ON CONFLICT）。

    ``scope='system'`` は ``user_id`` を必ず None にする。``scope='user'`` は
    ``user_id`` が必須（呼び出し側が保証する。fail-closed 検証は API 層の責務）。
    """
    if scope not in ("system", "user"):
        raise ValueError(f"invalid scope: {scope!r}")
    if scope == "user" and not user_id:
        raise ValueError("user_id is required when scope='user'")
    if scope == "system":
        user_id = None

    conflict_target = "(user_id, scene_key) WHERE scope = 'user'" if scope == "user" else "(scene_key) WHERE scope = 'system'"

    row = session.execute(
        sa_text(
            f"""
            INSERT INTO llm_model_policies
                (scope, user_id, scene_key, model, reasoning_effort, updated_by, note, updated_at)
            VALUES
                (:scope, CAST(:user_id AS uuid), :scene_key, :model, :reasoning_effort,
                 CAST(:updated_by AS uuid), :note, now())
            ON CONFLICT {conflict_target}
            DO UPDATE SET
                model = EXCLUDED.model,
                reasoning_effort = EXCLUDED.reasoning_effort,
                updated_by = EXCLUDED.updated_by,
                note = EXCLUDED.note,
                updated_at = now()
            RETURNING id, scope, user_id::text AS user_id, scene_key, model, reasoning_effort,
                      updated_by::text AS updated_by, updated_at, note
            """
        ),
        {
            "scope": scope,
            "user_id": user_id,
            "scene_key": scene_key,
            "model": model,
            "reasoning_effort": reasoning_effort,
            "updated_by": updated_by,
            "note": note or "",
        },
    ).mappings().first()

    invalidate()
    return _row_to_dict(row)


def delete_policy(session: Session, *, scope: str, user_id: str | None, scene_key: str) -> bool:
    """指定行を削除する（既定へ戻す = restore）。削除できたら True、対象が無ければ False。"""
    if scope == "system":
        result = session.execute(
            sa_text(
                "DELETE FROM llm_model_policies WHERE scope = 'system' AND scene_key = :scene_key"
            ),
            {"scene_key": scene_key},
        )
    else:
        result = session.execute(
            sa_text(
                """
                DELETE FROM llm_model_policies
                WHERE scope = 'user' AND user_id = CAST(:user_id AS uuid) AND scene_key = :scene_key
                """
            ),
            {"user_id": user_id, "scene_key": scene_key},
        )
    invalidate()
    return (result.rowcount or 0) > 0


# ---------------------------------------------------------------------------
# env → DB シード取込（設計書 §3 手順⑤→④, 冪等）
# ---------------------------------------------------------------------------


def seed_env_policies(session: Session) -> int:
    """既存の ``*_LLM_MODEL`` 環境変数を ``scope='system'`` 行へ冪等に取り込む。

    既存の DB 行は絶対に上書きしない（存在しない scene_key のときのみ INSERT）。
    起動のたびに呼ばれる想定（atlas_skeletons / library の同梱シードと同じパターン）。
    返り値は新規に挿入した行数。
    """
    inserted = 0

    for feature, (attr, _fallback_tier) in llm_policy._FEATURE_ENV_SETTINGS.items():  # noqa: SLF001
        settings = _settings()
        value = (getattr(settings, attr, "") or "").strip()
        if not value:
            continue
        if _seed_one(session, scene_key=feature, model=value, note=f"seeded from env: {attr.upper()}"):
            inserted += 1

    for feature, (env_name, _fallback_tier) in llm_policy._FEATURE_DIRECT_ENV.items():  # noqa: SLF001
        value = (os.getenv(env_name, "") or "").strip()
        if not value:
            continue
        if _seed_one(session, scene_key=feature, model=value, note=f"seeded from env: {env_name}"):
            inserted += 1

    if inserted:
        invalidate()
    return inserted


def _settings():
    from core.config import get_settings

    return get_settings()


def _seed_one(session: Session, *, scene_key: str, model: str, note: str) -> bool:
    """存在しない場合のみ INSERT する（既存行は絶対に上書きしない）。

    ``ON CONFLICT ... DO NOTHING RETURNING id`` の1文で判定するため、
    check-then-insert のレースが起きない。
    """
    row = session.execute(
        sa_text(
            """
            INSERT INTO llm_model_policies (scope, user_id, scene_key, model, reasoning_effort, updated_by, note)
            VALUES ('system', NULL, :scene_key, :model, NULL, NULL, :note)
            ON CONFLICT (scene_key) WHERE scope = 'system' DO NOTHING
            RETURNING id
            """
        ),
        {"scene_key": scene_key, "model": model, "note": note},
    ).first()
    return row is not None


__all__ = [
    "DbPolicyBackend",
    "invalidate",
    "list_system_policies",
    "list_user_policies",
    "get_policy",
    "upsert_policy",
    "delete_policy",
    "seed_env_policies",
]
