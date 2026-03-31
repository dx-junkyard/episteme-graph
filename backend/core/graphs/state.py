"""LangGraph ワークフロー共有ステート定義。"""

from __future__ import annotations

from typing import Any, TypedDict


# ---------------------------------------------------------------------------
# StudentGraph 用ステート
# ---------------------------------------------------------------------------

class StudentState(TypedDict, total=False):
    """学生向け対話パイプラインの状態。"""

    # --- 入力 ---
    question: str
    history: list[dict[str, str]]
    course_id: str
    topic_id: str
    topic_title: str
    course_title: str
    user_id: str

    # --- QueryAnalyzer 出力 ---
    intent: str  # "greeting" | "factual" | "conceptual" | "misconception" | "formula" | "other"
    search_keywords: list[str]

    # --- Retrieval 出力 ---
    chunks: list[dict[str, Any]]  # [{text, source_title, source_file, score}]
    no_relevant_chunks: bool

    # --- PedagogicalEval 出力 ---
    route: str  # "standard" | "deep"
    eval_notes: str  # 判定根拠

    # --- Generate 出力 ---
    raw_answer: str

    # --- FormatGuard 出力 ---
    answer: str

    # --- エラー ---
    error: str


# ---------------------------------------------------------------------------
# TeacherGraph 用ステート
# ---------------------------------------------------------------------------

class TeacherState(TypedDict, total=False):
    """教師向けコース構築パイプラインの状態。"""

    # --- 入力 ---
    pdf_bytes: bytes
    paper_id: str
    filename: str

    # --- DocumentParse 出力 ---
    chunks: list[str]  # パース済みテキストチャンク
    tei_xml: str

    # --- StructureExtraction 出力 ---
    sections: list[dict[str, Any]]  # [{title, keywords, summary}]

    # --- ConceptGraphBuilder 出力 ---
    concept_graph: dict[str, Any]  # {nodes: [...], edges: [...], misconceptions: [...]}

    # --- DiffReviewFormatter 出力 ---
    diff_data: dict[str, Any]  # 教師UIへ返却する差分データ

    # --- エラー ---
    error: str
