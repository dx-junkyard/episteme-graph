"""Episteme Graph — 認証エンドポイント (/api/auth)。

アカウントライフサイクル管理（AL層、``docs/features/account_lifecycle_management_design.md``
§4.1）以降、ログインは以下の順序を厳守する（**順序が生命線**）:

1. 資格情報の検証（ユーザー不在 / ``password_hash IS NULL`` / 不一致 → すべて 401 固定文言 +
   ``auth_events(login_failed)``。不在と不一致を payload で区別しない）
2. **資格情報が一致した場合のみ** ``users.status`` を判定
   （``suspended`` / ``pending_deletion`` → 403 + ``login_rejected_suspended``、
   ``deleted`` → 401 = 存在を教えない）
3. 成功時に ``last_login_at`` 更新 + ``login_success`` 記録 + ``gen`` クレーム付き JWT 発行

status 判定を資格情報検証より先に置くと、第三者がユーザー名だけで「存在し、かつ停止中」を
判定できてしまう（列挙リーク）。**この順序を逆にしないこと。**
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text as sa_text

from dependencies import (
    _create_token,
    _get_current_user,
    _pg_role_to_app_role,
    _verify_password,
)
from schemas import LoginRequest, TokenResponse, UserOut
from core import account_status as _account_status
from core import auth_events as _auth_events
from core.postgres import get_session as _pg_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Auth"])

# 401 の固定文言（不在・NULL ハッシュ・不一致・墓標を区別しない — 列挙攻撃対策）
_INVALID_CREDENTIALS_DETAIL = "Invalid credentials"
# 403 の事実文（設計書 §4.1-2）
_SUSPENDED_DETAIL = "このアカウントは停止されています。管理者に連絡してください。"

# 資格情報が一致しても認証を拒否する状態
_LOGIN_BLOCKED_STATUSES = (
    _account_status.ACCOUNT_STATUS_SUSPENDED,
    _account_status.ACCOUNT_STATUS_PENDING_DELETION,
)


@router.post("/login", response_model=TokenResponse)
def auth_login(body: LoginRequest, request: Request = None) -> TokenResponse:  # type: ignore[assignment]
    """ユーザー名とパスワードを検証し、JWT を返す。"""
    headers = getattr(request, "headers", None) if request is not None else None
    ip_address = _auth_events.client_ip_from_headers(headers)
    user_agent = _auth_events.user_agent_from_headers(headers)

    session = _pg_session()
    try:
        record = session.execute(
            sa_text("""
                SELECT id, email, password_hash, role, status, token_generation
                FROM users WHERE display_name = :username LIMIT 1
            """),
            {"username": body.username},
        ).fetchone()
    finally:
        session.close()

    # --- 1. 資格情報の検証（status より先。§4.1-1）-----------------------------
    # password_hash が NULL の行は _verify_password を呼ばずに不一致として扱う
    # （NULL を渡すと TypeError → 500 になる潜在バグの是正。墓標行にも効く）。
    stored_hash = record[2] if record else None
    credentials_ok = bool(record) and bool(stored_hash) and _verify_password(body.password, stored_hash)

    if not credentials_ok:
        _auth_events.record_auth_event(
            event=_auth_events.AUTH_EVENT_LOGIN_FAILED,
            user_id=str(record[0]) if record else None,
            username_attempted=body.username,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        raise HTTPException(status_code=401, detail=_INVALID_CREDENTIALS_DETAIL)

    user_id = str(record[0])
    status = str(record[4] or _account_status.ACCOUNT_STATUS_ACTIVE)

    # --- 2. 資格情報が一致した場合のみ status 判定（§4.1-2）--------------------
    if status in _LOGIN_BLOCKED_STATUSES:
        _auth_events.record_auth_event(
            event=_auth_events.AUTH_EVENT_LOGIN_REJECTED_SUSPENDED,
            user_id=user_id,
            username_attempted=body.username,
            ip_address=ip_address,
            user_agent=user_agent,
            payload={"status": status},
        )
        raise HTTPException(status_code=403, detail=_SUSPENDED_DETAIL)

    if status == _account_status.ACCOUNT_STATUS_DELETED:
        # 墓標行。存在を教えないため 401 固定文言 + login_failed に落とす。
        _auth_events.record_auth_event(
            event=_auth_events.AUTH_EVENT_LOGIN_FAILED,
            user_id=user_id,
            username_attempted=body.username,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        raise HTTPException(status_code=401, detail=_INVALID_CREDENTIALS_DETAIL)

    # --- 3. 成功（§4.1-3/4）--------------------------------------------------
    try:
        generation = int(record[5] or 0)
    except (TypeError, ValueError):
        generation = 0

    session = _pg_session()
    try:
        session.execute(
            sa_text("UPDATE users SET last_login_at = now() WHERE id = CAST(:user_id AS uuid)"),
            {"user_id": user_id},
        )
        _auth_events.record_auth_event(
            session,
            event=_auth_events.AUTH_EVENT_LOGIN_SUCCESS,
            user_id=user_id,
            username_attempted=body.username,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        session.commit()
    except Exception:  # noqa: BLE001 — 記録・最終ログイン更新の失敗でログインを止めない
        logger.warning("auth_login: failed to record login for user_id=%r", user_id, exc_info=True)
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass
    finally:
        session.close()

    role = _pg_role_to_app_role(record[3])
    return TokenResponse(
        access_token=_create_token(user_id, body.username, record[1], role, gen=generation)
    )


@router.get("/me", response_model=UserOut)
def auth_me(current_user: dict = Depends(_get_current_user)) -> UserOut:
    """Bearer トークンからデコードした現在のユーザー情報を返す。"""
    return UserOut(**current_user)
