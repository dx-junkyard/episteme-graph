"""StudentGraph — 学生向けリアルタイム対話パイプライン。

ワークフロー:
  QueryAnalyzer (Fast)
    → Retrieval (LLM不使用)
      → [チャンク0件] → 範囲外エラー応答で終了
      → [チャンクあり] → PedagogicalEval (Standard)
        → [誤解 or 複雑な数式] → GenerateDeep (Deep)
        → [通常 or 前提知識誘導] → GenerateStandard (Standard)
    → FormatGuard (Fast)
    → 最終出力
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langgraph.graph import END, StateGraph

from core.graphs.state import StudentState
from core.llm import generate_text, get_llm_params

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ノード実装
# ---------------------------------------------------------------------------


def query_analyzer_node(state: StudentState) -> dict[str, Any]:
    """意図分類と検索キーワード抽出 (Fast モード)。"""
    params = get_llm_params("fast")
    question = state["question"]

    prompt = (
        "あなたは学習支援システムの意図分類器です。\n"
        "以下の学生の質問を分析し、JSON形式で回答してください。\n\n"
        f"質問: {question}\n\n"
        '出力形式 (JSONのみ、他のテキストは不要):\n'
        '{"intent": "<factual|conceptual|misconception|formula|other>", '
        '"search_keywords": ["keyword1", "keyword2", ...]}\n\n'
        "intent の判定基準:\n"
        "- factual: 事実・定義を問う質問\n"
        "- conceptual: 概念の理解・関係性を問う質問\n"
        "- misconception: 誤った理解を含む質問\n"
        "- formula: 数式・導出を求める質問\n"
        "- other: 上記に該当しない質問"
    )

    try:
        result = generate_text(
            messages=[{"role": "user", "content": prompt}],
            model=params["model"],
            reasoning_effort=params["reasoning_effort"],
        )
        parsed = json.loads(result.strip().removeprefix("```json").removesuffix("```").strip())
        return {
            "intent": parsed.get("intent", "other"),
            "search_keywords": parsed.get("search_keywords", [question]),
        }
    except Exception:
        logger.warning("QueryAnalyzer parse failed, falling back to raw question")
        return {
            "intent": "other",
            "search_keywords": [question],
        }


def retrieval_node(state: StudentState) -> dict[str, Any]:
    """ベクトル検索 + グラフ走査 (LLM 不使用)。

    services.search_chunks_with_metadata を内部で呼び出す。
    """
    from services import search_chunks_with_metadata

    question = state["question"]
    relevance_threshold = 0.35
    chunk_results = search_chunks_with_metadata(question, top_k=8)

    above_threshold = [r for r in chunk_results if r["score"] >= relevance_threshold]
    if not above_threshold:
        return {"chunks": [], "no_relevant_chunks": True}

    return {"chunks": above_threshold, "no_relevant_chunks": False}


def _route_after_retrieval(state: StudentState) -> str:
    """Retrieval 結果に応じて分岐する条件関数。"""
    if state.get("no_relevant_chunks", False):
        return "no_chunks"
    return "has_chunks"


def no_chunks_response_node(state: StudentState) -> dict[str, Any]:
    """チャンク0件の場合のエラー応答 (LLM スキップ)。"""
    return {
        "answer": (
            "申し訳ありませんが、ご質問の内容はシステムに登録された教材には"
            "見つかりませんでした。\n\n"
            "教材に含まれていない内容についてはお答えできません。\n"
            "別の表現で質問するか、担当教員にお問い合わせください。"
        ),
        "error": "no_relevant_chunks",
    }


def pedagogical_eval_node(state: StudentState) -> dict[str, Any]:
    """検索結果と学習者状態を評価し、ルーティングを決定 (Standard モード)。"""
    params = get_llm_params("standard")
    question = state["question"]
    intent = state.get("intent", "other")
    chunks = state.get("chunks", [])

    chunk_texts = "\n---\n".join(c["text"] for c in chunks[:5])
    history_summary = ""
    if state.get("history"):
        recent = state["history"][-4:]
        history_summary = "\n".join(
            f"{h['role']}: {h['content'][:200]}" for h in recent
        )

    prompt = (
        "あなたは教育学的評価を行うAIアシスタントです。\n"
        "以下の情報を基に、学生への回答をどの深さで生成すべきか判定してください。\n\n"
        f"■ 学生の質問: {question}\n"
        f"■ 意図分類: {intent}\n"
        f"■ 検索されたチャンク:\n{chunk_texts}\n\n"
        f"■ 直近の会話履歴:\n{history_summary}\n\n"
        '出力形式 (JSONのみ):\n'
        '{"route": "<standard|deep>", "eval_notes": "<判定根拠の短い説明>"}\n\n'
        "判定基準:\n"
        "- deep: 学生が誤解を持っている、または複雑な数式の導出・展開が必要な場合\n"
        "- standard: 通常の解説、前提知識への誘導、1ステップの段階的解説で十分な場合"
    )

    try:
        result = generate_text(
            messages=[{"role": "user", "content": prompt}],
            model=params["model"],
            reasoning_effort=params["reasoning_effort"],
        )
        parsed = json.loads(result.strip().removeprefix("```json").removesuffix("```").strip())
        return {
            "route": parsed.get("route", "standard"),
            "eval_notes": parsed.get("eval_notes", ""),
        }
    except Exception:
        logger.warning("PedagogicalEval parse failed, defaulting to standard")
        return {"route": "standard", "eval_notes": "parse_error: defaulting to standard"}


def _route_after_eval(state: StudentState) -> str:
    """PedagogicalEval の結果に応じて分岐する条件関数。"""
    return state.get("route", "standard")


def _build_generation_context(state: StudentState) -> str:
    """Generate ノード共通のコンテキストブロック構築。"""
    chunks = state.get("chunks", [])
    cited = []
    for r in chunks:
        cited.append(f"[出典: 『{r.get('source_title', '不明')}』]\n{r['text']}")

    return "## 教材から検索された関連箇所（出典付き）\n" + "\n---\n".join(cited)


_GENERATE_SYSTEM_PROMPT = (
    "あなたは大学院レベルの学習を支援するAIチューターです。\n"
    "教材から検索された情報を基に、正確で分かりやすい回答を生成してください。\n\n"
    "回答のルール:\n"
    "- 教材の内容に基づいて回答すること（出典を明示）\n"
    "- 数式は LaTeX 記法（$...$ または $$...$$）を使用すること\n"
    "- 回答の最後に、関連する概念へのドリルダウンリンクを提示すること\n"
    "  形式: [〇〇について詳しく聞く]\n"
    "- 学生の言語に合わせて回答すること\n"
)


def generate_standard_node(state: StudentState) -> dict[str, Any]:
    """通常の回答生成 (Standard モード)。"""
    params = get_llm_params("standard")
    context = _build_generation_context(state)

    messages: list[dict[str, str]] = [
        {"role": "user", "content": (
            f"{_GENERATE_SYSTEM_PROMPT}\n\n"
            f"コース: {state.get('course_title', '')}\n"
            f"トピック: {state.get('topic_title', '')}\n\n"
            f"{context}\n\n"
            "上記を踏まえて、以下の質問に回答してください。\n"
            "前提知識が不足している場合は、1ステップだけ段階的に解説してください。\n\n"
            f"質問: {state['question']}"
        )},
    ]

    for turn in state.get("history", []):
        messages.append({"role": turn["role"], "content": turn["content"]})

    raw = generate_text(
        messages=messages,
        model=params["model"],
        reasoning_effort=params["reasoning_effort"],
    )
    return {"raw_answer": raw}


def generate_deep_node(state: StudentState) -> dict[str, Any]:
    """深い推論を要する回答生成 (Deep モード)。"""
    params = get_llm_params("deep")
    context = _build_generation_context(state)
    eval_notes = state.get("eval_notes", "")

    messages: list[dict[str, str]] = [
        {"role": "user", "content": (
            f"{_GENERATE_SYSTEM_PROMPT}\n\n"
            "【重要】この質問は高度な推論を必要とします。\n"
            f"教育的評価メモ: {eval_notes}\n\n"
            f"コース: {state.get('course_title', '')}\n"
            f"トピック: {state.get('topic_title', '')}\n\n"
            f"{context}\n\n"
            "以下の点に特に注意して回答してください:\n"
            "- 学生の誤解がある場合は、根本から丁寧に訂正すること\n"
            "- 数式の導出は途中の式変形を省略せず、ステップごとに説明すること\n"
            "- 必要に応じて直感的な説明と厳密な定義の両方を提供すること\n\n"
            f"質問: {state['question']}"
        )},
    ]

    for turn in state.get("history", []):
        messages.append({"role": turn["role"], "content": turn["content"]})

    raw = generate_text(
        messages=messages,
        model=params["model"],
        reasoning_effort=params["reasoning_effort"],
    )
    return {"raw_answer": raw}


def format_guard_node(state: StudentState) -> dict[str, Any]:
    """同調ワード除去とフォーマット最終確認 (Fast モード)。"""
    params = get_llm_params("fast")
    raw = state.get("raw_answer", "")

    if not raw:
        return {"answer": raw}

    prompt = (
        "以下のAI回答テキストを校正してください。修正後のテキストのみ出力してください。\n\n"
        "校正ルール:\n"
        "1. 冒頭の同調ワードを除去する（「いい質問ですね」「素晴らしい質問です」"
        "「それは面白い質問ですね」等）\n"
        "2. 出典タグ [出典: 『...』] のフォーマットが統一されていることを確認する\n"
        "3. ドリルダウンリンク [〇〇について詳しく聞く] が末尾にあることを確認する\n"
        "4. 内容自体は変更しないこと\n\n"
        f"--- 回答テキスト ---\n{raw}"
    )

    try:
        answer = generate_text(
            messages=[{"role": "user", "content": prompt}],
            model=params["model"],
            reasoning_effort=params["reasoning_effort"],
        )
        return {"answer": answer}
    except Exception:
        logger.warning("FormatGuard failed, returning raw answer")
        return {"answer": raw}


# ---------------------------------------------------------------------------
# グラフ構築
# ---------------------------------------------------------------------------

def build_student_graph() -> StateGraph:
    """StudentGraph (学生向け対話パイプライン) を構築して返す。"""
    graph = StateGraph(StudentState)

    # ノード登録
    graph.add_node("query_analyzer", query_analyzer_node)
    graph.add_node("retrieval", retrieval_node)
    graph.add_node("no_chunks_response", no_chunks_response_node)
    graph.add_node("pedagogical_eval", pedagogical_eval_node)
    graph.add_node("generate_standard", generate_standard_node)
    graph.add_node("generate_deep", generate_deep_node)
    graph.add_node("format_guard", format_guard_node)

    # エッジ: エントリ → QueryAnalyzer → Retrieval
    graph.set_entry_point("query_analyzer")
    graph.add_edge("query_analyzer", "retrieval")

    # 条件分岐: Retrieval → (no_chunks | has_chunks)
    graph.add_conditional_edges(
        "retrieval",
        _route_after_retrieval,
        {
            "no_chunks": "no_chunks_response",
            "has_chunks": "pedagogical_eval",
        },
    )

    # no_chunks_response → END
    graph.add_edge("no_chunks_response", END)

    # 条件分岐: PedagogicalEval → (standard | deep)
    graph.add_conditional_edges(
        "pedagogical_eval",
        _route_after_eval,
        {
            "standard": "generate_standard",
            "deep": "generate_deep",
        },
    )

    # Generate → FormatGuard → END
    graph.add_edge("generate_standard", "format_guard")
    graph.add_edge("generate_deep", "format_guard")
    graph.add_edge("format_guard", END)

    return graph.compile()
