"""RAG-based chat logic for Episteme Graph.

ユーザーの質問に対して:
1. text-embedding-3-large で質問をベクトル化
2. Qdrant で対象論文のチャンクを類似度検索
3. MinIO から PaperStructure を取得
4. チャンク + 構造 + 履歴 + 質問をプロンプトに組み込んで LLM に回答生成
"""

from __future__ import annotations

import json
import logging

from openai import OpenAI
from qdrant_client.models import FieldCondition, Filter, MatchValue

from core.embedder import _COLLECTION, _qdrant
from core.llm import get_client, get_settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a scientific paper analysis assistant for Episteme Graph.
You help researchers deeply understand academic papers by answering questions based on:
1. Relevant text chunks retrieved from the paper (via vector search)
2. The extracted structured analysis of the paper

**CRITICAL INSTRUCTION REGARDING STRUCTURE DATA:**
The extracted structured analysis is provided in JSON format. It includes a field named "smiles_dsl" under "abstract_structure" (or directly).
This field contains the "Episteme Graph-SMILES DSL", a specialized format used to represent the causal and relational graph of the paper.
Format: `(varID:OntologyType:value) ==[CorePredicate:domain_verb:polarity]=> (targetVarID:OntologyType:value)`
CorePredicates include: CAUSES, INHIBITS, CORRELATES, DEFINES, MEASURES, TRANSFORMS, REQUIRES, CONTAINS, EQUIVALENT.
- CONTAINS represents parent-child (macro-micro) inclusion relationships.
- EQUIVALENT represents equivalence or contrast between concepts at the same level.
- REQUIRES represents prerequisite/dependency lateral relationships.
Polarity values: '+' (positive), '-' (negative), '+/-' (bidirectional/conditional), '?' (unknown direction).
If the user asks about "SMILES", "SMILES DSL", or "structure graph", you MUST look at this "smiles_dsl" field and the "variables" / "edges" fields to formulate your answer. Do not confuse it with chemical SMILES.

**RESPONSE FORMAT FOR CONCEPT QUESTIONS:**
When the user asks about a concept, follow these steps:
1. Identify the target concept and provide an overview explanation.
2. Using the DSL structure data, systematically explain:
   - Macro/micro logical composition: parent elements and child elements connected via CONTAINS edges.
   - Prerequisites and related knowledge: lateral elements connected via REQUIRES and EQUIVALENT edges.
   - Causal relationships: elements connected via CAUSES, INHIBITS, CORRELATES, etc.
3. At the end of your response, present actionable drill-down options as a Markdown list of "explanation links" for each related element (child nodes, prerequisite nodes, parent nodes). Use the format:
   - `[〇〇について詳しく聞く]`
   Example:
   - `[仮説Aの構成要素について詳しく聞く]`
   - `[前提条件Bについて詳しく聞く]`
   - `[親概念Cの全体像について詳しく聞く]`

Answer clearly and concisely in the same language as the user's question.
If the provided context does not contain enough information to answer, say so honestly."""


def _embed_query(question: str, client: OpenAI, model: str) -> list[float]:
    """質問文字列を embedding ベクトルに変換する。"""
    resp = client.embeddings.create(model=model, input=[question])
    return resp.data[0].embedding


def search_chunks(question: str, arxiv_id: str, top_k: int = 5) -> list[str]:
    """Qdrant で arxiv_id に属するチャンクを類似度検索して返す。"""
    settings = get_settings()
    client = get_client()

    query_vector = _embed_query(question, client, settings.embedding_model)

    result = _qdrant().query_points(
        collection_name=_COLLECTION,
        query=query_vector,
        query_filter=Filter(
            must=[
                FieldCondition(
                    key="arxiv_id",
                    match=MatchValue(value=arxiv_id),
                )
            ]
        ),
        limit=top_k,
        with_payload=True,
    )

    return [hit.payload.get("text", "") for hit in result.points if hit.payload]


def _get_paper_structure(arxiv_id: str, minio_client) -> dict:
    """MinIO から抽出済み PaperStructure JSON を取得して dict で返す。"""
    safe_id = arxiv_id.replace("/", "_")
    try:
        response = minio_client.get_object("extracted-structures", f"{safe_id}.json")
        data = response.read()
        response.close()
        response.release_conn()
        return json.loads(data)
    except Exception as exc:
        logger.warning("Could not load PaperStructure for %s: %s", arxiv_id, exc)
        return {}


def generate_chat_response(
    arxiv_id: str,
    message: str,
    history: list[dict],
    minio_client,
) -> str:
    """RAG ベースでユーザーの質問に回答する。

    Parameters
    ----------
    arxiv_id:
        対象論文の arXiv ID。
    message:
        ユーザーの最新メッセージ。
    history:
        過去の対話履歴。各要素は {"role": "user"|"assistant", "content": str}。
    minio_client:
        MinIO クライアント（StorageManager.client）。

    Returns
    -------
    str
        LLM が生成した回答文字列。
    """
    settings = get_settings()
    client = get_client()

    # 1. 関連チャンクをベクトル検索
    chunks = search_chunks(message, arxiv_id, top_k=5)

    # 2. PaperStructure を取得
    structure = _get_paper_structure(arxiv_id, minio_client)

    # 【デバッグ用】取得した構造データをログに出力して確認する
    logger.info(f"DEBUG: Loaded structure for {arxiv_id}: {json.dumps(structure.get('abstract_structure', {}), ensure_ascii=False)}")

    # 3. コンテキストブロックを構築
    context_parts: list[str] = []
    if chunks:
        context_parts.append("## Relevant Paper Excerpts\n" + "\n---\n".join(chunks))
    if structure:
        context_parts.append(
            "## Extracted Paper Structure\n"
            + json.dumps(structure, ensure_ascii=False, indent=2)
        )

    context_block = "\n\n".join(context_parts) if context_parts else "(No context available)"

    # 4. メッセージリストを構築（コンテキストをシステムターン後に挿入）
    messages: list[dict] = [{"role": "system", "content": _SYSTEM_PROMPT}]

    # コンテキストをアシスタントに事前提示
    messages.append({
        "role": "user",
        "content": (
            f"Context for paper `{arxiv_id}`:\n\n{context_block}\n\n"
            "Please keep the above context in mind when answering my questions."
        ),
    })
    messages.append({
        "role": "assistant",
        "content": "Understood. I will use this context to answer your questions about the paper.",
    })

    # 過去の履歴を追加
    for turn in history:
        messages.append({"role": turn["role"], "content": turn["content"]})

    # 最新の質問
    messages.append({"role": "user", "content": message})

    # 5. LLM を呼び出して回答を生成
    response = client.chat.completions.create(
        model=settings.analysis_model,
        messages=messages,
        temperature=0.3,
    )

    return response.choices[0].message.content or ""
