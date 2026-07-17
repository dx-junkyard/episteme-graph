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
        # G1-6 是正: is_published は「現在 visibility='public' か」を常に正確に反映する
        # （admin.py::update_course_visibility の G1-1 是正と同一意味論。旧実装は public 化の
        # ときだけ True を立て、group/private へ戻しても is_published が True のまま残る
        # バグがあった — Copilot 経由の代行がサーバ本体と異なる状態を作ってしまっていた）。
        # is_template は「テンプレートとして作られたことがあるか」の意図を保つため、
        # 従来どおり public 化時のみ True を立て、離脱時にリセットはしない。
        is_published = visibility == "public"
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


# ---------------------------------------------------------------------------
# lecture_studio.rewrite_chunk_script（N12: 原点動機の rewrite 代行, L2 永続可逆）
# ---------------------------------------------------------------------------


def _json_list(value) -> list:
    """chunks.formulas（JSONB）を list に正規化する（None / 文字列も許容）。"""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            import json

            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (TypeError, ValueError):
            return []
    return []


def _fetch_chunk_script(chunk_id: str) -> Optional[dict]:
    """chunk の原稿状態（display_text / spoken_text / formulas）を読む。"""
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT display_text, spoken_text, formulas
                FROM chunks
                WHERE id = CAST(:cid AS uuid)
            """),
            {"cid": chunk_id},
        ).fetchone()
        if row is None:
            return None
        return {
            "display_text": row[0],
            "spoken_text": row[1],
            "formulas": _json_list(row[2]),
        }
    finally:
        session.close()


def _write_chunk_script(chunk_id: str, state: dict) -> bool:
    """chunk の原稿状態を復元し、音声キャッシュを無効化する（rewrite API と同じ後始末）。"""
    import json

    session = _pg_session()
    try:
        res = session.execute(
            sa_text("""
                UPDATE chunks
                SET display_text = :display_text,
                    spoken_text = :spoken_text,
                    formulas = CAST(:formulas AS jsonb)
                WHERE id = CAST(:cid AS uuid)
                RETURNING id
            """),
            {
                "cid": chunk_id,
                "display_text": state.get("display_text"),
                "spoken_text": state.get("spoken_text"),
                "formulas": json.dumps(_json_list(state.get("formulas")), ensure_ascii=False),
            },
        ).fetchone()
        # 原稿が変わるため既存音声は無効（rewrite_lecture_script と同じ invalidate）。
        session.execute(
            sa_text("DELETE FROM lecture_audio_cache WHERE chunk_id = CAST(:cid AS uuid)"),
            {"cid": chunk_id},
        )
        session.commit()
        return res is not None
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _call_rewrite_api(chunk_id: str, prompt: str, studio_view: str, user: dict) -> dict:
    """既存の rewrite エンドポイント関数を呼ぶだけの薄いラッパ（P7）。

    routes / schemas は FastAPI に依存するため**遅延 import** する（core/admin_assistant
    は module レベルで FastAPI 系を import しない、というガードレールを守る）。既存 API を
    呼ぶだけなので、原稿の書き換え・DB 保存・音声キャッシュ無効化・LLM 計測（U層）は
    すべて既存実装のまま流用される。
    """
    from routes.lecture_studio.scripts import rewrite_lecture_script  # noqa: PLC0415
    from schemas import LectureScriptRewriteRequest  # noqa: PLC0415

    body = LectureScriptRewriteRequest(prompt=prompt, studio_view=studio_view)
    res = rewrite_lecture_script(chunk_id, body, current_user=user)
    return {
        "display_text": res.display_text,
        "spoken_text": res.spoken_text,
        "formulas": [
            f.model_dump() if hasattr(f, "model_dump") else dict(f) for f in (res.formulas or [])
        ],
    }


class LectureRewriteChunkScriptAction(AssistantAction):
    """チャンク原稿の AI 書き換え（L2・永続可逆）。

    - apply: 既存 POST /chunks/{id}/lecture-script/rewrite の実装関数を呼ぶだけ（P7）。
      既存 API 自体が chunks を即時 UPDATE し音声キャッシュを無効化する。
    - revert: capture_before で取った display_text / spoken_text / formulas を復元し、
      復元でも原稿が変わるので音声キャッシュを同様に無効化する（P3: before で元に戻る）。
    - 権限: 既存 rewrite API と同一（_require_teacher 相当は route 側で担保済み）。
    """

    capability_id = "lecture_studio.rewrite_chunk_script"

    def capture_before(self, ctx: ActionContext) -> dict:
        if not ctx.target_id:
            raise ActionArgError("chunk_id が指定されていません")
        state = _fetch_chunk_script(ctx.target_id)
        if state is None:
            raise ActionTargetError("チャンクが見つかりません")
        return state

    def apply(self, ctx: ActionContext, before: dict) -> dict:
        prompt = str(ctx.args.get("prompt") or "").strip()
        if not prompt:
            raise ActionArgError("書き換えの指示（prompt）が指定されていません")
        studio_view = str(ctx.args.get("studio_view") or "edit").strip().lower() or "edit"
        user = {"id": ctx.user_id, "role": ctx.role}
        try:
            return _call_rewrite_api(ctx.target_id, prompt, studio_view, user)
        except (ActionError, ActionTargetError, ActionArgError):
            raise
        except Exception as exc:
            # 既存 API は FastAPI の HTTPException を送出しうるが、core/admin_assistant は
            # fastapi を import しない（ガードレール）ため duck-typing で写像する。
            status = getattr(exc, "status_code", None)
            detail = getattr(exc, "detail", None) or str(exc)
            if status == 404:
                raise ActionTargetError("チャンクが見つかりません") from exc
            raise ActionError(f"原稿の書き換えに失敗しました: {detail}") from exc

    def revert(self, ctx: ActionContext, before: dict) -> None:
        if not ctx.target_id:
            raise ActionArgError("chunk_id が指定されていません")
        ok = _write_chunk_script(ctx.target_id, before or {})
        if not ok:
            raise ActionTargetError("取り消し対象のチャンクが見つかりません")


_HANDLERS: dict[str, AssistantAction] = {
    CourseSetVisibilityAction.capability_id: CourseSetVisibilityAction(),
    CoursePublishAction.capability_id: CoursePublishAction(),
    LectureRewriteChunkScriptAction.capability_id: LectureRewriteChunkScriptAction(),
}


def get_handler(capability_id: str) -> Optional[AssistantAction]:
    return _HANDLERS.get(capability_id)


def supported_capability_ids() -> list[str]:
    return list(_HANDLERS.keys())
