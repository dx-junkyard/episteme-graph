"""参照リスト（外部事実）のキャッシュ（migration 077 / レンズC「基盤論文」）。

設計正本: ``docs/features/corpus_complement_design.md`` §5.3 / §5.4 / §6.3。

1行 = 取り込み済み arXiv 論文1本が**引用している**論文の一覧
（Semantic Scholar の graph API が公開しているメタデータの写し）。

不変条項:

- **CC3 の設計明示例外**。本層は候補・レンズ判定を保存しない（PD5 継承）が、
  参照リストという**外部事実**だけは保存する。教員の判断でも候補一覧の
  スナップショットでもない（``documents.source_url`` と同じ「事実の記帳」側）。
- **行削除の SQL を書かない**（P4 / PD5）。更新は upsert のみ。
- 取得失敗は ``fetch_status='failed'`` + 空配列で記録し、TTL 内の再取得を抑える
  （外部 API の行儀 — PD7）。ただし :func:`get_fresh` は ``'ok'`` の行だけを
  「読めた」扱いにするので、失敗シードの参照が候補に混ざることはない。
- FastAPI 非 import・``core.llm`` 非 import。
- ``commit`` / ``close`` は呼び出し側（API 層）の責務（``store.py`` と同じ流儀）。

``store.py`` ではなく別ファイルに置いているのは、
``test_store_writes_only_subscriptions_and_dismissals`` が守っている「store が書くのは
購読と見送りだけ」という構造を広げないため（設計書 §5.3）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text as sa_text

from core.paper_discovery.schema import normalize_arxiv_id

logger = logging.getLogger(__name__)

#: :func:`upsert` が受ける取得状態の語彙（migration 077 の CHECK と一対一）。
FETCH_STATUS_OK = "ok"
FETCH_STATUS_FAILED = "failed"
FETCH_STATUSES = (FETCH_STATUS_OK, FETCH_STATUS_FAILED)


def _as_list(value: Any) -> list:
    """JSONB 列の値をリストへ（ドライバが str を返す場合も吸収する）。"""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def get_fresh(session, arxiv_ids: list[str], *, ttl_days: int) -> dict[str, list[dict]]:
    """新鮮な（TTL 内・``fetch_status='ok'``）参照リストを ``{arxiv_id: 参照}`` で返す。

    Args:
        session: SQLAlchemy セッション（読み取りのみ。commit しない）。
        arxiv_ids: 引用している側（シード）の arXiv ID の列。正規化前でも受ける。
        ttl_days: この日数以内に取得した行だけを新鮮とみなす。

    Returns:
        読めたシードだけの辞書。**``fetch_status='failed'`` の行は返さない** —
        失敗の記録は「TTL 内は再取得しない」ためのもので、「参照が空だった」の
        意味ではないため。結果として、失敗シードは呼び出し側から見て
        「まだ読めていない」ままである（次の TTL 切れで再挑戦する）。
        空入力は SQL を撃たずに空辞書（空 IN 句を全件条件に化けさせない）。
    """
    keys = [k for k in dict.fromkeys(normalize_arxiv_id(a) or "" for a in arxiv_ids or ()) if k]
    if not keys:
        return {}
    try:
        days = max(0, int(ttl_days))
    except (TypeError, ValueError):
        days = 0

    rows = session.execute(
        sa_text(
            """
            SELECT arxiv_id, reference_entries
              FROM paper_discovery_reference_cache
             WHERE arxiv_id = ANY(CAST(:arxiv_ids AS text[]))
               AND fetch_status = 'ok'
               AND fetched_at >= now() - make_interval(days => :ttl_days)
            """
        ),
        {"arxiv_ids": keys, "ttl_days": days},
    ).fetchall()

    out: dict[str, list[dict]] = {}
    for row in rows:
        arxiv_id = str(row[0] or "")
        if arxiv_id:
            out[arxiv_id] = _as_list(row[1])
    return out


def fresh_failed_ids(session, arxiv_ids: list[str], *, ttl_days: int) -> set[str]:
    """TTL 内に**取得を試みて失敗した**シードの arXiv ID 集合。

    :func:`get_fresh` が ``'ok'`` の行しか返さないため、「まだ読めていない」と
    「TTL 内に失敗を記録済み」を区別するのに使う。後者は今回も取りに行かない
    （外部 API を叩き続けない — PD7）。空入力は SQL を撃たずに空集合。
    """
    keys = [k for k in dict.fromkeys(normalize_arxiv_id(a) or "" for a in arxiv_ids or ()) if k]
    if not keys:
        return set()
    try:
        days = max(0, int(ttl_days))
    except (TypeError, ValueError):
        days = 0

    rows = session.execute(
        sa_text(
            """
            SELECT arxiv_id
              FROM paper_discovery_reference_cache
             WHERE arxiv_id = ANY(CAST(:arxiv_ids AS text[]))
               AND fetch_status = 'failed'
               AND fetched_at >= now() - make_interval(days => :ttl_days)
            """
        ),
        {"arxiv_ids": keys, "ttl_days": days},
    ).fetchall()
    return {str(row[0]) for row in rows if row and row[0]}


def upsert(
    session,
    arxiv_id: str,
    references: list[dict],
    *,
    fetch_status: str = FETCH_STATUS_OK,
) -> None:
    """1シード分の参照リストを記録する（``ON CONFLICT DO UPDATE``。行削除しない）。

    Args:
        session: SQLAlchemy セッション（``commit`` は呼び出し側）。
        arxiv_id: 引用している側（シード）の arXiv ID。
        references: :meth:`CitationEntry.to_dict` の列。失敗時は空リスト。
        fetch_status: ``'ok'`` / ``'failed'``。未知の値は ``'failed'`` に寄せる
            （語彙の fail-closed。捏造した 'ok' でキャッシュを汚さない）。

    Raises:
        ValueError: arXiv ID を正規化できない。
    """
    key = normalize_arxiv_id(arxiv_id)
    if not key:
        raise ValueError(f"invalid arXiv id: {arxiv_id!r}")

    status = fetch_status if fetch_status in FETCH_STATUSES else FETCH_STATUS_FAILED
    entries = [item for item in (references or ()) if isinstance(item, dict)]
    if status != FETCH_STATUS_OK:
        entries = []

    session.execute(
        sa_text(
            """
            INSERT INTO paper_discovery_reference_cache
                (arxiv_id, reference_entries, fetch_status, fetched_at)
            VALUES (:arxiv_id, CAST(:reference_entries AS jsonb), :fetch_status, now())
            ON CONFLICT (arxiv_id) DO UPDATE
               SET reference_entries = EXCLUDED.reference_entries,
                   fetch_status      = EXCLUDED.fetch_status,
                   fetched_at        = now()
            """
        ),
        {
            "arxiv_id": key,
            "reference_entries": json.dumps(entries, ensure_ascii=False),
            "fetch_status": status,
        },
    )
