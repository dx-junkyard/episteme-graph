"""L層ライブラリの検索 — apparatus_semantics retrieval / 昇格モーダルの類似提示。

- ``search_frozen_entries`` は **各 active エントリの凍結版のみ** を対象にする
  （draft は解析に使わない。原則8 / ガードレール#8）。retired は除外する。
  代表版は「embedding を持つ最新凍結版」（最新版の embedding が failed/欠落なら
  直近の有効な embedding を持つ版へ fallback する。N23）。
- 失敗時（embedding 不可・クエリ失敗）は例外を投げず空リストを返し、パイプラインを
  止めない。呼び出し側は「ライブラリが空・retrieval 0件」と同じ縮退として扱える。
- 画像埋め込みモデル（CLIP 等）は導入しない。retrieval はテキスト記述（name /
  aliases / summary / body）の embedding のみで行う（§13）。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text as sa_text

from core.postgres import get_session

from . import schema

logger = logging.getLogger(__name__)


def _embed_query(query_text: str, embed_fn: Any = None) -> list[float] | None:
    try:
        if embed_fn is not None:
            vector = embed_fn(query_text)
        else:
            from core.llm import generate_embeddings

            vectors = generate_embeddings([query_text])
            vector = vectors[0] if vectors else None
    except Exception:  # noqa: BLE001 — 検索は非致命
        logger.warning("library embedding query failed", exc_info=True)
        return None
    return vector or None


def _embedding_search(
    *,
    domain_key: str,
    query_text: str,
    entry_type: str | None,
    top_k: int,
    embed_fn: Any = None,
) -> list[dict]:
    """各 active エントリの「embedding を持つ最新凍結版」を対象にした pgvector cosine 類似検索。

    最新凍結版の embedding が failed/欠落（NULL）のエントリは、直近の有効な embedding を
    持つ凍結版をそのエントリの代表として対象に含める（N23 fallback: 凍結時の embedding
    失敗で retrieval から静かに脱落させない）。実装は row_number() の PARTITION を
    ``embedding IS NOT NULL`` の行だけで振ること（CTE 内 WHERE）による。全凍結版に
    embedding が無いエントリだけが対象外になる。返す ``version_no`` / ``content`` は
    実際に代表となった版のもの（referenced_library_versions の記録が事実と一致する）。
    """
    if not domain_key or not query_text:
        return []
    vector = _embed_query(query_text, embed_fn)
    if not vector:
        return []

    conditions = [
        "l.rn = 1",
        "e.domain_key = :domain_key",
        "e.status = 'active'",
    ]
    params: dict[str, Any] = {
        "qvec": str(vector),
        "domain_key": domain_key,
        "top_k": int(top_k),
    }
    if entry_type:
        conditions.append("e.entry_type = :entry_type")
        params["entry_type"] = entry_type
    where = " AND ".join(conditions)

    session = get_session()
    try:
        # name/aliases/summary/body は凍結スナップショット（versions.content）から復元する。
        # library_entries（draft）は domain/type/status フィルタにのみ使う — draft の本文を
        # 解析入力に混ぜない（原則8: パイプラインが参照するのは凍結版のみ）。
        # CTE 内の WHERE v.embedding IS NOT NULL が N23 fallback の実体:
        # rn=1 は「embedding を持つ版のうち最新」を指す（最新版が NULL でも直近の
        # 有効版がエントリの代表になる）。
        rows = session.execute(
            sa_text(
                f"""
                WITH latest AS (
                    SELECT v.entry_id, v.version_no, v.embedding, v.content,
                           row_number() OVER (
                               PARTITION BY v.entry_id ORDER BY v.version_no DESC
                           ) AS rn
                      FROM library_entry_versions v
                     WHERE v.embedding IS NOT NULL
                )
                SELECT l.entry_id::text, l.version_no, l.content,
                       (l.embedding <=> CAST(:qvec AS vector)) AS distance
                  FROM latest l
                  JOIN library_entries e ON e.id = l.entry_id
                 WHERE {where}
                 ORDER BY distance ASC
                 LIMIT :top_k
                """
            ),
            params,
        ).fetchall()
    except Exception:  # noqa: BLE001 — 検索は非致命（呼び出し側を止めない）
        logger.warning("library embedding search query failed for domain=%s", domain_key, exc_info=True)
        return []
    finally:
        session.close()

    results: list[dict] = []
    for row in rows:
        content = schema.as_dict(row[2])
        results.append(
            {
                "entry_id": row[0],
                "version_no": int(row[1] or 0),
                "name": str(content.get("name") or ""),
                "aliases": schema.as_list(content.get("aliases")),
                "summary": str(content.get("summary") or ""),
                "body": schema.as_dict(content.get("body")),
                "distance": float(row[3]) if row[3] is not None else None,
            }
        )
    return results


def search_frozen_entries(
    *,
    domain_key: str,
    query_text: str,
    entry_type: str = schema.ENTRY_TYPE_APPARATUS,
    top_k: int = 5,
    embed_fn: Any = None,
) -> list[dict]:
    """apparatus_semantics retrieval 用: 凍結版のみを対象にした埋め込み類似検索。

    ライブラリが空 / retrieval 0件でも例外にせず空リストを返す（agent は
    match_status='novel'/'unknown' に縮退できる、§5-3）。
    """
    return _embedding_search(
        domain_key=domain_key,
        query_text=query_text,
        entry_type=entry_type,
        top_k=top_k,
        embed_fn=embed_fn,
    )


def _ilike_search(
    *,
    domain_key: str,
    text: str,
    entry_type: str | None,
    top_k: int,
) -> list[dict]:
    """name/aliases の部分一致検索（draft も対象。凍結版が無い active エントリも拾う）。"""
    conditions = [
        "status = 'active'",
        "domain_key = :domain_key",
        "(name ILIKE :q OR aliases::text ILIKE :q)",
    ]
    params: dict[str, Any] = {"domain_key": domain_key, "q": f"%{text}%", "top_k": int(top_k)}
    if entry_type:
        conditions.append("entry_type = :entry_type")
        params["entry_type"] = entry_type
    where = " AND ".join(conditions)

    session = get_session()
    try:
        rows = session.execute(
            sa_text(
                f"""
                SELECT id::text, latest_version_no, name, aliases, summary, body
                  FROM library_entries
                 WHERE {where}
                 ORDER BY name
                 LIMIT :top_k
                """
            ),
            params,
        ).fetchall()
    except Exception:  # noqa: BLE001
        logger.warning("library ilike search failed for domain=%s", domain_key, exc_info=True)
        return []
    finally:
        session.close()

    return [
        {
            "entry_id": row[0],
            "version_no": int(row[1] or 0),
            "name": row[2] or "",
            "aliases": schema.as_list(row[3]),
            "summary": row[4] or "",
            "body": schema.as_dict(row[5]),
            "distance": None,
        }
        for row in rows
    ]


def find_similar_entries(
    *,
    domain_key: str,
    text: str,
    entry_type: str | None = None,
    top_k: int = 5,
) -> list[dict]:
    """昇格モーダルの類似既存エントリ提示（embedding 検索 + ILIKE 検索のマージ）。

    entry_id で重複排除し、embedding ヒットを優先する。凍結版が無い active エントリ
    （embedding 検索に出てこない）も ILIKE 側で拾える。
    """
    if not domain_key or not text:
        return []

    embedding_hits = _embedding_search(
        domain_key=domain_key, query_text=text, entry_type=entry_type, top_k=top_k
    )
    ilike_hits = _ilike_search(domain_key=domain_key, text=text, entry_type=entry_type, top_k=top_k)

    seen: set[str] = set()
    merged: list[dict] = []
    for hit in embedding_hits + ilike_hits:
        entry_id = hit["entry_id"]
        if entry_id in seen:
            continue
        seen.add(entry_id)
        merged.append(hit)
        if len(merged) >= top_k:
            break
    return merged
