"""購読条件・見送り記録の読み書き（migration 071）。

設計正本: ``docs/features/paper_discovery_design.md`` §4.1 / §4.2。

不変条項:

- **行削除の SQL を書かない**（P4 / PD5）。見送りの取り消しは行削除ではなく
  ``revoked`` 遷移で、履歴（誰がいつ見送ったか）を残す。ガードレール
  ``test_paper_discovery_guardrails.py`` がこのファイルの構造として固定する。
- **候補を保存しない**（PD5）。ここが持つのは購読条件と見送り記録だけで、
  arXiv から取れた候補一覧のスナップショットは作らない。
- FastAPI 非 import・``core.llm`` 非 import。
- ``commit`` / ``close`` は呼び出し側（API 層）の責務
  （``core/url_fetch.py`` の CRUD と同じ流儀）。

購読は分野単位1行の共同編集で、楽観ロックは持たない（設計書 §4.1 — 条件は小さく
last-write-wins の実害が軽微。衝突が観測されたら ``revision_store`` に接続する）。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from sqlalchemy import text as sa_text

from core.paper_discovery.schema import (
    normalize_arxiv_id,
    normalize_authors,
    normalize_categories,
    normalize_keyphrases,
)

logger = logging.getLogger(__name__)

_SUBSCRIPTION_COLUMNS = """
    domain_key,
    arxiv_categories,
    keyphrases,
    followed_authors,
    updated_by,
    updated_at,
    last_checked_at
"""


def _iso(value: Any) -> str:
    if value is None:
        return ""
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


def _as_list(value: Any) -> list:
    """JSONB 列の値をリストへ（ドライバが str を返す場合も吸収する）。"""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _row_to_subscription(row) -> dict:
    return {
        "domain_key": str(row[0] or ""),
        "arxiv_categories": normalize_categories(list(row[1] or [])),
        "keyphrases": normalize_keyphrases(_as_list(row[2])),
        "followed_authors": normalize_authors(_as_list(row[3])),
        "updated_by": str(row[4]) if row[4] else "",
        "updated_at": _iso(row[5]),
        "last_checked_at": _iso(row[6]),
    }


# ---------------------------------------------------------------------------
# 購読
# ---------------------------------------------------------------------------


def list_subscriptions(session) -> list[dict]:
    """購読を分野キー昇順で返す。"""
    rows = session.execute(
        sa_text(
            f"""
            SELECT {_SUBSCRIPTION_COLUMNS}
              FROM paper_discovery_subscriptions
             ORDER BY domain_key ASC
            """
        )
    ).fetchall()
    return [_row_to_subscription(row) for row in rows]


def get_subscription(session, domain_key: str) -> Optional[dict]:
    """1分野の購読を返す（未設定なら ``None``）。"""
    key = str(domain_key or "").strip()
    if not key:
        return None
    row = session.execute(
        sa_text(
            f"""
            SELECT {_SUBSCRIPTION_COLUMNS}
              FROM paper_discovery_subscriptions
             WHERE domain_key = :domain_key
             LIMIT 1
            """
        ),
        {"domain_key": key},
    ).fetchone()
    return _row_to_subscription(row) if row else None


def upsert_subscription(
    session,
    domain_key: str,
    *,
    arxiv_categories: Any = None,
    keyphrases: Any = None,
    followed_authors: Any = None,
    updated_by: Any = None,
) -> dict:
    """購読を作成・更新する（分野キーで upsert）。保存後の行を返す。

    キーフレーズは ``{"text", "source", "enabled"}`` へ正規化し、語彙外の
    ``source`` は ``"manual"`` へ落とす（:func:`~core.paper_discovery.schema.normalize_keyphrase`）。
    ``enabled=False`` のフレーズも**保存する**（外した状態を保持する — P4）。

    Raises:
        ValueError: ``domain_key`` が空。
    """
    key = str(domain_key or "").strip()
    if not key:
        raise ValueError("domain_key must not be empty")

    categories = normalize_categories(arxiv_categories)
    phrases = normalize_keyphrases(keyphrases)
    authors = normalize_authors(followed_authors)

    session.execute(
        sa_text(
            """
            INSERT INTO paper_discovery_subscriptions
                (domain_key, arxiv_categories, keyphrases, followed_authors,
                 updated_by, updated_at)
            VALUES
                (:domain_key,
                 CAST(:arxiv_categories AS text[]),
                 CAST(:keyphrases AS jsonb),
                 CAST(:followed_authors AS jsonb),
                 CAST(:updated_by AS uuid),
                 now())
            ON CONFLICT (domain_key) DO UPDATE
               SET arxiv_categories = EXCLUDED.arxiv_categories,
                   keyphrases       = EXCLUDED.keyphrases,
                   followed_authors = EXCLUDED.followed_authors,
                   updated_by       = EXCLUDED.updated_by,
                   updated_at       = now()
            """
        ),
        {
            "domain_key": key,
            "arxiv_categories": categories,
            "keyphrases": json.dumps(phrases, ensure_ascii=False),
            "followed_authors": json.dumps(authors, ensure_ascii=False),
            "updated_by": str(updated_by) if updated_by else None,
        },
    )

    stored = get_subscription(session, key)
    if stored is not None:
        return stored
    # フェイクセッション等で読み戻せない場合も、書いた内容を正直に返す。
    return {
        "domain_key": key,
        "arxiv_categories": categories,
        "keyphrases": phrases,
        "followed_authors": authors,
        "updated_by": str(updated_by) if updated_by else "",
        "updated_at": "",
        "last_checked_at": "",
    }


def touch_last_checked(session, domain_key: str) -> None:
    """検索を実行した事実（``last_checked_at``）だけを更新する。

    購読行が無い分野（条件を保存せず検索だけした場合）は 0 行更新で何もしない
    — 購読行を勝手に作らない（購読は教員の意思の正本、PD3）。
    """
    key = str(domain_key or "").strip()
    if not key:
        return
    session.execute(
        sa_text(
            """
            UPDATE paper_discovery_subscriptions
               SET last_checked_at = now()
             WHERE domain_key = :domain_key
            """
        ),
        {"domain_key": key},
    )


# ---------------------------------------------------------------------------
# 見送り（行削除しない — revoked 遷移で復帰する）
# ---------------------------------------------------------------------------


def _require_ids(domain_key: str, arxiv_id: str) -> tuple[str, str]:
    key = str(domain_key or "").strip()
    if not key:
        raise ValueError("domain_key must not be empty")
    normalized = normalize_arxiv_id(arxiv_id)
    if not normalized:
        raise ValueError(f"invalid arXiv id: {arxiv_id!r}")
    return (key, normalized)


def dismiss(session, domain_key: str, arxiv_id: str, user_id: Any = None) -> dict:
    """候補を見送る（既に見送り済みなら復帰済みでも再度 ``revoked=FALSE`` に戻す）。

    Raises:
        ValueError: 分野キーが空、または arXiv ID を正規化できない。
    """
    key, normalized = _require_ids(domain_key, arxiv_id)
    session.execute(
        sa_text(
            """
            INSERT INTO paper_discovery_dismissals
                (domain_key, arxiv_id, dismissed_by, dismissed_at, revoked)
            VALUES (:domain_key, :arxiv_id, CAST(:user_id AS uuid), now(), FALSE)
            ON CONFLICT (domain_key, arxiv_id) DO UPDATE
               SET revoked      = FALSE,
                   dismissed_by = EXCLUDED.dismissed_by,
                   dismissed_at = now()
            """
        ),
        {
            "domain_key": key,
            "arxiv_id": normalized,
            "user_id": str(user_id) if user_id else None,
        },
    )
    return {"domain_key": key, "arxiv_id": normalized, "revoked": False}


def restore(session, domain_key: str, arxiv_id: str, user_id: Any = None) -> Optional[dict]:
    """見送りを取り消す（``revoked=TRUE`` 遷移）。行が無ければ ``None``。

    行は削除しない（誰がいつ見送ったかの履歴を保持する — P4）。

    Raises:
        ValueError: 分野キーが空、または arXiv ID を正規化できない。
    """
    key, normalized = _require_ids(domain_key, arxiv_id)
    row = session.execute(
        sa_text(
            """
            UPDATE paper_discovery_dismissals
               SET revoked = TRUE
             WHERE domain_key = :domain_key
               AND arxiv_id = :arxiv_id
         RETURNING domain_key, arxiv_id
            """
        ),
        {"domain_key": key, "arxiv_id": normalized},
    ).fetchone()
    if not row:
        return None
    return {"domain_key": key, "arxiv_id": normalized, "revoked": True}


def dismissed_ids(session, domain_key: str) -> set[str]:
    """現在有効な（``revoked=FALSE``）見送り済み arXiv ID の集合。"""
    key = str(domain_key or "").strip()
    if not key:
        return set()
    rows = session.execute(
        sa_text(
            """
            SELECT arxiv_id
              FROM paper_discovery_dismissals
             WHERE domain_key = :domain_key
               AND revoked = FALSE
            """
        ),
        {"domain_key": key},
    ).fetchall()
    return {str(row[0]) for row in rows if row and row[0]}


def list_dismissals(session, domain_key: str) -> list[dict]:
    """見送り記録を（復帰済みも含めて）新しい順に返す。

    「見送り済み」フィルタと [戻す] 導線の材料。復帰済み行も返すのは、行を
    消していないことをそのまま見せるため（P4）。
    """
    key = str(domain_key or "").strip()
    if not key:
        return []
    rows = session.execute(
        sa_text(
            """
            SELECT arxiv_id, dismissed_by, dismissed_at, revoked
              FROM paper_discovery_dismissals
             WHERE domain_key = :domain_key
             ORDER BY dismissed_at DESC
            """
        ),
        {"domain_key": key},
    ).fetchall()
    return [
        {
            "domain_key": key,
            "arxiv_id": str(row[0] or ""),
            "dismissed_by": str(row[1]) if row[1] else "",
            "dismissed_at": _iso(row[2]),
            "revoked": bool(row[3]),
        }
        for row in rows
    ]
