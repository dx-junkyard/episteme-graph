"""Episteme Graph — 認証・認可の依存関数。

JWT トークン生成・検証、ロールマッピング、パスワードハッシュ等のヘルパーを提供する。

アカウントライフサイクル管理（AL層、``docs/features/account_lifecycle_management_design.md``
§4.2）以降、``_get_current_user`` は JWT のデコードだけでなく **サーバ側の
``users.status`` / ``users.token_generation`` との照合**を行う（AL3: 停止・パスワード
リセットを最大 TTL 秒で全 API に波及させる）。fail-open は **DB 例外のときだけ**で、
行不在・世代不一致・非 active はすべて 401。
"""

from __future__ import annotations

import datetime
import logging

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext

from core import account_status as _account_status
from core import auth_events as _auth_events
from core.config import get_settings as _get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

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


def _create_token(
    user_id: str, username: str, email: str, role: str = ROLE_STUDENT, gen: int = 0
) -> str:
    """JWT を発行する。

    ``gen`` は発行時点の ``users.token_generation``（AL3）。``gen`` クレームを持たない
    旧トークンは照合時に ``0`` とみなされるため、列の初期値 0 と一致して後方互換になる
    （パスワードリセット・停止で ++ した時点から旧トークンが失効する）。
    """
    expire = datetime.datetime.utcnow() + datetime.timedelta(hours=_JWT_EXPIRE_HOURS)
    payload = {
        "sub": user_id,
        "username": username,
        "email": email,
        "role": role,
        "gen": int(gen or 0),
        "exp": expire,
    }
    return jwt.encode(payload, _get_settings().jwt_secret, algorithm=_JWT_ALGORITHM)


def _reject_token(
    event: str,
    *,
    user_id: str | None,
    request: Request | None,
    detail: str,
) -> HTTPException:
    """トークン拒否イベントを best-effort で記録し、401 の例外オブジェクトを返す。"""
    headers = getattr(request, "headers", None) if request is not None else None
    try:
        _auth_events.record_auth_event(
            event=event,
            user_id=user_id,
            ip_address=_auth_events.client_ip_from_headers(headers),
            user_agent=_auth_events.user_agent_from_headers(headers),
        )
    except Exception:  # noqa: BLE001 — 記録の失敗で認証判定を変えない
        logger.warning("dependencies: failed to record %r", event, exc_info=True)
    return HTTPException(status_code=401, detail=detail)


def _get_current_user(
    request: Request = None,  # type: ignore[assignment]
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    """Bearer トークンをデコードし、サーバ側のアカウント状態と照合してユーザー情報を返す。

    照合の規則（設計書 §4.2 を厳密に）:

    1. ``status <> 'active'`` → 401 + ``token_rejected_suspended``
    2. ``gen``（欠落は 0）≠ ``users.token_generation`` → 401 + ``token_rejected_stale``
    3. **行が引けない（None）→ 401** + ``token_rejected_stale``
       （AL1 により行は消えないので、行不在は偽造 sub か移行漏れを意味する）
    4. **DB 例外のときだけ fail-open**（従来どおり payload だけで通す + warning ログ）
    5. 照合で DB を引いた成功パスで ``last_seen_at`` をスロットル更新する
    """
    try:
        payload = jwt.decode(
            credentials.credentials, _get_settings().jwt_secret, algorithms=[_JWT_ALGORITHM]
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = {
        "id": payload["sub"],
        "username": payload["username"],
        "email": payload["email"],
        "role": payload.get("role", ROLE_STUDENT),
    }

    try:
        state = _account_status.get_account_state(user["id"])
    except _account_status.AccountStatusUnavailable as exc:
        # DB 断では他の全エンドポイントも動かないため、認証層で二重障害を起こさない。
        logger.warning(
            "dependencies: account status unavailable (fail-open) for user_id=%r: %s",
            user["id"],
            exc,
        )
        return user

    if state is None:
        raise _reject_token(
            _auth_events.AUTH_EVENT_TOKEN_REJECTED_STALE,
            user_id=user["id"],
            request=request,
            detail="Invalid token",
        )

    if not state.is_active:
        raise _reject_token(
            _auth_events.AUTH_EVENT_TOKEN_REJECTED_SUSPENDED,
            user_id=user["id"],
            request=request,
            detail="Account is not active",
        )

    if int(payload.get("gen", 0) or 0) != state.token_generation:
        raise _reject_token(
            _auth_events.AUTH_EVENT_TOKEN_REJECTED_STALE,
            user_id=user["id"],
            request=request,
            detail="Token expired",
        )

    _account_status.touch_last_seen(user["id"])
    return user


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
