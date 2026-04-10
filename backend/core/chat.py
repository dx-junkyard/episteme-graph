"""RAG-based chat logic for Episteme Graph.

ユーザーの質問に対して:
1. text-embedding-3-large で質問をベクトル化
2. PostgreSQL pgvector で対象論文のチャンクを類似度検索
3. MinIO から PaperStructure を取得
4. チャンク + 構造 + 履歴 + 質問をプロンプトに組み込んで LLM に回答生成
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import text

from core.llm import generate_text, generate_embeddings, get_embedding_dim
from core.postgres import get_session

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


def _embed_query(question: str) -> list[float]:
    """質問文字列を embedding ベクトルに変換する。"""
    vectors = generate_embeddings([question])
    return vectors[0]


def search_chunks(question: str, material_id: str, top_k: int = 5) -> list[str]:
    """PostgreSQL pgvector で material_id に属するチャンクを類似度検索して返す。"""
    query_vector = _embed_query(question)
    dim = get_embedding_dim()

    session = get_session()
    try:
        rows = session.execute(
            text(f"""
                SELECT c.text
                FROM chunks c
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

        return [row[0] for row in rows if row[0]]
    finally:
        session.close()


def _get_paper_structure(paper_id: str, minio_client) -> dict:
    """MinIO から抽出済み PaperStructure JSON を取得して dict で返す。"""
    safe_id = paper_id.replace("/", "_")
    try:
        response = minio_client.get_object("extracted-structures", f"{safe_id}.json")
        data = response.read()
        response.close()
        response.release_conn()
        return json.loads(data)
    except Exception as exc:
        logger.warning("Could not load PaperStructure for %s: %s", paper_id, exc)
        return {}


def generate_chat_response(
    material_id: str,
    message: str,
    history: list[dict],
    minio_client,
) -> str:
    """RAG ベースでユーザーの質問に回答する。"""
    # 1. 関連チャンクをベクトル検索
    chunks = search_chunks(message, material_id, top_k=5)

    # 2. PaperStructure を取得
    structure = _get_paper_structure(material_id, minio_client)

    logger.info(f"DEBUG: Loaded structure for {material_id}: {json.dumps(structure.get('abstract_structure', {}), ensure_ascii=False)}")

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

    # 4. メッセージリストを構築
    messages: list[dict] = [{"role": "system", "content": _SYSTEM_PROMPT}]
    messages.append({
        "role": "user",
        "content": (
            f"Context for paper `{material_id}`:\n\n{context_block}\n\n"
            "Please keep the above context in mind when answering my questions."
        ),
    })
    messages.append({
        "role": "assistant",
        "content": "Understood. I will use this context to answer your questions about the paper.",
    })

    for turn in history:
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": message})

    # 5. LLM を呼び出して回答を生成
    return generate_text(messages=messages, temperature=0.3)
