"""Episteme Graph — 認証・認可の依存関数。

JWT トークン生成・検証、ロールマッピング、パスワードハッシュ等のヘルパーを提供する。
"""

from __future__ import annotations

import datetime
import os

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_JWT_SECRET: str = os.environ.get("JWT_SECRET", "episteme-dev-secret-change-in-prod")
_JWT_ALGORITHM: str = "HS256"
_JWT_EXPIRE_HOURS: int = 24

# ---------------------------------------------------------------------------
# Role definitions
# ---------------------------------------------------------------------------

ROLE_STUDENT = "STUDENT"
ROLE_TEACHER = "TEACHER"
ROLE_SYSTEM_ADMIN = "SYSTEM_ADMIN"

# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
_bearer = HTTPBearer()

# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------


def _hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def _verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------


def _create_token(user_id: str, username: str, email: str, role: str = ROLE_STUDENT) -> str:
    expire = datetime.datetime.utcnow() + datetime.timedelta(hours=_JWT_EXPIRE_HOURS)
    payload = {"sub": user_id, "username": username, "email": email, "role": role, "exp": expire}
    return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)


def _get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    """Bearer トークンをデコードしてユーザー情報を返す。"""
    try:
        payload = jwt.decode(
            credentials.credentials, _JWT_SECRET, algorithms=[_JWT_ALGORITHM]
        )
        return {
            "id": payload["sub"],
            "username": payload["username"],
            "email": payload["email"],
            "role": payload.get("role", ROLE_STUDENT),
        }
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


# ---------------------------------------------------------------------------
# Role mapping
# ---------------------------------------------------------------------------


def _pg_role_to_app_role(pg_role: str) -> str:
    """PostgreSQL の role 値をアプリケーションの role 定数にマッピングする。"""
    mapping = {"learner": ROLE_STUDENT, "instructor": ROLE_TEACHER, "admin": ROLE_SYSTEM_ADMIN}
    return mapping.get(pg_role, ROLE_STUDENT)


def _app_role_to_pg_role(app_role: str) -> str:
    """アプリケーションの role 定数を PostgreSQL の role 値にマッピングする。"""
    mapping = {ROLE_STUDENT: "learner", ROLE_TEACHER: "instructor", ROLE_SYSTEM_ADMIN: "admin"}
    return mapping.get(app_role, "learner")


# ---------------------------------------------------------------------------
# Role-based dependencies
# ---------------------------------------------------------------------------


def _require_teacher(current_user: dict = Depends(_get_current_user)) -> dict:
    """TEACHER または SYSTEM_ADMIN ロールを要求する。"""
    if current_user["role"] not in (ROLE_TEACHER, ROLE_SYSTEM_ADMIN):
        raise HTTPException(status_code=403, detail="Teacher or admin role required")
    return current_user


def _require_system_admin(current_user: dict = Depends(_get_current_user)) -> dict:
    """SYSTEM_ADMIN ロールを要求する。"""
    if current_user["role"] != ROLE_SYSTEM_ADMIN:
        raise HTTPException(status_code=403, detail="System admin role required")
    return current_user
