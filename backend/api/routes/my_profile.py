"""本人の「人に見せる名前」（IK-0848 / IK-1007・第 25 周 第 5 波・migration 094）。

``users.display_name`` はログインの照合列を兼ねるので、帰属・所有者名の表示には使わない。
人に見せる名前は本人がここで登録する ``users.profile_name`` だけで、空なら共有先の画面は
名前なしの事実文に戻る（``core/account_display.py`` が読みの正本）。

- ``GET /api/me/profile`` — 本人の登録値と、共有先にどう見えるかの事実文。
- ``PATCH /api/me/profile`` — ``{profile_name}``（200 字・制御文字除去・空で消す）。

不変条項:

- **本人のみ**: ユーザー ID のパスパラメータを作らない（対象は常に ``current_user["id"]``）。
- **ログインに触れない**: ``display_name``（ログイン ID）・パスワード・トークン世代を読まず書かない。
  ログインの判定順序（資格情報 → status）は ``routes/auth.py`` のまま不変。
- **行を消さない（AL1）**: 列の UPDATE だけ。
- 監査は ``AUDIT_ENTITY_USER_ACCOUNT``（名前の本文は載せず、登録の有無だけ）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text as sa_text

from core import account_display
from core.label_vocab import (
    ACCOUNT_NAME_UNREGISTERED,
    ACCOUNT_PROFILE_NAME_CLEARED,
    ACCOUNT_PROFILE_NAME_SAVED,
)
from core.postgres import get_session as _pg_session
from core.schema import AUDIT_ENTITY_USER_ACCOUNT
from core.text_hygiene import strip_control_sequences
from dependencies import _get_current_user
from display_route import LearnerDisplayRoute  # DP1: /api/me 配下は表示投影を通る

logger = logging.getLogger(__name__)

me_router = APIRouter(prefix="/api/me", tags=["Account"], route_class=LearnerDisplayRoute)

#: 名前の欄の説明（共有先に何が出るか・ログイン ID は出ないこと）。
PROFILE_NAME_NOTE = (
    "教材やコースを共有した相手・判断の記録には、この名前が出ます。"
    "ログインに使うユーザー名は、ほかの利用者には見せません。"
)


class ProfileOut(BaseModel):
    profile_name: str = ""
    #: 共有先に出る名前（登録が無ければ「名前の登録がありません」）。
    label: str = ACCOUNT_NAME_UNREGISTERED
    note: str = PROFILE_NAME_NOTE
    #: 保存直後の事実文（GET では空）。
    message: str = ""


class ProfileUpdate(BaseModel):
    profile_name: str = ""


def _profile_out(name: str, message: str = "") -> ProfileOut:
    return ProfileOut(
        profile_name=name,
        label=name or ACCOUNT_NAME_UNREGISTERED,
        message=message,
    )


@me_router.get("/profile", response_model=ProfileOut)
def get_my_profile(current_user: dict = Depends(_get_current_user)) -> ProfileOut:
    """本人の人に見せる名前（未登録なら空と事実文）。"""
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT profile_name, COALESCE(status, '') FROM users WHERE id = CAST(:uid AS uuid)"),
            {"uid": current_user["id"]},
        ).fetchone()
    finally:
        session.close()
    name = account_display.display_label(
        {"profile_name": row[0], "status": row[1]} if row else None
    ) or ""
    return _profile_out(name)


@me_router.patch("/profile", response_model=ProfileOut)
def update_my_profile(
    body: ProfileUpdate,
    current_user: dict = Depends(_get_current_user),
) -> ProfileOut:
    """本人の人に見せる名前を登録・変更・消去する（空文字で消す）。"""
    raw = body.profile_name if isinstance(body.profile_name, str) else ""
    # 長さは制御文字を除き空白を畳んだ後で測る（切り詰めて黙って保存しない）。
    if len(" ".join(strip_control_sequences(raw).split())) > account_display.PROFILE_NAME_MAX_CHARS:
        raise HTTPException(
            status_code=422,
            detail=f"名前は{account_display.PROFILE_NAME_MAX_CHARS}字以内で入力してください。",
        )
    name = account_display.normalize_profile_name(raw)
    session = _pg_session()
    try:
        result = session.execute(
            sa_text(
                "UPDATE users SET profile_name = :name "
                "WHERE id = CAST(:uid AS uuid) AND COALESCE(status, 'active') <> 'deleted'"
            ),
            {"name": name or None, "uid": current_user["id"]},
        )
        if getattr(result, "rowcount", 1) == 0:
            session.rollback()
            raise HTTPException(status_code=404, detail="アカウントが見つかりません。")
        session.commit()
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        session.rollback()
        logger.warning("my_profile: update failed", exc_info=True)
        raise HTTPException(status_code=500, detail="名前を保存できませんでした。")
    finally:
        session.close()
    try:
        from services import record_review_event

        record_review_event(
            AUDIT_ENTITY_USER_ACCOUNT,
            str(current_user["id"]),
            "",
            "profile_name_set" if name else "profile_name_cleared",
            str(current_user["id"]),
            {"action": "profile_name_update", "has_name": bool(name)},
        )
    except Exception:  # noqa: BLE001 — 監査の失敗で本人の保存を止めない
        logger.debug("my_profile: audit failed (non-fatal)", exc_info=True)
    return _profile_out(name, ACCOUNT_PROFILE_NAME_SAVED if name else ACCOUNT_PROFILE_NAME_CLEARED)
