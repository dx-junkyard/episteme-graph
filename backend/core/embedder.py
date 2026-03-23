"""PostgreSQL pgvector storage for Episteme Graph paper chunk embeddings.

各論文のチャンクを text-embedding-3-large でベクトル化し、
PostgreSQL の chunks テーブルに pgvector embedding 付きで保存する。

環境変数
--------
DATABASE_URL   PostgreSQL 接続文字列 (default: postgresql://episteme:episteme@postgres:5432/episteme)
"""

from __future__ import annotations

import logging
import uuid

from openai import OpenAI
from sqlalchemy import text

from core.llm import get_client, get_settings
from core.postgres import get_session
from core.schema import CausalEdge, PaperStructure

logger = logging.getLogger(__name__)

_VECTOR_DIM = 3072  # text-embedding-3-large の次元数


def _build_ancestors_map(edges: list[CausalEdge]) -> dict[str, list[str]]:
    """CONTAINS エッジから各ノードの祖先（親ノード群）のマップを構築する。"""
    parent_of: dict[str, set[str]] = {}
    for edge in edges:
        if edge.core_predicate.value == "CONTAINS":
            parent_of.setdefault(edge.target, set()).add(edge.source)

    if not parent_of:
        return {}

    def _get_ancestors(node: str, visited: set[str] | None = None) -> list[str]:
        if visited is None:
            visited = set()
        if node in visited:
            return []
        visited.add(node)
        result: list[str] = []
        for parent in parent_of.get(node, []):
            result.append(parent)
            result.extend(_get_ancestors(parent, visited))
        return result

    ancestors_map: dict[str, list[str]] = {}
    for child in parent_of:
        ancestors = _get_ancestors(child)
        if ancestors:
            ancestors_map[child] = ancestors
    return ancestors_map


def embed_and_store(
    chunks: list[str],
    arxiv_id: str,
    openai_client: OpenAI,
    embedding_model: str,
    extracted_structure: PaperStructure | None = None,
) -> None:
    """チャンクリストを一括 Embedding して PostgreSQL chunks テーブルに保存する。"""
    if not chunks:
        return

    # OpenAI Embeddings API でバッチ処理
    _BATCH = 100
    all_embeddings: list[list[float]] = []
    for i in range(0, len(chunks), _BATCH):
        batch = chunks[i : i + _BATCH]
        resp = openai_client.embeddings.create(model=embedding_model, input=batch)
        all_embeddings.extend([e.embedding for e in resp.data])

    # extracted_structure からメタデータを抽出
    smiles_dsl = None
    variables = None
    ancestors = None
    if extracted_structure is not None:
        smiles_dsl = extracted_structure.abstract_structure.smiles_dsl
        variables = extracted_structure.abstract_structure.variables
        ancestors_map = _build_ancestors_map(extracted_structure.abstract_structure.edges)
        if ancestors_map:
            ancestors = ancestors_map

    # まずドキュメントが存在するか確認、なければ作成
    session = get_session()
    try:
        result = session.execute(
            text("SELECT id FROM documents WHERE source_path = :arxiv_id LIMIT 1"),
            {"arxiv_id": arxiv_id},
        ).fetchone()

        if result:
            doc_id = result[0]
        else:
            doc_id = uuid.uuid4()
            title = extracted_structure.title if extracted_structure else arxiv_id
            session.execute(
                text("""
                    INSERT INTO documents (id, title, doc_type, source_path)
                    VALUES (:id, :title, 'paper', :source_path)
                """),
                {"id": doc_id, "title": title, "source_path": arxiv_id},
            )

        # チャンクを一括挿入
        for i, (chunk_text, vector) in enumerate(zip(chunks, all_embeddings)):
            chunk_id = uuid.uuid4()
            session.execute(
                text("""
                    INSERT INTO chunks (id, document_id, chunk_index, text, embedding,
                                        arxiv_id, smiles_dsl, variables, ancestors)
                    VALUES (:id, :doc_id, :idx, :text, :embedding,
                            :arxiv_id, :smiles_dsl, :variables, :ancestors)
                """),
                {
                    "id": chunk_id,
                    "doc_id": doc_id,
                    "idx": i,
                    "text": chunk_text,
                    "embedding": str(vector),
                    "arxiv_id": arxiv_id,
                    "smiles_dsl": smiles_dsl,
                    "variables": variables if variables else None,
                    "ancestors": ancestors if ancestors else None,
                },
            )

        session.commit()
        logger.info(
            "Stored %d chunk embeddings for arxiv_id=%s in PostgreSQL",
            len(all_embeddings),
            arxiv_id,
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Patterns: store pattern embeddings in chunks table with special marker
# ---------------------------------------------------------------------------

def embed_and_store_pattern(
    pattern_id: str,
    pattern_text: str,
    openai_client: OpenAI,
    embedding_model: str,
) -> None:
    """パターンのテキスト表現を Embedding して PostgreSQL に保存する。"""
    resp = openai_client.embeddings.create(model=embedding_model, input=[pattern_text])
    vector = resp.data[0].embedding

    session = get_session()
    try:
        # パターン用のドキュメントを取得または作成
        result = session.execute(
            text("SELECT id FROM documents WHERE source_path = :path LIMIT 1"),
            {"path": f"pattern:{pattern_id}"},
        ).fetchone()

        if result:
            doc_id = result[0]
        else:
            doc_id = uuid.uuid4()
            session.execute(
                text("""
                    INSERT INTO documents (id, title, doc_type, source_path)
                    VALUES (:id, :title, 'report', :source_path)
                """),
                {"id": doc_id, "title": f"Pattern: {pattern_id}", "source_path": f"pattern:{pattern_id}"},
            )

        existing = session.execute(
            text("SELECT id FROM chunks WHERE document_id = :doc_id AND chunk_index = 0 LIMIT 1"),
            {"doc_id": doc_id},
        ).fetchone()

        if existing:
            session.execute(
                text("""
                    UPDATE chunks SET text = :text, embedding = :embedding
                    WHERE id = :id
                """),
                {
                    "id": existing[0],
                    "text": pattern_text,
                    "embedding": str(vector),
                },
            )
        else:
            session.execute(
                text("""
                    INSERT INTO chunks (id, document_id, chunk_index, text, embedding, arxiv_id)
                    VALUES (:id, :doc_id, 0, :text, :embedding, :arxiv_id)
                """),
                {
                    "id": uuid.uuid4(),
                    "doc_id": doc_id,
                    "text": pattern_text,
                    "embedding": str(vector),
                    "arxiv_id": f"pattern:{pattern_id}",
                },
            )

        session.commit()
        logger.info("Stored pattern embedding for pattern_id=%s", pattern_id)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def search_similar_papers(
    query_text: str,
    openai_client: OpenAI,
    embedding_model: str,
    top_k: int = 5,
    exclude_arxiv_id: str | None = None,
) -> list[dict]:
    """パターン（またはクエリテキスト）に類似する過去の論文チャンクを PostgreSQL から検索する。"""
    resp = openai_client.embeddings.create(model=embedding_model, input=[query_text])
    vector = resp.data[0].embedding

    session = get_session()
    try:
        inner = """
            SELECT DISTINCT ON (c.arxiv_id)
                   c.arxiv_id,
                   1 - (c.embedding::halfvec(3072) <=> CAST(:query_vector AS halfvec(3072))) AS score,
                   c.text
            FROM chunks c
            WHERE c.arxiv_id IS NOT NULL
              AND c.arxiv_id NOT LIKE 'pattern:%%'
        """
        params: dict = {"query_vector": str(vector)}

        if exclude_arxiv_id:
            inner += " AND c.arxiv_id != :exclude_id"
            params["exclude_id"] = exclude_arxiv_id

        inner += """
            ORDER BY c.arxiv_id, c.embedding::halfvec(3072) <=> CAST(:query_vector AS halfvec(3072))
        """

        query = f"""
            SELECT arxiv_id, score, text
            FROM ({inner}) AS deduped
            ORDER BY score DESC
            LIMIT :limit
        """
        params["limit"] = top_k

        rows = session.execute(text(query), params).fetchall()

        return [
            {"arxiv_id": row[0], "score": float(row[1]), "text": row[2]}
            for row in rows
        ]
    finally:
        session.close()


# ---------------------------------------------------------------------------
# FANNS (Filtered ANNS) hybrid search
# ---------------------------------------------------------------------------

def search_fanns_hybrid(
    query_dsl_regex: str,
    query_text: str,
    top_k: int = 5,
) -> list[dict]:
    """Filtered ANNS by SMILES DSL + vector similarity search on PostgreSQL."""
    client = get_client()
    settings = get_settings()

    resp = client.embeddings.create(model=settings.embedding_model, input=[query_text])
    query_vector = resp.data[0].embedding

    session = get_session()
    try:
        inner = """
            SELECT DISTINCT ON (c.arxiv_id)
                   c.arxiv_id,
                   1 - (c.embedding::halfvec(3072) <=> CAST(:query_vector AS halfvec(3072))) AS score,
                   c.text,
                   c.smiles_dsl,
                   c.variables
            FROM chunks c
            WHERE c.arxiv_id IS NOT NULL
              AND c.arxiv_id NOT LIKE 'pattern:%%'
        """
        params: dict = {"query_vector": str(query_vector)}

        if query_dsl_regex:
            inner += " AND c.smiles_dsl LIKE :dsl_pattern"
            params["dsl_pattern"] = f"%{query_dsl_regex}%"

        inner += """
            ORDER BY c.arxiv_id, c.embedding::halfvec(3072) <=> CAST(:query_vector AS halfvec(3072))
        """

        query = f"""
            SELECT arxiv_id, score, text, smiles_dsl, variables
            FROM ({inner}) AS deduped
            ORDER BY score DESC
            LIMIT :limit
        """
        params["limit"] = top_k

        rows = session.execute(text(query), params).fetchall()

        results = [
            {
                "arxiv_id": row[0],
                "score": float(row[1]),
                "text": row[2] or "",
                "smiles_dsl": row[3] or "",
                "variables": row[4] or [],
            }
            for row in rows
        ]
        logger.info(
            "FANNS hybrid search: filter=%r, returned=%d",
            query_dsl_regex, len(results),
        )
        return results
    finally:
        session.close()
