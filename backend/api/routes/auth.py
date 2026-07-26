"""Episteme Graph — 認証エンドポイント (/api/auth)。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text as sa_text

from dependencies import (
    _create_token,
    _get_current_user,
    _pg_role_to_app_role,
    _verify_password,
)
from schemas import LoginRequest, TokenResponse, UserOut
from core.postgres import get_session as _pg_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Auth"])


@router.post("/login", response_model=TokenResponse)
def auth_login(body: LoginRequest) -> TokenResponse:
    """ユーザー名とパスワードを検証し、JWT を返す。"""
    session = _pg_session()
    try:
        record = session.execute(
            sa_text("""
                SELECT id, email, password_hash, role
                FROM users WHERE display_name = :username LIMIT 1
            """),
            {"username": body.username},
        ).fetchone()
    finally:
        session.close()

    if not record or not _verify_password(body.password, record[2]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    role = _pg_role_to_app_role(record[3])
    return TokenResponse(
        access_token=_create_token(str(record[0]), body.username, record[1], role)
    )


@router.get("/me", response_model=UserOut)
def auth_me(current_user: dict = Depends(_get_current_user)) -> UserOut:
    """Bearer トークンからデコードした現在のユーザー情報を返す。"""
    return UserOut(**current_user)
