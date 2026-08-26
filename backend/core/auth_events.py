"""アカウントライフサイクル管理（AL層）— 認証イベント台帳 ``auth_events`` の書き込み正本。

正本ドキュメント: ``docs/features/account_lifecycle_management_design.md`` §3.2
（テーブル定義・event 語彙）/ §4.1（ログインの判定順序）/ §4.2（トークン照合）。
DDL は ``backend/db/068_account_lifecycle.sql``。

不変条項（設計書 §1）:
- **AL4 平文パスワード・ハッシュ値を記録しない。** payload はホワイトリストではなく
  「禁止キーの除去 + 値の文字列化」で多重防御する（:func:`sanitize_payload`）。
  呼び出し側が誤って credential を混ぜても DB に落ちない。
- **AL5 append-only。** 本モジュールに行の削除・更新関数を作らない（U6 / DO4 と同型。
  台帳への SQL は INSERT だけで、削除文・更新文をこのモジュールに書かない）。
- **AL8 情報を落とさない。** FK を張らないため、ユーザー行の墓標化後もイベントは残る。

設計方針:
- FastAPI 非 import（開発ルール2）。LLM も呼ばない。
- **記録の失敗で認証を止めない**（:func:`record_auth_event` は例外を外に漏らさず
  ``logger.warning`` に留める）。認証イベントはテレメトリであり、認証の可否を
  左右してはならない。
- セッション規約: ``session`` を渡された場合は**呼び出し側のトランザクションに同乗する**
  （commit / close は呼び出し側の責務）。``session=None`` のときだけ
  ``core.postgres.get_session()`` を自前で開閉し commit する。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Mapping, Optional

from sqlalchemy import text as sa_text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# event 語彙（正本。設計書 §3.2.1 の表と 1:1）
# ---------------------------------------------------------------------------

#: ``/api/auth/login`` 成功
AUTH_EVENT_LOGIN_SUCCESS = "login_success"
#: 同・失敗。**ユーザー不在とパスワード不一致を payload で区別しない**（列挙攻撃対策）
AUTH_EVENT_LOGIN_FAILED = "login_failed"
#: 停止中アカウントのログイン試行（**資格情報が一致した場合のみ**記録する）
AUTH_EVENT_LOGIN_REJECTED_SUSPENDED = "login_rejected_suspended"
#: 有効期限内トークンが status 照合で拒否された
AUTH_EVENT_TOKEN_REJECTED_SUSPENDED = "token_rejected_suspended"
#: token_generation 不一致（リセット後の旧トークン）／照合対象の行が引けない
AUTH_EVENT_TOKEN_REJECTED_STALE = "token_rejected_stale"
#: 管理者によるパスワードリセット実行（payload に対象 user_id。**パスワードは入れない**）
AUTH_EVENT_PASSWORD_RESET = "password_reset"

#: サーバ側ホワイトリスト（語彙外は :class:`ValueError`）。
AUTH_EVENTS: tuple[str, ...] = (
    AUTH_EVENT_LOGIN_SUCCESS,
    AUTH_EVENT_LOGIN_FAILED,
    AUTH_EVENT_LOGIN_REJECTED_SUSPENDED,
    AUTH_EVENT_TOKEN_REJECTED_SUSPENDED,
    AUTH_EVENT_TOKEN_REJECTED_STALE,
    AUTH_EVENT_PASSWORD_RESET,
)

# ---------------------------------------------------------------------------
# 上限（無制限の文字列を DB に流し込まない）
# ---------------------------------------------------------------------------

_MAX_USERNAME_ATTEMPTED = 200
_MAX_USER_AGENT = 500
_MAX_IP_ADDRESS = 100
_MAX_PAYLOAD_VALUE = 500

#: AL4: このいずれかを部分一致で含むキーは payload から除去する（大小無視）。
_FORBIDDEN_PAYLOAD_KEY_PARTS: tuple[str, ...] = (
    "password",
    "passwd",
    "hash",
    "secret",
    "token",
    "credential",
)


# ---------------------------------------------------------------------------
# payload のサニタイズ（AL4 の多重防御）
# ---------------------------------------------------------------------------


def sanitize_payload(payload: Optional[Mapping[str, Any]]) -> dict:
    """payload から credential 系キーを除去し、値を JSON 安全な形に丸める。

    - キー名に :data:`_FORBIDDEN_PAYLOAD_KEY_PARTS` のいずれかを含む項目は**落とす**
      （呼び出し側の事故を DB の手前で止める。AL4）。
    - 値は ``str`` / ``int`` / ``float`` / ``bool`` / ``None`` のみ受理し、それ以外は
      ``str()`` にしてから長さで切る（ネストした dict を丸ごと持ち込ませない）。
    """
    if not payload:
        return {}
    out: dict[str, Any] = {}
    for raw_key, value in payload.items():
        key = str(raw_key)
        lowered = key.lower()
        if any(part in lowered for part in _FORBIDDEN_PAYLOAD_KEY_PARTS):
            continue
        if value is None or isinstance(value, (int, float, bool)):
            out[key] = value
            continue
        out[key] = str(value)[:_MAX_PAYLOAD_VALUE]
    return out


# ---------------------------------------------------------------------------
# クライアント IP の解決（設計書 §3.2 の注記）
# ---------------------------------------------------------------------------


def client_ip_from_headers(headers: Optional[Mapping[str, Any]]) -> Optional[str]:
    """リクエストヘッダからクライアント IP を推定する。

    ``X-Real-IP`` を第一候補とし、無ければ ``X-Forwarded-For`` の**末尾要素**を採る。
    nginx の ``$proxy_add_x_forwarded_for`` はクライアント送信値の**後ろ**に実 IP を
    足すため、先頭要素は攻撃者が偽装できる（設計書 §3.2）。**先頭を使わないこと。**

    ``headers`` は大文字小文字を無視して引ける Mapping（``starlette.datastructures.Headers``）
    を想定するが、素の dict でも動くよう小文字キーでの再探索をフォールバックに持つ。
    """
    if not headers:
        return None

    def _get(name: str) -> Optional[str]:
        try:
            value = headers.get(name)
        except Exception:  # noqa: BLE001 — 変な Mapping 実装でも落とさない
            return None
        if value is None:
            try:
                value = headers.get(name.lower())
            except Exception:  # noqa: BLE001
                return None
        return str(value) if value is not None else None

    real_ip = (_get("X-Real-IP") or "").strip()
    if real_ip:
        return real_ip[:_MAX_IP_ADDRESS]

    forwarded = (_get("X-Forwarded-For") or "").strip()
    if forwarded:
        parts = [part.strip() for part in forwarded.split(",") if part.strip()]
        if parts:
            return parts[-1][:_MAX_IP_ADDRESS]
    return None


def user_agent_from_headers(headers: Optional[Mapping[str, Any]]) -> Optional[str]:
    """``User-Agent`` ヘッダを長さ上限付きで取り出す（無ければ None）。"""
    if not headers:
        return None
    try:
        value = headers.get("user-agent") or headers.get("User-Agent")
    except Exception:  # noqa: BLE001
        return None
    if not value:
        return None
    return str(value)[:_MAX_USER_AGENT]


# ---------------------------------------------------------------------------
# 記録（append-only。例外を外に漏らさない）
# ---------------------------------------------------------------------------

_INSERT_SQL = """
    INSERT INTO auth_events
        (user_id, username_attempted, event, ip_address, user_agent, payload)
    VALUES
        (CAST(:user_id AS uuid), :username_attempted, :event, :ip_address, :user_agent,
         CAST(:payload AS jsonb))
"""


def record_auth_event(
    session: Any = None,
    *,
    event: str,
    user_id: Optional[str] = None,
    username_attempted: str = "",
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    payload: Optional[Mapping[str, Any]] = None,
) -> bool:
    """``auth_events`` に1行追記する（best-effort）。成功したら True。

    :param session: 既存の SQLAlchemy ``Session``。渡された場合は**そのトランザクションに
        同乗する**（commit / close は呼び出し側の責務）。``None`` のときだけ自前で
        ``get_session()`` → commit → close する。
    :param event: :data:`AUTH_EVENTS` のいずれか。語彙外は :class:`ValueError`
        （プログラミングエラーなので握り潰さない — DB 例外とは扱いが違う）。

    **DB 例外は外に漏らさない**（設計書 §10 / AL8 の精神。記録の失敗で認証を止めない）。
    失敗時は ``logger.warning`` を出して False を返す。
    """
    if event not in AUTH_EVENTS:
        raise ValueError(f"unknown auth event: {event!r}")

    params = {
        "user_id": str(user_id) if user_id else None,
        "username_attempted": str(username_attempted or "")[:_MAX_USERNAME_ATTEMPTED],
        "event": event,
        "ip_address": (str(ip_address)[:_MAX_IP_ADDRESS] if ip_address else None),
        "user_agent": (str(user_agent)[:_MAX_USER_AGENT] if user_agent else None),
        "payload": json.dumps(sanitize_payload(payload), ensure_ascii=False),
    }

    if session is not None:
        try:
            session.execute(sa_text(_INSERT_SQL), params)
            return True
        except Exception:  # noqa: BLE001 — 記録失敗で認証を止めない
            logger.warning("auth_events: failed to record %r (shared session)", event, exc_info=True)
            return False

    from core.postgres import get_session  # 遅延 import（テスト時の DB 依存を最小化）

    own_session = None
    try:
        own_session = get_session()
        own_session.execute(sa_text(_INSERT_SQL), params)
        own_session.commit()
        return True
    except Exception:  # noqa: BLE001 — 記録失敗で認証を止めない
        logger.warning("auth_events: failed to record %r", event, exc_info=True)
        if own_session is not None:
            try:
                own_session.rollback()
            except Exception:  # noqa: BLE001
                pass
        return False
    finally:
        if own_session is not None:
            try:
                own_session.close()
            except Exception:  # noqa: BLE001
                pass


__all__ = [
    "AUTH_EVENTS",
    "AUTH_EVENT_LOGIN_FAILED",
    "AUTH_EVENT_LOGIN_REJECTED_SUSPENDED",
    "AUTH_EVENT_LOGIN_SUCCESS",
    "AUTH_EVENT_PASSWORD_RESET",
    "AUTH_EVENT_TOKEN_REJECTED_STALE",
    "AUTH_EVENT_TOKEN_REJECTED_SUSPENDED",
    "client_ip_from_headers",
    "record_auth_event",
    "sanitize_payload",
    "user_agent_from_headers",
]
