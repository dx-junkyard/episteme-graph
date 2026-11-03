"""人に見せる名前の正本（IK-0848 / IK-1007・第 25 周 第 5 波・migration 094）。

``users.display_name`` はログインの照合列を兼ねている（``routes/auth.py`` の ``auth_login`` は
``WHERE display_name = :username``）。帰属・所有者名・査読者名・記帳者名としてこの列を出すと、
人の名前ではなく**ログイン ID を他の利用者へ開示する**。

オーナー判断（推奨どおり）: ログイン ID は見せない。人に見せる名前は本人が登録する
``users.profile_name`` だけで、空なら名前なしの事実文に戻す（ログイン ID へフォールバックしない）。

- :func:`display_label` — 1 行（Mapping / 属性）から表示名を返す。空・削除済みは ``None``。
- :func:`label_or_unregistered` — 表示名か「名前を登録していない利用者」（文の主語）。
- :func:`fetch_display_labels` — ``user_id -> 表示名 | None``（1 SQL・行が無い id は欠落）。
- :func:`profile_name_sql` — SELECT に埋める列式（``display_name`` を書かせないための部品）。
- :func:`normalize_profile_name` — 本人の登録値の正規化（制御文字除去・空白の畳み込み・200 字）。

FastAPI を import しない（開発ルール2）。SQL は ``users`` の 1 本だけで、セッションの寿命は呼び出し側。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from typing import Any

from core.label_vocab import ACCOUNT_NAME_UNREGISTERED, ACCOUNT_NAME_UNREGISTERED_ACTOR
from core.text_hygiene import strip_control_sequences

logger = logging.getLogger(__name__)

#: 人に見せる名前の上限（文字数）。
PROFILE_NAME_MAX_CHARS = 200

#: 名前の登録が無いことの事実文（欄・行）と、文の主語に置く事実語（再エクスポート）。
NAME_UNREGISTERED = ACCOUNT_NAME_UNREGISTERED
NAME_UNREGISTERED_ACTOR = ACCOUNT_NAME_UNREGISTERED_ACTOR

_WS_RE = re.compile(r"\s+")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_ALIAS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def normalize_profile_name(value: Any) -> str:
    """本人の登録値を正規化する（制御文字・改行を除き、空白を 1 つに畳み、200 字で切る）。"""
    text = strip_control_sequences(value if isinstance(value, str) else "")
    text = _WS_RE.sub(" ", text).strip()
    return text[:PROFILE_NAME_MAX_CHARS].strip()


def _field(row: Any, key: str) -> Any:
    if row is None:
        return None
    if isinstance(row, Mapping):
        return row.get(key)
    return getattr(row, key, None)


def display_label(user_row: Any) -> str | None:
    """人に見せる名前を返す。``profile_name`` が空・削除済みアカウントなら ``None``。

    ログイン ID（``display_name`` / ``username``）は**読まない** — フォールバックもしない。
    """
    if str(_field(user_row, "status") or "").strip() == "deleted":
        return None
    name = normalize_profile_name(_field(user_row, "profile_name"))
    return name or None


def label_or_unregistered(value: Any) -> str:
    """表示名（文字列）か、名前の登録が無いときの主語の事実語。"""
    name = normalize_profile_name(value)
    return name or NAME_UNREGISTERED_ACTOR


def profile_name_sql(alias: str = "u") -> str:
    """SELECT に埋める列式（空白だけの登録は空文字に倒す）。``alias`` は識別子のみ。"""
    if not _ALIAS_RE.match(alias or ""):
        raise ValueError(f"invalid alias: {alias!r}")
    return f"COALESCE(NULLIF(BTRIM({alias}.profile_name), ''), '')"


def fetch_display_labels(session: Any, user_ids: Iterable[Any]) -> dict[str, str | None]:
    """``user_id -> 表示名 | None`` を返す（UUID 形の id だけを 1 SQL で引く）。

    行が無い id はキーごと欠落。名前の登録が無い・削除済みは ``None``。DB 例外は空 dict
    （表示名が引けなくても呼び出し側の一覧は壊さない）。
    """
    ids = sorted({str(u).strip() for u in user_ids or () if _UUID_RE.match(str(u or "").strip())})
    if not ids:
        return {}
    try:
        rows = session.execute(
            _sa_text(
                "SELECT id::text, profile_name, COALESCE(status, '') "
                "FROM users WHERE id::text = ANY(:ids)"
            ),
            {"ids": ids},
        ).fetchall()
    except Exception:  # noqa: BLE001 — 表示名が引けなくても一覧は返す
        logger.warning("account_display: profile names unavailable (non-fatal)", exc_info=True)
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            logger.debug("account_display: rollback failed (non-fatal)", exc_info=True)
        return {}
    out: dict[str, str | None] = {}
    for row in rows or ():
        user_id = str(row[0] or "").strip()
        if not user_id:
            continue
        out[user_id] = display_label(
            {"profile_name": row[1], "status": row[2] if len(row) > 2 else ""}
        )
    return out


def _sa_text(sql: str):
    from sqlalchemy import text as sa_text  # 遅延 import（純関数のテストで sqlalchemy を要さない）

    return sa_text(sql)
