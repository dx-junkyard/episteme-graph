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

    # --- トピックメタ情報 (オーバービュー用) ---
    topic_concepts: list[str]       # トピックに関連する主要概念名
    topic_prerequisites: list[str]  # トピックの前提知識名

    # --- QueryAnalyzer 出力 ---
    intent: str  # "greeting" | "factual" | "conceptual" | "misconception" | "formula" | "other"
    search_keywords: list[str]

    # --- Retrieval 出力（tier 付与: 仕様書 §3.2）---
    chunks: list[dict[str, Any]]  # [{text, source_title, source_file, score, tier}]
    no_relevant_chunks: bool
    overall_tier: str             # 回答全体の格（最弱根拠に引きずる安全側集約）

    # --- 位置・復帰（仕様書 §3.2 / L2）---
    position_anchor: dict[str, Any]  # {topic_id, segment_id, scroll_offset}
    detour_origin: dict[str, Any]    # DetourStack 単段の入口

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
