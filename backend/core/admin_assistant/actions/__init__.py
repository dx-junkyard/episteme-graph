"""操作代行 tool 群（設計 §6.1）。

各 tool は `capture_before` / `apply` / `revert` を宣言する。`apply` は既存の
エンドポイントと同じ DB 変更を行う薄いラッパ（P7: 既存契約を変えない）。owner スコープは
SQL の `WHERE user_id = :uid` で強制する（既存 visibility エンドポイントと同一意味論）。

段階登録（P1 / 設計 §13-5）: ここに handler がある capability のみ代行実行できる。
未実装の action capability は route が「未対応（画面で操作してください）」に縮退する。
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import text as sa_text

from core.postgres import get_session as _pg_session


class ActionError(Exception):
    """操作代行の一般エラー。"""


class ActionTargetError(ActionError):
    """対象が見つからない / 所有していない（route → 404/403）。"""


class ActionArgError(ActionError):
    """引数不足・不正（route → 400）。"""


@dataclass
class ActionContext:
    user_id: str
    role: str
    target_id: Optional[str]
    args: dict = field(default_factory=dict)


class AssistantAction:
    capability_id: str = ""

    def capture_before(self, ctx: ActionContext) -> dict:  # pragma: no cover - interface
        raise NotImplementedError

    def apply(self, ctx: ActionContext, before: dict) -> dict:  # pragma: no cover
        raise NotImplementedError

    def revert(self, ctx: ActionContext, before: dict) -> None:  # pragma: no cover
        raise NotImplementedError


_VISIBILITY_VALUES = ("public", "group", "private")


def _fetch_course_state(course_id: str, user_id: str) -> Optional[dict]:
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT visibility,
                       group_id::text AS group_id,
                       COALESCE(is_published, false) AS is_published,
                       COALESCE(is_template, false) AS is_template
                FROM learning_courses
                WHERE id = :cid AND user_id = CAST(:uid AS uuid)
            """),
            {"cid": course_id, "uid": user_id},
        ).fetchone()
        if row is None:
            return None
        m = row._mapping
        return {
            "visibility": m["visibility"],
            "group_id": m["group_id"],
            "is_published": bool(m["is_published"]),
            "is_template": bool(m["is_template"]),
        }
    finally:
        session.close()


def _write_course_state(course_id: str, user_id: str, state: dict) -> bool:
    session = _pg_session()
    try:
        res = session.execute(
            sa_text("""
                UPDATE learning_courses
                SET visibility = :visibility,
                    group_id = CAST(:group_id AS uuid),
                    is_published = :is_published,
                    is_template = :is_template,
                    updated_at = :ts
                WHERE id = :cid AND user_id = CAST(:uid AS uuid)
                RETURNING id
            """),
            {
                "visibility": state["visibility"],
                "group_id": state.get("group_id"),
                "is_published": bool(state["is_published"]),
                "is_template": bool(state["is_template"]),
                "cid": course_id,
                "uid": user_id,
                "ts": datetime.datetime.now(datetime.timezone.utc),
            },
        ).fetchone()
        session.commit()
        return res is not None
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class CourseSetVisibilityAction(AssistantAction):
    """コースの開示範囲変更（L2・可逆）。既存 PUT /courses/{id}/visibility と同一意味論。"""

    capability_id = "course.set_visibility"

    def capture_before(self, ctx: ActionContext) -> dict:
        if not ctx.target_id:
            raise ActionArgError("course_id が指定されていません")
        state = _fetch_course_state(ctx.target_id, ctx.user_id)
        if state is None:
            raise ActionTargetError("コースが見つからないか、あなたが所有者ではありません")
        return state

    def _target_state(self, before: dict, args: dict) -> dict:
        visibility = args.get("visibility")
        if visibility not in _VISIBILITY_VALUES:
            raise ActionArgError(f"visibility は {_VISIBILITY_VALUES} のいずれか: {visibility!r}")
        group_id = args.get("group_id") if visibility == "group" else None
        if visibility == "group" and not group_id:
            raise ActionArgError("visibility='group' には group_id が必要です")
        # 既存エンドポイント同様、public 化は published/template を立てる。
        is_published = True if visibility == "public" else before["is_published"]
        is_template = True if visibility == "public" else before["is_template"]
        return {
            "visibility": visibility,
            "group_id": group_id,
            "is_published": is_published,
            "is_template": is_template,
        }

    def apply(self, ctx: ActionContext, before: dict) -> dict:
        after = self._target_state(before, ctx.args)
        ok = _write_course_state(ctx.target_id, ctx.user_id, after)
        if not ok:
            raise ActionTargetError("コースの更新に失敗しました（所有者ではない可能性）")
        return after

    def revert(self, ctx: ActionContext, before: dict) -> None:
        ok = _write_course_state(ctx.target_id, ctx.user_id, before)
        if not ok:
            raise ActionTargetError("取り消し対象のコースが見つかりません")


class CoursePublishAction(AssistantAction):
    """コースを学生に公開する（確認ゲート / reversible=False）。

    公開 = visibility を public にし is_published / is_template を立てる（既存 visibility
    エンドポイントの public 化と同一効果）。設計上 Copilot からの取り消しは行わない
    （revert は route が 409 で拒否）。ただし P3 のため before は必ず保持する。
    """

    capability_id = "course.publish"

    def capture_before(self, ctx: ActionContext) -> dict:
        if not ctx.target_id:
            raise ActionArgError("course_id が指定されていません")
        state = _fetch_course_state(ctx.target_id, ctx.user_id)
        if state is None:
            raise ActionTargetError("コースが見つからないか、あなたが所有者ではありません")
        return state

    def apply(self, ctx: ActionContext, before: dict) -> dict:
        after = {
            "visibility": "public",
            "group_id": None,
            "is_published": True,
            "is_template": True,
        }
        ok = _write_course_state(ctx.target_id, ctx.user_id, after)
        if not ok:
            raise ActionTargetError("コースの公開に失敗しました（所有者ではない可能性）")
        return after

    def revert(self, ctx: ActionContext, before: dict) -> None:  # pragma: no cover
        raise ActionError("この操作は取り消せません")


_HANDLERS: dict[str, AssistantAction] = {
    CourseSetVisibilityAction.capability_id: CourseSetVisibilityAction(),
    CoursePublishAction.capability_id: CoursePublishAction(),
}


def get_handler(capability_id: str) -> Optional[AssistantAction]:
    return _HANDLERS.get(capability_id)


def supported_capability_ids() -> list[str]:
    return list(_HANDLERS.keys())
