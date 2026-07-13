"""RAG chat 用の共通ユーティリティ (pgvector 類似度検索)。

実際の RAG チャット回答生成は ``routes/learning.py::learning_chat`` が
別実装として行っている（コンテキスト構築・grounding 判定・casual モード等を含む）。
本モジュールが提供するのは、その下請けとしても使われる質問埋め込み・
チャンク類似度検索（tier 付与込み）のみ。
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from core.learning_experience import attach_tiers, approved_chunk_ids
from core.llm import generate_embeddings, get_embedding_dim
from core.postgres import get_session

logger = logging.getLogger(__name__)


def _embed_query(question: str) -> list[float]:
    """質問文字列を embedding ベクトルに変換する。"""
    vectors = generate_embeddings([question])
    return vectors[0]


def search_chunks(question: str, material_id: str, top_k: int = 5) -> list[dict]:
    """PostgreSQL pgvector で material_id に属するチャンクを類似度検索して返す。

    仕様書 §3.3 の契約変更: 戻り値は ``list[str]`` ではなく構造化 dict のリスト
    ``{id, text, source_title, source_file, score, approved, tier}``。tier は信頼性
    レイヤー(L1)で付与し、approved は教員承認(teacher_reviewed)に紐づく chunk のみ True。
    """
    query_vector = _embed_query(question)
    dim = get_embedding_dim()

    session = get_session()
    try:
        rows = session.execute(
            text(f"""
                SELECT c.id,
                       c.text,
                       COALESCE(d.title, '') AS source_title,
                       COALESCE(d.filename, '') AS source_file,
                       1 - (c.embedding::halfvec({dim}) <=> CAST(:query_vector AS halfvec({dim}))) AS score
                FROM chunks c
                LEFT JOIN documents d ON c.document_id = d.id
                WHERE c.material_id = :material_id
                  AND c.embedding IS NOT NULL
                ORDER BY c.embedding::halfvec({dim}) <=> CAST(:query_vector AS halfvec({dim}))
                LIMIT :limit
            """),
            {
                "material_id": material_id,
                "query_vector": str(query_vector),
                "limit": top_k,
            },
        ).fetchall()

        results = [
            {
                "id": str(row[0]),
                "text": row[1],
                "source_title": row[2] or row[3] or "不明な教材",
                "source_file": row[3],
                "score": float(row[4]),
            }
            for row in rows
            if row[1]
        ]
        # L1 信頼性: 教員承認に紐づく chunk へ approved を付与してから tier を判定。
        approved = approved_chunk_ids(session, [r["id"] for r in results])
        for r in results:
            r["approved"] = r["id"] in approved
        return attach_tiers(results)
    finally:
        session.close()
