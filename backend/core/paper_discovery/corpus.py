"""分野（domain_key）→ 取り込み済み document の解決（読み取り専用・非LLM）。

設計正本: ``docs/features/paper_discovery_design.md`` §4.2 / §6（Phase 3）。

Phase 1 では ``vocab.py`` の内部関数として「分野のコース → sources → documents」を
解決していたが、Phase 3 の関連度ランキング（``ranking.py``）と引用グラフ供給
（``citation_search.py``）が同じ解決を必要とするため、**唯一の正本**としてここへ
抽出した（コピペで3実装に増やさない）。

不変条項:

- **読み取りのみ**（INSERT / UPDATE / DELETE を書かない。``commit`` しない）。
- 対応 document ゼロは**正常な状態**（分野に紐づくコースがまだ無い）。空配列で
  SQL を撃たない — 空 IN 句は全件条件に化けやすいので入口で返す。
- FastAPI 非 import・``core.llm`` 非 import。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import text as sa_text

from core.paper_discovery.schema import normalize_arxiv_id

logger = logging.getLogger(__name__)

#: 1分野あたりに読む document 行の上限（重心計算・シード選択の防波堤）。
MAX_DOMAIN_DOCUMENTS = 200


def domain_material_ids(session, domain_key: str) -> list[str]:
    """分野（``learning_courses.data.cartridge_id``）のコースが参照する material_id。

    ``course_data`` の走査は正本アクセサ（``core.course_data``）に委ねる
    （素の dict アクセスを新規に書かない — Tier 3-18）。
    """
    from core.course_data import course_source_material_ids  # 遅延 import

    key = str(domain_key or "").strip()
    if not key:
        return []

    rows = session.execute(
        sa_text(
            """
            SELECT data
              FROM learning_courses
             WHERE data->>'cartridge_id' = :domain_key
            """
        ),
        {"domain_key": key},
    ).fetchall()

    material_ids: list[str] = []
    for row in rows:
        material_ids.extend(course_source_material_ids(row[0]))
    return [m for m in dict.fromkeys(material_ids) if m]


def domain_document_refs(session, domain_key: str) -> list[str]:
    """当該分野の document の参照値（``documents.id`` と ``source_path`` の両方）。

    ``theory_components.document_id`` は documents.id（UUID 文字列）と material_id
    （``source_path``）のどちらも取りうるため、両方を候補に入れる
    （``services._resolve_document`` と同じ二面性）。
    """
    material_ids = domain_material_ids(session, domain_key)
    if not material_ids:
        return []

    doc_rows = session.execute(
        sa_text(
            """
            SELECT id::text, COALESCE(source_path, '')
              FROM documents
             WHERE source_path = ANY(CAST(:material_ids AS text[]))
            """
        ),
        {"material_ids": material_ids},
    ).fetchall()

    refs: list[str] = []
    for row in doc_rows:
        for value in (row[0], row[1]):
            value = str(value or "").strip()
            if value:
                refs.append(value)
    return list(dict.fromkeys(refs))


def domain_document_rows(
    session,
    domain_key: str,
    *,
    limit: int = MAX_DOMAIN_DOCUMENTS,
) -> list[dict[str, Any]]:
    """当該分野の document を新しい順に返す（``{document_id, title, source_url}``）。

    ``source_url`` は URL 経由で取り込まれた document にだけ入る（手動アップロード分は
    空文字。取り込み出所の判定不能を偽装しない — 設計書 §8）。
    """
    material_ids = domain_material_ids(session, domain_key)
    if not material_ids:
        return []
    try:
        row_limit = max(0, int(limit))
    except (TypeError, ValueError):
        row_limit = MAX_DOMAIN_DOCUMENTS
    if row_limit == 0:
        return []

    rows = session.execute(
        sa_text(
            """
            SELECT id::text,
                   COALESCE(title, ''),
                   COALESCE(source_url, '')
              FROM documents
             WHERE source_path = ANY(CAST(:material_ids AS text[]))
             ORDER BY created_at DESC NULLS LAST, id
             LIMIT :limit
            """
        ),
        {"material_ids": material_ids, "limit": row_limit},
    ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        document_id = str(row[0] or "").strip()
        if not document_id:
            continue
        out.append(
            {
                "document_id": document_id,
                "title": str(row[1] or "").strip(),
                "source_url": str(row[2] or "").strip(),
            }
        )
    return out


def domain_document_ids(
    session,
    domain_key: str,
    *,
    limit: int = MAX_DOMAIN_DOCUMENTS,
) -> list[str]:
    """当該分野の ``documents.id``（UUID 文字列）を新しい順に返す。"""
    return [row["document_id"] for row in domain_document_rows(session, domain_key, limit=limit)]


def domain_ingested_papers(
    session,
    domain_key: str,
    *,
    limit: Optional[int] = None,
) -> list[dict[str, Any]]:
    """当該分野の**取り込み済み arXiv 論文**を新しい順に返す。

    ``documents.source_url`` から正規化 arXiv ID が取れる行だけが対象で、同一論文
    （版違い）は先勝ちで1件に畳む（``normalize_arxiv_id`` の規則 — 設計書 §4.1）。

    Returns:
        ``[{"arxiv_id", "title", "document_id"}]``。該当ゼロは空リスト（正常な状態）。
    """
    rows = domain_document_rows(session, domain_key)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        arxiv_id = normalize_arxiv_id(row.get("source_url"))
        if not arxiv_id or arxiv_id in seen:
            continue
        seen.add(arxiv_id)
        out.append(
            {
                "arxiv_id": arxiv_id,
                "title": row.get("title") or "",
                "document_id": row.get("document_id") or "",
            }
        )
        if limit is not None and len(out) >= max(0, int(limit)):
            break
    return out
