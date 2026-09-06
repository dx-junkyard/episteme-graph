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
- :mod:`~core.paper_discovery.ingest_queue` — 取り込みキューの状態遷移（migration 072 /
  Phase 2。取得・受理そのものは API 層の責務で、ここからは呼ばない）
- :mod:`~core.paper_discovery.search` — クエリ組み立てと候補への読み時注釈
- :mod:`~core.paper_discovery.corpus` — 分野 → 取り込み済み document の解決（正本）
- :mod:`~core.paper_discovery.ranking` — 関連度ランキング（Phase 3。**このパッケージで
  唯一 embedding を使う**。並べ替えだけで候補を捨てない）
- :mod:`~core.paper_discovery.compare` — 論文レーダーの AI 比較分析（1 コール・非保存。
  ``ranking`` と並ぶ ``core.llm`` 接触の allowlist）
- :mod:`~core.paper_discovery.citation_client` — 引用グラフ API の唯一の入口
  （Phase 3。宛先固定・3秒スロットル）
- :mod:`~core.paper_discovery.citation_search` — 引用グラフによる候補供給（Phase 3。
  ``DISCOVERY_CITATION_SOURCE_ENABLED`` のオプトイン）

FastAPI を import しない。``core.llm`` に触れてよいのは :mod:`ranking`（embedding）と
:mod:`compare`（比較分析の text LLM）の2ファイルだけで、どちらも関数内の遅延 import に
閉じる（Phase 1〜2 の経路は LLM 0回のまま）。
論文本体の取得は API 層が既存の URL 取得層（migration 070 / UF1〜UF6）を呼ぶ
（PD2 — このパッケージは HTTP で論文を取りに行かない）。
"""

from __future__ import annotations

from core.paper_discovery import (
    arxiv_client,
    citation_client,
    citation_search,
    corpus,
    ingest_queue,
    ranking,
    schema,
    search,
    store,
    vocab,
)
from core.paper_discovery.arxiv_client import ArxivApiError, parse_atom
from core.paper_discovery.citation_client import CitationApiError
from core.paper_discovery.citation_search import run_citation_search

# NOTE: ``arxiv_client.search`` はパッケージ属性として再エクスポートしない
# （サブモジュール ``core.paper_discovery.search`` と名前が衝突するため）。
# arXiv API を直接叩く場合は ``arxiv_client.search(...)`` を使う。
from core.paper_discovery.ranking import field_centroid, rank_candidates
from core.paper_discovery.schema import (
    ARXIV_API_HOST,
    CANDIDATE_STATUSES,
    KEYPHRASE_SOURCES,
    SEMANTIC_SCHOLAR_API_HOST,
    ArxivEntry,
    CitationEntry,
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
    "SEMANTIC_SCHOLAR_API_HOST",
    "ArxivApiError",
    "ArxivEntry",
    "CitationApiError",
    "CitationEntry",
    "abs_url_for",
    "arxiv_client",
    "build_search_query",
    "citation_client",
    "citation_search",
    "corpus",
    "dismiss",
    "field_centroid",
    "rank_candidates",
    "ranking",
    "run_citation_search",
    "dismissed_ids",
    "get_subscription",
    "ingest_queue",
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
