"""論文ディスカバリー層（arXiv 分野購読とコーパス成長ループ）— core。

設計正本: ``docs/features/paper_discovery_design.md``（不変条項 PD1〜PD8）。
DDL は ``backend/db/071_paper_discovery.sql``、API 層は
``backend/api/routes/paper_discovery.py``（実パス ``/api/admin/discovery/...``）。

構成:

- :mod:`~core.paper_discovery.schema` — 語彙・DTO・arXiv ID 正規化の正本
- :mod:`~core.paper_discovery.arxiv_client` — arXiv API の唯一の入口（宛先固定・
  3秒スロットル）
- :mod:`~core.paper_discovery.vocab` — キーフレーズ候補の供給（分野語彙から）
- :mod:`~core.paper_discovery.store` — 購読・見送りの読み書き（行削除の SQL を持たない）
- :mod:`~core.paper_discovery.search` — クエリ組み立てと候補への読み時注釈

FastAPI / ``core.llm`` を import しない（発見層は Phase 1〜2 を通じて LLM 0回）。
論文本体の取得は API 層が既存の URL 取得層（migration 070 / UF1〜UF6）を呼ぶ
（PD2 — このパッケージは HTTP で論文を取りに行かない）。
"""

from __future__ import annotations

from core.paper_discovery import arxiv_client, schema, search, store, vocab
from core.paper_discovery.arxiv_client import ArxivApiError, parse_atom

# NOTE: ``arxiv_client.search`` はパッケージ属性として再エクスポートしない
# （サブモジュール ``core.paper_discovery.search`` と名前が衝突するため）。
# arXiv API を直接叩く場合は ``arxiv_client.search(...)`` を使う。
from core.paper_discovery.schema import (
    ARXIV_API_HOST,
    CANDIDATE_STATUSES,
    KEYPHRASE_SOURCES,
    ArxivEntry,
    abs_url_for,
    normalize_arxiv_id,
    normalize_authors,
    normalize_categories,
    normalize_keyphrases,
    pdf_url_for,
    split_arxiv_ref,
)
from core.paper_discovery.search import (
    CLOSED_WORLD_NOTE,
    build_search_query,
    ingested_arxiv_ids,
    run_search,
)
from core.paper_discovery.store import (
    dismiss,
    dismissed_ids,
    get_subscription,
    list_dismissals,
    list_subscriptions,
    restore,
    touch_last_checked,
    upsert_subscription,
)
from core.paper_discovery.vocab import keyphrase_candidates

__all__ = [
    "ARXIV_API_HOST",
    "CANDIDATE_STATUSES",
    "CLOSED_WORLD_NOTE",
    "KEYPHRASE_SOURCES",
    "ArxivApiError",
    "ArxivEntry",
    "abs_url_for",
    "arxiv_client",
    "build_search_query",
    "dismiss",
    "dismissed_ids",
    "get_subscription",
    "ingested_arxiv_ids",
    "keyphrase_candidates",
    "list_dismissals",
    "list_subscriptions",
    "normalize_arxiv_id",
    "normalize_authors",
    "normalize_categories",
    "normalize_keyphrases",
    "parse_atom",
    "pdf_url_for",
    "restore",
    "run_search",
    "schema",
    "search",
    "split_arxiv_ref",
    "store",
    "touch_last_checked",
    "upsert_subscription",
    "vocab",
]
