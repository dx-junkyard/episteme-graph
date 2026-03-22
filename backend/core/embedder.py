"""Qdrant vector storage for Episteme Graph paper chunk embeddings.

各論文のチャンクを text-embedding-3-large でベクトル化し、
Qdrant コレクション "papers" に arxiv_id メタデータ付きで保存する。

環境変数
--------
QDRANT_HOST   Qdrant サービスのホスト名 (default: localhost)
QDRANT_PORT   Qdrant REST API ポート   (default: 6333)
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, FieldCondition, Filter, MatchText, MatchValue, PointStruct, VectorParams

from core.llm import get_client, get_settings
from core.schema import CausalEdge, PaperStructure

logger = logging.getLogger(__name__)

_COLLECTION = "papers"
_PATTERNS_COLLECTION = "patterns"
_VECTOR_DIM = 3072  # text-embedding-3-large の次元数


@lru_cache(maxsize=1)
def _qdrant() -> QdrantClient:
    """Qdrant クライアントのシングルトンを返す。"""
    host = os.environ.get("QDRANT_HOST", "localhost")
    port = int(os.environ.get("QDRANT_PORT", "6333"))
    logger.info("Connecting to Qdrant at %s:%d", host, port)
    return QdrantClient(host=host, port=port)


def _ensure_collection() -> None:
    """コレクション "papers" が存在しない場合は作成する。"""
    client = _qdrant()
    existing = {c.name for c in client.get_collections().collections}
    if _COLLECTION not in existing:
        client.create_collection(
            collection_name=_COLLECTION,
            vectors_config=VectorParams(size=_VECTOR_DIM, distance=Distance.COSINE),
        )
        logger.info("Qdrant collection '%s' created (dim=%d)", _COLLECTION, _VECTOR_DIM)


def _build_ancestors_map(edges: list[CausalEdge]) -> dict[str, list[str]]:
    """CONTAINS エッジから各ノードの祖先（親ノード群）のマップを構築する。

    絶対的な depth_level は使用せず、CONTAINS エッジの推移的閉包から
    各ノードの祖先リスト（context_path / ancestors）を算出する。
    これにより「特定のマクロなテーマに属するノード」のフィルタリングが可能になる。

    Returns
    -------
    dict[str, list[str]]
        ノード名 → 祖先ノード名のリスト（直近の親が先頭、ルートが末尾）。
    """
    # parent_of: child -> set of direct parents
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
    """チャンクリストを一括 Embedding して Qdrant に upsert する。

    Parameters
    ----------
    chunks:
        論文テキストを分割したチャンクのリスト。
    arxiv_id:
        論文の arXiv ID（メタデータとして各ベクトルに付与）。
    openai_client:
        使用する OpenAI クライアント。
    embedding_model:
        Embedding モデル名（例: "text-embedding-3-large"）。
    extracted_structure:
        抽出済みの PaperStructure（指定時は SMILES DSL とオントロジー情報を
        ペイロードに付与する）。
    """
    if not chunks:
        return

    _ensure_collection()

    # OpenAI Embeddings API は最大 2048 入力を一度に受け付けるが、
    # テキストが大きい場合は分割して呼ぶ（ここでは 100 件ずつ）
    _BATCH = 100
    all_embeddings: list[list[float]] = []
    for i in range(0, len(chunks), _BATCH):
        batch = chunks[i : i + _BATCH]
        resp = openai_client.embeddings.create(
            model=embedding_model,
            input=batch,
        )
        all_embeddings.extend([e.embedding for e in resp.data])

    # 決定論的な整数 ID を生成（arxiv_id + チャンクインデックスのハッシュ）
    safe_id = arxiv_id.replace("/", "_").replace(".", "_")

    # extracted_structure が指定されている場合、SMILES DSL と変数をペイロードに追加
    extra_payload: dict = {}
    if extracted_structure is not None:
        extra_payload["smiles_dsl"] = extracted_structure.abstract_structure.smiles_dsl
        extra_payload["variables"] = extracted_structure.abstract_structure.variables
        # ancestors: CONTAINS エッジから各ノードの祖先（マクロ文脈）を構築
        # 絶対的な depth_level は保存せず、相対的な祖先リストのみを保持する
        ancestors_map = _build_ancestors_map(extracted_structure.abstract_structure.edges)
        if ancestors_map:
            extra_payload["ancestors"] = ancestors_map

    points: list[PointStruct] = []
    for i, vector in enumerate(all_embeddings):
        point_id = abs(hash(f"{safe_id}_{i}")) % (2**53)
        payload = {
            "arxiv_id": arxiv_id,
            "chunk_index": i,
            # Qdrant ペイロードには先頭 500 文字のみ保存（コスト削減）
            "text": chunks[i][:500],
            **extra_payload,
        }
        points.append(
            PointStruct(
                id=point_id,
                vector=vector,
                payload=payload,
            )
        )

    _qdrant().upsert(collection_name=_COLLECTION, points=points)
    logger.info(
        "Stored %d chunk embeddings for arxiv_id=%s in Qdrant collection '%s'",
        len(points),
        arxiv_id,
        _COLLECTION,
    )


# ---------------------------------------------------------------------------
# Patterns collection: store and search abstraction patterns
# ---------------------------------------------------------------------------

def _ensure_patterns_collection() -> None:
    """コレクション "patterns" が存在しない場合は作成する。"""
    client = _qdrant()
    existing = {c.name for c in client.get_collections().collections}
    if _PATTERNS_COLLECTION not in existing:
        client.create_collection(
            collection_name=_PATTERNS_COLLECTION,
            vectors_config=VectorParams(size=_VECTOR_DIM, distance=Distance.COSINE),
        )
        logger.info(
            "Qdrant collection '%s' created (dim=%d)", _PATTERNS_COLLECTION, _VECTOR_DIM
        )


def embed_and_store_pattern(
    pattern_id: str,
    pattern_text: str,
    openai_client: OpenAI,
    embedding_model: str,
) -> None:
    """パターンのテキスト表現を Embedding して Qdrant patterns コレクションに保存する。

    Parameters
    ----------
    pattern_id:
        AbstractionPattern の pattern_id。
    pattern_text:
        パターンの説明文（name + description + structural_rules を結合したもの）。
    openai_client:
        使用する OpenAI クライアント。
    embedding_model:
        Embedding モデル名。
    """
    _ensure_patterns_collection()

    resp = openai_client.embeddings.create(model=embedding_model, input=[pattern_text])
    vector = resp.data[0].embedding

    point_id = abs(hash(f"pattern_{pattern_id}")) % (2**53)
    _qdrant().upsert(
        collection_name=_PATTERNS_COLLECTION,
        points=[
            PointStruct(
                id=point_id,
                vector=vector,
                payload={"pattern_id": pattern_id, "text": pattern_text[:500]},
            )
        ],
    )
    logger.info("Stored pattern embedding for pattern_id=%s", pattern_id)


def search_similar_papers(
    query_text: str,
    openai_client: OpenAI,
    embedding_model: str,
    top_k: int = 5,
    exclude_arxiv_id: str | None = None,
) -> list[dict]:
    """パターン（またはクエリテキスト）に類似する過去の論文チャンクを Qdrant から検索する。

    Returns
    -------
    list[dict]
        各要素は ``{"arxiv_id": str, "score": float, "text": str}``。
        同一 arxiv_id の重複は排除し、最高スコアのみを残す。
    """
    _ensure_collection()

    resp = openai_client.embeddings.create(model=embedding_model, input=[query_text])
    vector = resp.data[0].embedding

    # Qdrant 検索（top_k * 3 で多めに取得して重複排除）
    results = _qdrant().search(
        collection_name=_COLLECTION,
        query_vector=vector,
        limit=top_k * 3,
    )

    seen: dict[str, dict] = {}
    for hit in results:
        arxiv_id = hit.payload.get("arxiv_id", "")
        if not arxiv_id:
            continue
        if exclude_arxiv_id and arxiv_id == exclude_arxiv_id:
            continue
        if arxiv_id not in seen or hit.score > seen[arxiv_id]["score"]:
            seen[arxiv_id] = {
                "arxiv_id": arxiv_id,
                "score": hit.score,
                "text": hit.payload.get("text", ""),
            }

    # スコア降順でソートし、上位 top_k 件を返す
    ranked = sorted(seen.values(), key=lambda x: x["score"], reverse=True)
    return ranked[:top_k]


# ---------------------------------------------------------------------------
# FANNS (Filtered ANNS) hybrid search
# ---------------------------------------------------------------------------

def search_fanns_hybrid(
    query_dsl_regex: str,
    query_text: str,
    top_k: int = 5,
) -> list[dict]:
    """Filtered ANNS (Pre-filtering) によるハイブリッド検索。

    FANNSアーキテクチャ: Qdrant の query_filter を用いた Pre-filtering により、
    「意味（ベクトル）は遠いが構造（SMILES DSL）が一致する異分野の論文」を
    確実に発見する。Post-filtering では意味が遠い論文がベクトル検索の時点で
    足切りされるため、分野横断検索が成立しない。

    処理フロー:
    1. ``query_text`` を Embedding モデルでベクトル化する。
    2. ``query_dsl_regex`` が指定されている場合、Qdrant の ``MatchText``
       フィルタを構築し、``smiles_dsl`` ペイロードで DB 側事前絞り込みを行う。
    3. フィルタ付きベクトル検索を実行し、構造が一致する候補の中から
       意味的に近い順にランキングする。
    4. 同一 ``arxiv_id`` の重複を排除し、最高スコアのもののみを残す。
    5. スコア降順でソートし、上位 ``top_k`` 件を返す。

    Parameters
    ----------
    query_dsl_regex:
        SMILES DSL ペイロードに対するテキストフィルタパターン。
        Qdrant の MatchText（全文検索）を使用して DB 側で事前絞り込みを行う。
        空文字列の場合はフィルタリングをスキップし、純粋なベクトル検索のみ行う。
    query_text:
        ベクトル検索用の自然言語クエリ。Embedding 後にコサイン類似度検索を行う。
    top_k:
        返却する上位件数。

    Returns
    -------
    list[dict]
        各要素は ``{"arxiv_id": str, "score": float, "text": str,
        "smiles_dsl": str, "variables": list[str]}``。
    """
    _ensure_collection()

    client = get_client()
    settings = get_settings()

    # 1. query_text をベクトル化
    resp = client.embeddings.create(model=settings.embedding_model, input=[query_text])
    query_vector = resp.data[0].embedding

    # 2. Pre-filtering: Qdrant の MatchText で smiles_dsl を DB 側で事前絞り込み
    query_filter = None
    if query_dsl_regex:
        query_filter = Filter(
            must=[
                FieldCondition(
                    key="smiles_dsl",
                    match=MatchText(text=query_dsl_regex),
                )
            ]
        )

    # 3. フィルタ付きベクトル検索（構造一致 → 意味的類似度ランキング）
    results = _qdrant().search(
        collection_name=_COLLECTION,
        query_vector=query_vector,
        query_filter=query_filter,
        limit=top_k * 3,
    )

    # 4. 同一 arxiv_id の重複を排除（最高スコアを保持）
    seen: dict[str, dict] = {}
    for hit in results:
        arxiv_id = hit.payload.get("arxiv_id", "")
        if not arxiv_id:
            continue
        aid = arxiv_id
        if aid not in seen or hit.score > seen[aid]["score"]:
            seen[aid] = {
                "arxiv_id": arxiv_id,
                "score": hit.score,
                "text": hit.payload.get("text", ""),
                "smiles_dsl": hit.payload.get("smiles_dsl", ""),
                "variables": hit.payload.get("variables", []),
            }

    # 5. スコア降順ソート → 上位 top_k 件
    ranked = sorted(seen.values(), key=lambda x: x["score"], reverse=True)
    logger.info(
        "FANNS hybrid search: filter=%r, candidates=%d, unique=%d, returned=%d",
        query_dsl_regex, len(results), len(seen), min(len(ranked), top_k),
    )
    return ranked[:top_k]
