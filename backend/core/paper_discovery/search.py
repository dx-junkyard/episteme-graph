"""購読条件 → arXiv 検索 → 候補への注釈（読み時導出）。

設計正本: ``docs/features/paper_discovery_design.md`` §4.2 / §3 の4層のじょうご。

このモジュールの責務:

1. 購読条件（カテゴリ / キーフレーズ / 著者）から arXiv の ``search_query`` を
   決定論的に組み立てる。
2. arXiv API の結果に「取り込み済み / 見送り済み / 新規」の状態と「なぜ候補か」
   （一致したキーフレーズ）を**読み時導出で**注釈する。候補は保存しない（PD5）。

不変条項:

- **候補テーブルを持たない**（PD5）。取り込み済み判定は ``documents.source_url``
  から、見送り判定は ``paper_discovery_dismissals`` から毎回導出する。
- **数値スコア・類似度を DTO に入れない**（PD4）。「なぜ候補か」は一致した
  概念名の列挙であって点数ではない。並び順は arXiv の新着順そのもの。
- **閉世界の正直さ**（PD6）: 返す DTO には検索条件（``query``）と
  :data:`CLOSED_WORLD_NOTE` を必ず同梱し、「この分野の論文は他にない」と
  読める形にしない。条件が空なら API を呼ばず、空一覧を「該当なし」と偽らない。
- FastAPI 非 import・``core.llm`` 非 import。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import text as sa_text

from core.paper_discovery import arxiv_client, store
from core.paper_discovery.schema import (
    normalize_arxiv_id,
    normalize_authors,
    normalize_categories,
    normalize_keyphrases,
)

logger = logging.getLogger(__name__)

#: 候補一覧に必ず添える事実文（PD6）。一覧の上に常時表示する。
CLOSED_WORLD_NOTE = "この一覧は検索条件に一致した範囲のみを示します。"

#: 1回の検索で取得する既定件数。
DEFAULT_MAX_RESULTS = 50


# ---------------------------------------------------------------------------
# クエリ組み立て
# ---------------------------------------------------------------------------


def _group(terms: list[str]) -> str:
    """OR 結合した項を括弧でくくる（空なら空文字）。"""
    cleaned = [t for t in terms if t]
    if not cleaned:
        return ""
    return "(" + " OR ".join(cleaned) + ")"


def build_search_query(
    categories: Any = None,
    keyphrases: Any = None,
    followed_authors: Any = None,
) -> str:
    """購読条件から arXiv の ``search_query`` 文字列を組み立てる。

    - カテゴリ: ``(cat:astro-ph.CO OR cat:astro-ph.GA)``（第1層 = 網 / 再現率）
    - キーフレーズ: ``(all:"dark energy" OR all:"w0waCDM")``（第2層 = 絞り / 精度）。
      ``enabled=False`` のフレーズは**使わない**（外した状態は保存されているが
      検索には効かせない）。
    - 著者: ``(au:"Doe, J")``（補助シグナル。土台にはしない — 設計書 §3）

    非空のグループ同士は ``AND`` で結合する。条件が1つも無ければ空文字を返す
    （呼び出し側は API を呼ばない — PD6）。

    キーフレーズは文字列でも ``{"text", "enabled"}`` の辞書でも受ける。
    """
    category_terms = [f"cat:{c}" for c in normalize_categories(categories)]
    phrase_terms = [
        'all:"{}"'.format(p["text"].replace('"', ""))
        for p in normalize_keyphrases(keyphrases)
        if p.get("enabled", True)
    ]
    author_terms = [
        'au:"{}"'.format(a.replace('"', "")) for a in normalize_authors(followed_authors)
    ]

    groups = [
        _group(category_terms),
        _group(phrase_terms),
        _group(author_terms),
    ]
    return " AND ".join(g for g in groups if g)


# ---------------------------------------------------------------------------
# 取り込み済み判定（documents.source_url からの読み時導出）
# ---------------------------------------------------------------------------


def ingested_arxiv_ids(session) -> set[str]:
    """``documents.source_url`` から導出した「取り込み済み」arXiv ID の集合。

    URL 経由で取り込まれた document のみ判定できる（手動アップロードされた同一
    論文は判定できない — 設計書 §8 の非スコープ。UI は事実として注記する、PD6）。
    """
    rows = session.execute(
        sa_text(
            """
            SELECT source_url
              FROM documents
             WHERE source_url IS NOT NULL
               AND source_url <> ''
            """
        )
    ).fetchall()
    out: set[str] = set()
    for row in rows:
        arxiv_id = normalize_arxiv_id(row[0])
        if arxiv_id:
            out.add(arxiv_id)
    return out


def matched_keyphrases(entry_payload: dict, keyphrases: list[dict]) -> list[str]:
    """候補のタイトル + 要旨に現れた（enabled な）キーフレーズを返す。

    「なぜ候補か」を1行で示すための材料（ブラックボックスのおすすめにしない）。
    大文字小文字を無視した部分一致で、**一致度の数値は返さない**（PD4）。
    """
    haystack = " ".join(
        [
            str(entry_payload.get("title") or ""),
            str(entry_payload.get("summary") or ""),
        ]
    ).casefold()
    if not haystack.strip():
        return []
    hits: list[str] = []
    for phrase in keyphrases or ():
        if not phrase.get("enabled", True):
            continue
        text = str(phrase.get("text") or "").strip()
        if text and text.casefold() in haystack:
            hits.append(text)
    return hits


# ---------------------------------------------------------------------------
# 検索の実行
# ---------------------------------------------------------------------------


def run_search(
    session,
    domain_key: str,
    *,
    categories: Any = None,
    keyphrases: Any = None,
    followed_authors: Any = None,
    start: int = 0,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> dict:
    """購読条件で arXiv を検索し、注釈付きの候補一覧を返す。

    ``categories`` / ``keyphrases`` / ``followed_authors`` を渡した場合は保存済みの
    購読条件より優先する（保存せずに条件を試せる — 購読の書き換えは教員の明示
    保存だけ、PD3）。副作用は ``last_checked_at`` の更新のみで、候補は保存しない（PD5）。

    Returns:
        ``{"domain_key", "query", "total", "start", "candidates", "closed_world_note"}``。
        ``candidates`` の各要素は :meth:`ArxivEntry.to_dict` に ``status``
        （``new`` / ``ingested`` / ``dismissed``）と ``matched_keyphrases`` を足したもの。
        条件が空なら arXiv を呼ばず ``query=""`` / ``candidates=[]`` を返す（PD6）。

    Raises:
        arxiv_client.ArxivApiError: arXiv API への到達・応答・パースの失敗。
            呼び出し側が事実文で degrade する（空一覧を「該当なし」と偽らない）。
    """
    key = str(domain_key or "").strip()
    subscription: Optional[dict] = store.get_subscription(session, key) if key else None

    effective_categories = normalize_categories(
        categories if categories is not None else (subscription or {}).get("arxiv_categories")
    )
    effective_keyphrases = normalize_keyphrases(
        keyphrases if keyphrases is not None else (subscription or {}).get("keyphrases")
    )
    effective_authors = normalize_authors(
        followed_authors
        if followed_authors is not None
        else (subscription or {}).get("followed_authors")
    )

    query = build_search_query(
        effective_categories, effective_keyphrases, effective_authors
    )

    result: dict = {
        "domain_key": key,
        "query": query,
        "total": 0,
        "start": max(0, int(start or 0)),
        "candidates": [],
        "closed_world_note": CLOSED_WORLD_NOTE,
    }
    if not query:
        # 条件ゼロで arXiv を呼ばない（分野と無関係な全件が返るため — PD6）。
        return result

    total, entries = arxiv_client.search(
        query, start=result["start"], max_results=max_results
    )

    ingested = ingested_arxiv_ids(session)
    dismissed = store.dismissed_ids(session, key) if key else set()

    candidates: list[dict] = []
    for entry in entries:
        payload = entry.to_dict()
        arxiv_id = payload.get("arxiv_id") or ""
        if arxiv_id in ingested:
            status = "ingested"
        elif arxiv_id in dismissed:
            status = "dismissed"
        else:
            status = "new"
        payload["status"] = status
        payload["matched_keyphrases"] = matched_keyphrases(payload, effective_keyphrases)
        candidates.append(payload)

    if key:
        # コーパス回遊層「地図の端 — 外の輪」（corpus_roaming_design.md §6.2）が読む
        # 集約1ビットを同時に上書きする。保存するのは「新規候補が1件以上あったか」
        # だけで、候補そのものは保存しない（PD5）。
        store.touch_last_checked(
            session,
            key,
            found_new=any(c.get("status") == "new" for c in candidates),
        )

    result["total"] = int(total or 0)
    result["candidates"] = candidates
    return result
