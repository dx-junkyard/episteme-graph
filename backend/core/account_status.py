"""アカウントライフサイクル管理（AL層）— アカウント状態の照合（TTL キャッシュ付き）。

正本ドキュメント: ``docs/features/account_lifecycle_management_design.md`` §4.2
（トークン検証時の照合とキャッシュ）。DDL は ``backend/db/068_account_lifecycle.sql``。

役割: ステートレス JWT のまま「停止（``status``）」と「世代失効（``token_generation``）」を
**サーバ側の事実**で判定できるようにする（AL3）。``api/dependencies.py::_get_current_user``
がリクエストごとに呼ぶため、プロセス内 TTL キャッシュ（既定 30 秒。
``core/llm_policy_store.py`` の 20 秒 TTL パターン踏襲）で DB 往復を抑える。
停止・パスワードリセットの API 実行時に :func:`invalidate` を呼ぶことで、同一プロセス内は
即時・多プロセス構成でも最大 TTL 秒で反映される。

設計方針:
- FastAPI 非 import（開発ルール2）。LLM も呼ばない。
- **DB 例外と「行が無い」を混同しない**（設計書 §4.2-1/§4.2-3）。行不在は
  :class:`AccountState` ``None``（= 401 の材料）、DB 例外は
  :class:`AccountStatusUnavailable`（= 呼び出し側で fail-open する材料）。
  この2つを同じ戻り値で表すと「DB 障害で全員ログアウト」か「偽造 sub が通る」の
  どちらかを踏むため、型で分ける。
- セッションは ``core.postgres.get_session()`` を自前で開閉する（呼び出し元は認証層の
  奥深くでセッションを渡せないため。``llm_policy_store`` の TTL キャッシュと同型）。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text as sa_text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 状態語彙（migration 068 の CHECK 制約と 1:1。設計書 §3.1）
# ---------------------------------------------------------------------------

ACCOUNT_STATUS_ACTIVE = "active"
ACCOUNT_STATUS_SUSPENDED = "suspended"
ACCOUNT_STATUS_PENDING_DELETION = "pending_deletion"
ACCOUNT_STATUS_DELETED = "deleted"

#: users.status の全語彙（CHECK 制約と同じ順序）。
ACCOUNT_STATUSES: tuple[str, ...] = (
    ACCOUNT_STATUS_ACTIVE,
    ACCOUNT_STATUS_SUSPENDED,
    ACCOUNT_STATUS_PENDING_DELETION,
    ACCOUNT_STATUS_DELETED,
)

# ---------------------------------------------------------------------------
# TTL キャッシュ / スロットル
# ---------------------------------------------------------------------------

#: user_id → (status, token_generation) のキャッシュ TTL（秒）。設計書 §4.2-2 の既定 30 秒。
CACHE_TTL_SECONDS = 30.0

#: ``last_seen_at`` の最小更新間隔（秒）。設計書 §4.2-4 の 5 分。
LAST_SEEN_THROTTLE_SECONDS = 300.0

_cache_lock = threading.Lock()
# key = user_id -> (expires_at, AccountState | None)
_cache: dict[str, tuple[float, "Optional[AccountState]"]] = {}

_last_seen_lock = threading.Lock()
# key = user_id -> monotonic 時刻（最後に UPDATE を投げた時点）
_last_seen_touched: dict[str, float] = {}


class AccountStatusUnavailable(RuntimeError):
    """アカウント状態を DB から取得できなかった（接続失敗等）。

    「行が無い」（= :func:`get_account_state` が None を返すケース）とは**別**の事象。
    呼び出し側はこの例外のときのみ fail-open してよい（設計書 §4.2-3）。
    """


@dataclass(frozen=True)
class AccountState:
    """``users`` の認証照合に必要な最小の状態。"""

    user_id: str
    status: str
    token_generation: int

    @property
    def is_active(self) -> bool:
        return self.status == ACCOUNT_STATUS_ACTIVE


# ---------------------------------------------------------------------------
# キャッシュ操作
# ---------------------------------------------------------------------------


def invalidate(user_id: str) -> None:
    """指定ユーザーのキャッシュ項目を破棄する。

    **停止・再開・パスワードリセット・削除予約/取消・墓標化の各経路から必ず呼ぶこと**
    （AL3。呼ばないと最大 TTL 秒だけ旧状態で通ってしまう）。commit の**後**に呼ぶのが
    正しい（commit 前に呼ぶと他スレッドが旧値を読んで再キャッシュする窓が残る —
    ``llm_policy_store.invalidate`` と同じ注意）。
    """
    key = str(user_id or "")
    with _cache_lock:
        _cache.pop(key, None)
    with _last_seen_lock:
        _last_seen_touched.pop(key, None)


def invalidate_all() -> None:
    """キャッシュを全消去する（テスト・一括操作用）。"""
    with _cache_lock:
        _cache.clear()
    with _last_seen_lock:
        _last_seen_touched.clear()


def _cache_get(user_id: str) -> tuple[bool, "Optional[AccountState]"]:
    with _cache_lock:
        entry = _cache.get(user_id)
        if entry is None:
            return False, None
        expires_at, value = entry
        if expires_at < time.monotonic():
            del _cache[user_id]
            return False, None
        return True, value


def _cache_set(user_id: str, value: "Optional[AccountState]") -> None:
    with _cache_lock:
        _cache[user_id] = (time.monotonic() + CACHE_TTL_SECONDS, value)


# ---------------------------------------------------------------------------
# 照合
# ---------------------------------------------------------------------------


def get_account_state(user_id: str) -> "Optional[AccountState]":
    """``users`` から ``status`` / ``token_generation`` を引く（TTL キャッシュ付き）。

    :returns: 行があれば :class:`AccountState`、**行が無ければ None**（None も
        キャッシュする — 偽造 sub の連打で毎回 SELECT が走らないようにする）。
    :raises AccountStatusUnavailable: DB 例外（接続失敗・テーブル未作成等）。
        呼び出し側はこのときだけ fail-open してよい（設計書 §4.2-3）。
    """
    key = str(user_id or "")
    if not key:
        return None

    hit, cached = _cache_get(key)
    if hit:
        return cached

    try:
        state = _fetch_account_state(key)
    except Exception as exc:  # noqa: BLE001 — DB 例外は「行不在」と区別して伝える
        logger.warning("account_status: lookup failed for user_id=%r: %s", key, exc)
        raise AccountStatusUnavailable(str(exc)) from exc

    _cache_set(key, state)
    return state


def _fetch_account_state(user_id: str) -> "Optional[AccountState]":
    from core.postgres import get_session  # 遅延 import（テスト時の DB 依存を最小化）

    session = get_session()
    try:
        row = session.execute(
            sa_text(
                """
                SELECT status, token_generation
                FROM users
                WHERE id = CAST(:user_id AS uuid)
                LIMIT 1
                """
            ),
            {"user_id": user_id},
        ).fetchone()
    finally:
        session.close()

    if row is None:
        return None

    status = str(row[0] or ACCOUNT_STATUS_ACTIVE)
    try:
        generation = int(row[1] or 0)
    except (TypeError, ValueError):
        generation = 0
    return AccountState(user_id=user_id, status=status, token_generation=generation)


# ---------------------------------------------------------------------------
# last_seen_at（スロットル更新。厳密さより書き込み量を優先する近似値）
# ---------------------------------------------------------------------------


def touch_last_seen(user_id: str) -> bool:
    """``users.last_seen_at`` を更新する（前回更新から 5 分以上経過時のみ）。

    設計書 §4.2-4: 認証照合で DB を引いた機会に相乗りする近似値の更新。
    **イベント化しない**（``auth_events`` に行を積まない — 書き込み増幅の防止）。
    スロットルはプロセス内辞書なので多プロセス構成では最大でプロセス数倍の書き込みに
    なりうるが、それでもリクエスト毎更新とは桁が違う。

    :returns: 実際に UPDATE を発行したら True。スロットルで見送った場合・失敗した場合は
        False（**例外は外に漏らさない** — 近似値の更新で認証を止めない）。
    """
    key = str(user_id or "")
    if not key:
        return False

    now = time.monotonic()
    with _last_seen_lock:
        previous = _last_seen_touched.get(key)
        if previous is not None and (now - previous) < LAST_SEEN_THROTTLE_SECONDS:
            return False
        # 失敗しても次のリクエストで即再試行しない（DB 断のときに毎回叩かない）
        _last_seen_touched[key] = now

    from core.postgres import get_session  # 遅延 import

    session = None
    try:
        session = get_session()
        session.execute(
            sa_text("UPDATE users SET last_seen_at = now() WHERE id = CAST(:user_id AS uuid)"),
            {"user_id": key},
        )
        session.commit()
        return True
    except Exception:  # noqa: BLE001 — last_seen の更新失敗で認証を止めない
        logger.warning("account_status: last_seen_at update failed for user_id=%r", key, exc_info=True)
        if session is not None:
            try:
                session.rollback()
            except Exception:  # noqa: BLE001
                pass
        return False
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass


__all__ = [
    "ACCOUNT_STATUSES",
    "ACCOUNT_STATUS_ACTIVE",
    "ACCOUNT_STATUS_DELETED",
    "ACCOUNT_STATUS_PENDING_DELETION",
    "ACCOUNT_STATUS_SUSPENDED",
    "CACHE_TTL_SECONDS",
    "LAST_SEEN_THROTTLE_SECONDS",
    "AccountState",
    "AccountStatusUnavailable",
    "get_account_state",
    "invalidate",
    "invalidate_all",
    "touch_last_seen",
]
