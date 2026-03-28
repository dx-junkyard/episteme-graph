"""Episteme Graph — 認証エンドポイント (/api/auth)。"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text as sa_text

from dependencies import (
    _create_token,
    _get_current_user,
    _hash_password,
    _pg_role_to_app_role,
    _verify_password,
    ROLE_STUDENT,
)
from schemas import LoginRequest, RegisterRequest, TokenResponse, UserOut
from core.postgres import get_session as _pg_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Auth"])


@router.post("/register", response_model=TokenResponse, status_code=201)
def auth_register(body: RegisterRequest) -> TokenResponse:
    """新規ユーザーを PostgreSQL に登録し、JWT を返す。デフォルトは learner ロール。"""
    session = _pg_session()
    try:
        existing = session.execute(
            sa_text("SELECT id FROM users WHERE display_name = :username LIMIT 1"),
            {"username": body.username},
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Username already taken")

        user_id = uuid.uuid4()
        hashed_pw = _hash_password(body.password)
        session.execute(
            sa_text("""
                INSERT INTO users (id, email, display_name, role, password_hash)
                VALUES (:id, :email, :username, 'learner', :pw)
            """),
            {"id": user_id, "email": body.email, "username": body.username, "pw": hashed_pw},
        )
        session.commit()
    finally:
        session.close()

    logger.info("Registered new user '%s' (id=%s, role=learner)", body.username, user_id)
    return TokenResponse(access_token=_create_token(str(user_id), body.username, body.email, ROLE_STUDENT))


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
