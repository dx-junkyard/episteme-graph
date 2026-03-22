"""Episteme Graph – FastAPI application for physics learning chat.

Implements:
- Task 2: Adaptive routing with prerequisite (REQUIRES edge) checking
- Task 3: Hard-limit failsafe when RAG yields no relevant chunks, and
          misconception-aware system prompt
- Task 4: Step-by-step derivation support and parseable "ask more" links
"""

from __future__ import annotations

import logging
import os
import textwrap
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="Episteme Graph", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Configuration (env-driven)
# ---------------------------------------------------------------------------
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.35"))

# ---------------------------------------------------------------------------
# Lazy singletons (initialised on first request)
# ---------------------------------------------------------------------------
_neo4j_driver = None
_vector_store = None


def _get_neo4j_driver():
    global _neo4j_driver
    if _neo4j_driver is None:
        from neo4j import GraphDatabase
        _neo4j_driver = GraphDatabase.driver(
            NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)
        )
    return _neo4j_driver


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant|system)$")
    content: str


class ChatRequest(BaseModel):
    message: str
    course_id: str = ""
    history: list[ChatMessage] = Field(default_factory=list)
    course_data: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Client-side learning state.  Keys are node IDs the user has "
            "already studied / mastered."
        ),
    )


class SuggestLink(BaseModel):
    label: str
    query: str


class ChatResponse(BaseModel):
    answer: str
    sources: list[str] = Field(default_factory=list)
    suggest_links: list[SuggestLink] = Field(default_factory=list)
    prerequisite_prompt: str | None = None


# ---------------------------------------------------------------------------
# System prompt  (Task 3 – misconception-aware + Task 4 – derivation)
# ---------------------------------------------------------------------------

_LEARNING_SYSTEM_PROMPT = textwrap.dedent("""\
    あなたは素粒子物理学・場の量子論を教える大学院レベルの家庭教師です。
    以下の **文献チャンク** を根拠にして、学生の質問に正確かつ丁寧に答えてください。

    ## 回答ルール

    1. **根拠主義**: 提供された文献チャンクに記載のない内容を推測で述べてはいけません。
       文献に十分な情報がない場合は「この点については提供文献に記述がありません」と
       正直に伝えてください。

    2. **誤解の構造的訂正**: 学生の発言に誤解や混同が含まれる場合は、
       単に正解を示すだけでなく、**なぜその誤解が生じやすいか**を構造的に説明してください。
       例:
       - 「数式のこの項（∂_μ）を見落としがちですが、これは共変微分ではなく通常の偏微分です…」
       - 「○○と△△は名前が似ていますが、○○はゲージ場の自己相互作用を表し、
         △△はフェルミオンとの結合を表すので、物理的意味が異なります…」

    3. **数式の導出サポート**: 数式の行間（導出）に関する質問には、
       まず前提となる数学的公式・定理・恒等式を明示し、
       ステップ・バイ・ステップで途中計算を示してください。
       各ステップでは「ここで○○の公式を使います」と根拠を明記してください。

    4. **LaTeX 出力**: 数式は必ず LaTeX 形式（インライン: $...$、
       ディスプレイ: $$...$$）で記述してください。

    5. **深掘りサジェスト**: 回答の末尾に、学生が次に深掘りできるトピックを
       以下の形式で 1〜3 個提示してください:
       ```
       [[ask:〇〇について詳しく聞く]]
       ```
       例: `[[ask:ディラック方程式のローレンツ共変性について詳しく聞く]]`

    ## 文献チャンク
    {chunks}
""")


# ---------------------------------------------------------------------------
# Neo4j helpers
# ---------------------------------------------------------------------------

def _fetch_prerequisites(topic: str) -> list[dict[str, str]]:
    """Return prerequisite nodes for *topic* via REQUIRES edges in Neo4j."""
    driver = _get_neo4j_driver()
    query = textwrap.dedent("""\
        MATCH (t:Entity)-[:REQUIRES]->(pre:Entity)
        WHERE toLower(t.name) CONTAINS toLower($topic)
           OR toLower(t.id) CONTAINS toLower($topic)
        RETURN DISTINCT pre.id AS id, pre.name AS name
        LIMIT 10
    """)
    try:
        with driver.session() as session:
            result = session.run(query, topic=topic)
            return [{"id": r["id"], "name": r["name"]} for r in result]
    except Exception as exc:
        logger.warning("Neo4j prerequisite lookup failed: %s", exc)
        return []


def _check_mastered(prereqs: list[dict[str, str]], course_data: dict) -> list[dict[str, str]]:
    """Return the subset of *prereqs* that the user has NOT yet mastered."""
    mastered: set[str] = set(course_data.get("mastered", []))
    return [p for p in prereqs if p["id"] not in mastered]


# ---------------------------------------------------------------------------
# RAG helpers
# ---------------------------------------------------------------------------

class _ChunkResult(BaseModel):
    text: str
    score: float
    source: str = ""


def _search_relevant_chunks(
    query: str,
    course_id: str,
    top_k: int = 5,
) -> list[_ChunkResult]:
    """Search the vector store for chunks relevant to *query*.

    Returns an empty list when the vector store is not configured.
    """
    # NOTE: Actual vector-store integration (e.g. Qdrant, Chroma, pgvector)
    # is plugged in here.  The function signature is stable; only the body
    # needs to be swapped for the chosen backend.
    try:
        # Placeholder: in production, call the vector DB here.
        # from backend.core.vectorstore import search
        # return search(query, course_id=course_id, top_k=top_k)
        return []
    except Exception as exc:
        logger.error("Vector search failed: %s", exc)
        return []


def _filter_by_threshold(
    chunks: list[_ChunkResult],
    threshold: float = SIMILARITY_THRESHOLD,
) -> list[_ChunkResult]:
    """Drop chunks whose similarity score falls below *threshold*."""
    return [c for c in chunks if c.score >= threshold]


# ---------------------------------------------------------------------------
# LLM call helper
# ---------------------------------------------------------------------------

async def _call_llm(system: str, messages: list[dict[str, str]]) -> str:
    """Call the chat-completion LLM.

    Uses OpenAI-compatible API by default.  Swap this out for any provider.
    """
    try:
        import openai

        client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY)
        resp = await client.chat.completions.create(
            model=os.getenv("LLM_MODEL", "gpt-4o"),
            messages=[{"role": "system", "content": system}, *messages],
            temperature=0.3,
        )
        return resp.choices[0].message.content or ""
    except Exception as exc:
        logger.error("LLM call failed: %s", exc)
        raise HTTPException(status_code=502, detail="LLM service unavailable") from exc


# ---------------------------------------------------------------------------
# Parse suggest links from LLM output
# ---------------------------------------------------------------------------

def _parse_suggest_links(text: str) -> tuple[str, list[SuggestLink]]:
    """Extract ``[[ask:...]]`` tags from *text* and return cleaned text + links."""
    import re

    links: list[SuggestLink] = []
    pattern = r"\[\[ask:(.*?)\]\]"
    for match in re.finditer(pattern, text):
        label = match.group(1).strip()
        links.append(SuggestLink(label=label, query=label))

    cleaned = re.sub(pattern, "", text).rstrip()
    return cleaned, links


# ---------------------------------------------------------------------------
# Main chat endpoint
# ---------------------------------------------------------------------------

@app.post("/api/chat", response_model=ChatResponse)
async def learning_chat(req: ChatRequest):
    """Adaptive learning chat endpoint.

    Flow:
    1. Check prerequisite knowledge via Neo4j REQUIRES edges.
       → If unmastered prerequisites exist, return a counter-question instead
         of a RAG answer (Task 2: Adaptive Routing).
    2. Search vector store for relevant chunks.
       → If no chunks pass the similarity threshold, return a deterministic
         "not in literature" message without calling the LLM
         (Task 3: Hard-limit failsafe).
    3. Build the system prompt with chunks and call the LLM.
       → System prompt enforces misconception-aware correction
         (Task 3) and step-by-step derivation support (Task 4).
    4. Parse ``[[ask:...]]`` suggest links from the LLM response (Task 4).
    """

    user_message = req.message.strip()
    if not user_message:
        raise HTTPException(status_code=400, detail="Empty message")

    # ── Task 2: Prerequisite check via DAG ──────────────────────────────
    prereqs = _fetch_prerequisites(user_message)
    unmastered = _check_mastered(prereqs, req.course_data)

    if unmastered:
        names = "、".join(p["name"] for p in unmastered)
        prerequisite_msg = (
            f"この概念を理解するには、まず **{names}** を押さえる必要があります。\n\n"
        )
        # Ask about each prerequisite
        questions = "\n".join(
            f"- {p['name']} については理解していますか？" for p in unmastered
        )
        prerequisite_msg += questions
        suggest = [
            SuggestLink(label=f"{p['name']}について学ぶ", query=f"{p['name']}について教えてください")
            for p in unmastered
        ]
        return ChatResponse(
            answer=prerequisite_msg,
            prerequisite_prompt=prerequisite_msg,
            suggest_links=suggest,
        )

    # ── Task 3: RAG search with hard-limit failsafe ────────────────────
    raw_chunks = _search_relevant_chunks(user_message, course_id=req.course_id)
    good_chunks = _filter_by_threshold(raw_chunks)

    if not good_chunks:
        return ChatResponse(
            answer=(
                "この点については提供文献に記述がありません。"
                "別の観点から質問を言い換えていただくか、"
                "関連する文献を追加でアップロードしてください。"
            ),
            sources=[],
            suggest_links=[],
        )

    # ── Build prompt with chunks ───────────────────────────────────────
    chunk_text = "\n\n---\n\n".join(
        f"[{i+1}] (score={c.score:.2f}) {c.text}" for i, c in enumerate(good_chunks)
    )
    system_prompt = _LEARNING_SYSTEM_PROMPT.format(chunks=chunk_text)

    messages: list[dict[str, str]] = []
    for h in req.history:
        messages.append({"role": h.role, "content": h.content})
    messages.append({"role": "user", "content": user_message})

    # ── LLM call ───────────────────────────────────────────────────────
    raw_answer = await _call_llm(system_prompt, messages)

    # ── Task 4: Parse suggest links ────────────────────────────────────
    cleaned_answer, suggest_links = _parse_suggest_links(raw_answer)

    sources = list({c.source for c in good_chunks if c.source})

    return ChatResponse(
        answer=cleaned_answer,
        sources=sources,
        suggest_links=suggest_links,
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Serve frontend static files (must be last)
# ---------------------------------------------------------------------------
_frontend_dir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "frontend",
    "public",
)
if os.path.isdir(_frontend_dir):
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="static")
