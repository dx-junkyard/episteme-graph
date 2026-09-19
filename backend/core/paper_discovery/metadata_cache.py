"""arXiv メタデータ（外部事実）のキャッシュ（migration 085）。

設計正本: ``docs/features/paper_radar_design.md`` §14「arXiv 呼び出しの上限」。
先例: :mod:`core.paper_discovery.reference_cache`（migration 077 / CC3）。

1行 = arXiv API が公開している論文1本のメタデータの写し（タイトル・要旨・
カテゴリ・著者・日付・URL）。キーは **version 抜きの正規化 arXiv ID**。

不変条項:

- **CC3 と同型の設計明示例外**。発見層は候補・レンズ判定を保存しない（PD5）が、
  外部 API が公開している**事実**の写しだけは保存する。教員の判断でも候補一覧の
  スナップショットでもない（``documents.source_url`` と同じ「事実の記帳」側）。
  保存するのは arXiv が返した値だけで、誰が何を見たか・何を選んだかは入らない。
- **行削除の SQL を書かない**（P4 / PD5）。更新は upsert のみ。
- **失敗を保存しない**。読めなかったことの記録はここに置かない（429 の抑制は
  ``arxiv_client`` のクールダウンの責務）。この表に在るのは「読めた事実」だけ。
- FastAPI 非 import・``core.llm`` 非 import。
- ``commit`` / ``close`` は呼び出し側の責務（``store.py`` / ``reference_cache.py``
  と同じ流儀）。

``store.py`` ではなく別ファイルに置いているのは、
``test_store_writes_only_subscriptions_and_dismissals`` が守っている「store が書くのは
購読と見送りだけ」という構造を広げないため（``reference_cache.py`` と同じ理由）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import text as sa_text

from core.paper_discovery.schema import (
    ArxivEntry,
    normalize_arxiv_id,
    normalize_authors,
    normalize_categories,
)

logger = logging.getLogger(__name__)

#: 1行を復元するのに必要な列（``SELECT`` と :func:`entry_from_row` が同じ順で使う）。
_COLUMNS = (
    "arxiv_id",
    "title",
    "summary",
    "categories",
    "primary_category",
    "authors",
    "published_at",
    "updated_at",
    "abs_url",
    "pdf_url",
)


def _as_str_list(value: Any) -> list[str]:
    """JSONB 列の値を文字列リストへ（ドライバが str を返す場合も吸収する）。"""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, (str, int, float)) and str(item)]


def parse_timestamp(value: Any) -> Optional[datetime]:
    """arXiv の日時文字列を ``datetime`` へ（解釈できなければ ``None``）。

    解釈に失敗しても**エントリ全体を落とさない**（日付はメタデータの一部であって、
    要旨・カテゴリを捨てる理由にならない）。
    """
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def format_timestamp(value: Any) -> str:
    """``datetime`` を arXiv の表記（``...Z``）へ戻す（``None`` は空文字）。

    読み出した行から :class:`ArxivEntry` を復元するときに使う。UTC は ``Z`` 表記に
    畳んで、ライブ取得時の文字列と同じ見た目に揃える。
    """
    if value is None:
        return ""
    if not isinstance(value, datetime):
        return str(value or "").strip()
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
        return value.replace(tzinfo=None).isoformat() + "Z"
    return value.isoformat() + "Z"


def entry_from_row(row: Any) -> Optional[ArxivEntry]:
    """1行（:data:`_COLUMNS` の並び）を :class:`ArxivEntry` へ復元する。

    ``version`` は復元しない（キーが version 抜きなので、版番号を持たせると
    「この版のメタデータ」と読めてしまう — 設計書 §4.1）。
    """
    if row is None:
        return None
    values = list(row)
    if len(values) < len(_COLUMNS):
        return None
    arxiv_id = str(values[0] or "").strip()
    if not arxiv_id:
        return None
    primary = str(values[4] or "").strip()
    return ArxivEntry(
        arxiv_id=arxiv_id,
        title=str(values[1] or ""),
        summary=str(values[2] or ""),
        categories=_as_str_list(values[3]),
        primary_category=primary or None,
        authors=_as_str_list(values[5]),
        published=format_timestamp(values[6]),
        updated=format_timestamp(values[7]),
        abs_url=str(values[8] or ""),
        pdf_url=str(values[9] or ""),
    )


def get_fresh(session, arxiv_ids: Iterable[str], *, ttl_days: int) -> dict[str, ArxivEntry]:
    """新鮮な（TTL 内の）メタデータを ``{正規化 arXiv ID: ArxivEntry}`` で返す。

    Args:
        session: SQLAlchemy セッション（読み取りのみ。commit しない）。
        arxiv_ids: 正規化前でも受ける（URL・版つき ID も可）。
        ttl_days: この日数以内に取得した行だけを新鮮とみなす。

    Returns:
        読めた分だけの辞書（不足分は呼び出し側が arXiv へ取りに行く read-through）。
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
            f"""
            SELECT {", ".join(_COLUMNS)}
              FROM paper_discovery_arxiv_metadata_cache
             WHERE arxiv_id = ANY(CAST(:arxiv_ids AS text[]))
               AND fetched_at >= now() - make_interval(days => :ttl_days)
            """
        ),
        {"arxiv_ids": keys, "ttl_days": days},
    ).fetchall()

    out: dict[str, ArxivEntry] = {}
    for row in rows or ():
        entry = entry_from_row(row)
        if entry is not None:
            out[entry.arxiv_id] = entry
    return out


def upsert_entries(session, entries: Iterable[ArxivEntry]) -> int:
    """引けたメタデータを記録する（``ON CONFLICT DO UPDATE``。行削除しない）。

    Args:
        session: SQLAlchemy セッション（``commit`` は呼び出し側）。
        entries: :class:`ArxivEntry` の並び。正規化できない ID の項目は黙って飛ばす
            （捏造したキーでキャッシュを汚さない）。

    Returns:
        書いた行数（空入力・全件不正なら 0。SQL は撃たない）。
    """
    payloads: list[dict] = []
    seen: set[str] = set()
    for entry in entries or ():
        arxiv_id = normalize_arxiv_id(getattr(entry, "arxiv_id", ""))
        if not arxiv_id or arxiv_id in seen:
            continue
        seen.add(arxiv_id)
        payloads.append(
            {
                "arxiv_id": arxiv_id,
                "title": str(getattr(entry, "title", "") or ""),
                "summary": str(getattr(entry, "summary", "") or ""),
                "categories": json.dumps(
                    normalize_categories(getattr(entry, "categories", []) or []),
                    ensure_ascii=False,
                ),
                "primary_category": str(getattr(entry, "primary_category", "") or ""),
                "authors": json.dumps(
                    normalize_authors(getattr(entry, "authors", []) or []),
                    ensure_ascii=False,
                ),
                "published_at": parse_timestamp(getattr(entry, "published", "")),
                "updated_at": parse_timestamp(getattr(entry, "updated", "")),
                "abs_url": str(getattr(entry, "abs_url", "") or ""),
                "pdf_url": str(getattr(entry, "pdf_url", "") or ""),
            }
        )

    if not payloads:
        return 0

    statement = sa_text(
        """
        INSERT INTO paper_discovery_arxiv_metadata_cache
            (arxiv_id, title, summary, categories, primary_category, authors,
             published_at, updated_at, abs_url, pdf_url, fetched_at)
        VALUES (:arxiv_id, :title, :summary, CAST(:categories AS jsonb),
                :primary_category, CAST(:authors AS jsonb),
                :published_at, :updated_at, :abs_url, :pdf_url, now())
        ON CONFLICT (arxiv_id) DO UPDATE
           SET title            = EXCLUDED.title,
               summary          = EXCLUDED.summary,
               categories       = EXCLUDED.categories,
               primary_category = EXCLUDED.primary_category,
               authors          = EXCLUDED.authors,
               published_at     = EXCLUDED.published_at,
               updated_at       = EXCLUDED.updated_at,
               abs_url          = EXCLUDED.abs_url,
               pdf_url          = EXCLUDED.pdf_url,
               fetched_at       = now()
        """
    )
    for params in payloads:
        session.execute(statement, params)
    return len(payloads)


# ---------------------------------------------------------------------------
# fail-soft ラッパ（呼び出し側 = radar / search / compare が使う唯一の入口）
# ---------------------------------------------------------------------------


def ttl_days() -> int:
    """写しを新鮮とみなす日数（設定が読めなければ 0 = 常に取り直す）。

    ``DISCOVERY_ARXIV_METADATA_TTL_DAYS``（既定30）。設定が読めないことで探索を
    止めない（読めなければキャッシュ無しとして振る舞う）。
    """
    try:
        from core.config import get_settings  # 遅延 import（core の純粋性を保つ）

        return max(0, int(get_settings().discovery_arxiv_metadata_ttl_days))
    except Exception:  # noqa: BLE001
        logger.debug("arXiv metadata cache ttl unavailable", exc_info=True)
        return 0


def read_fresh(session, arxiv_ids: Iterable[str]) -> dict[str, ArxivEntry]:
    """新鮮な写しを読む（読めなければ空 — fail-soft）。

    キャッシュが引けないことは探索の失敗ではない（呼び出し側はそのまま arXiv へ
    取りに行く read-through）。
    """
    ids = [i for i in (arxiv_ids or ()) if i]
    if not ids:
        return {}
    try:
        return get_fresh(session, ids, ttl_days=ttl_days())
    except Exception:  # noqa: BLE001
        logger.warning("arXiv metadata cache read failed", exc_info=True)
        return {}


def remember(session, entries: Iterable[ArxivEntry]) -> None:
    """引けたメタデータ（外部事実）を写しとして残す（fail-soft）。

    保存するのは arXiv が返した値だけで、教員が何を見たか・何を選んだか・どの候補が
    新着だったかは残さない（PD5 は候補の保存を禁じているが、外部事実の写しは CC3 と
    同型の明示例外 — ``docs/features/paper_radar_design.md`` §14）。**失敗は保存
    しない**（読めなかった記録を「読んだ結果」と取り違えないため。429 の抑制は
    ``arxiv_client`` のクールダウンの責務）。

    書き込みの確定（``commit``）は呼び出し側の責務。書けなかったときは事実をログに
    残したうえで、同じトランザクションの後続の読み取りを巻き添えにしないよう
    ``rollback`` を試みる（探索そのものは必ず成立させる）。
    """
    items = [e for e in (entries or ()) if getattr(e, "arxiv_id", "")]
    if not items:
        return
    try:
        upsert_entries(session, items)
    except Exception:  # noqa: BLE001 — キャッシュ書き込みで探索を落とさない
        logger.warning("arXiv metadata cache write failed", exc_info=True)
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            logger.debug("rollback after cache write failure failed", exc_info=True)
