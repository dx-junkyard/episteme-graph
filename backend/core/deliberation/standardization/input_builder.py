"""標準化判定 worker — 対象文脈の構築（非LLM、Phase S）。

三角測量の3証拠のうち、②L層凍結版類似・③コーパス内反復は本モジュールが非LLMで
決定論的に計算する（①LLM事前知識のプロンプト用テキストの組み立てもここで行うが、
実際の LLM 呼び出しは agent.py/llm_client.py が担う）。

対象は library_entries 1件（＝shared_part）。読み取りのみ（``core.library.search`` の
retrieval / ``core.deliberation.identity_links`` の一覧取得）で、L層への書き込み関数は
一切 import しない（L層ガードレール維持）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.deliberation import identity_links
from core.deliberation.standardization.schema import (
    CorpusRepetitionEvidence,
    LibrarySimilarityEvidence,
)
from core.library import schema as library_schema
from core.library import search as library_search


@dataclass
class StandardizationTargetContext:
    """標準化判定 1 件分の対象文脈（LLM プロンプト用の記述 + 非LLM で計算済みの証拠②③）。"""

    entry_id: str
    domain_key: str = ""
    entry_type: str = ""
    name: str = ""
    aliases: list[str] = field(default_factory=list)
    summary: str = ""
    body: dict[str, Any] = field(default_factory=dict)
    library_similarity: LibrarySimilarityEvidence = field(default_factory=LibrarySimilarityEvidence)
    corpus_repetition: CorpusRepetitionEvidence = field(default_factory=CorpusRepetitionEvidence)


def _library_similarity(entry: dict[str, Any]) -> LibrarySimilarityEvidence:
    """同 domain・同 entry_type の他エントリ（自分を除く）の凍結版類似ヒット。"""
    query_text = library_schema.embedding_source_text(entry)
    if not query_text:
        return LibrarySimilarityEvidence()
    hits = library_search.search_frozen_entries(
        domain_key=str(entry.get("domain_key") or ""),
        query_text=query_text,
        entry_type=str(entry.get("entry_type") or ""),
        top_k=5,
    )
    others = [h for h in hits if str(h.get("entry_id") or "") != str(entry.get("id") or "")]
    return LibrarySimilarityEvidence(
        hit=bool(others),
        matched_entry_ids=[str(h["entry_id"]) for h in others],
    )


def _corpus_repetition(entry: dict[str, Any]) -> CorpusRepetitionEvidence:
    """confirmed identity link の instance_document_id ∪ source_document_ids の distinct 数。"""
    document_ids: set[str] = {
        str(d) for d in (entry.get("source_document_ids") or []) if str(d or "").strip()
    }
    links = identity_links.list_for_shared_part(str(entry.get("id") or ""))
    for link in links:
        if link.get("status") != "confirmed":
            continue
        doc_id = str(link.get("instance_document_id") or "").strip()
        if doc_id:
            document_ids.add(doc_id)
    return CorpusRepetitionEvidence(
        repeated=len(document_ids) >= 2,
        document_count=len(document_ids),
        document_ids=sorted(document_ids),
    )


def build_target_context(entry: dict[str, Any]) -> StandardizationTargetContext:
    """``core.library.store.get_entry()``/``list_entries()`` の1行（dict）から対象文脈を組み立てる。"""
    return StandardizationTargetContext(
        entry_id=str(entry.get("id") or ""),
        domain_key=str(entry.get("domain_key") or ""),
        entry_type=str(entry.get("entry_type") or ""),
        name=str(entry.get("name") or ""),
        aliases=list(entry.get("aliases") or []),
        summary=str(entry.get("summary") or ""),
        body=dict(entry.get("body") or {}),
        library_similarity=_library_similarity(entry),
        corpus_repetition=_corpus_repetition(entry),
    )
