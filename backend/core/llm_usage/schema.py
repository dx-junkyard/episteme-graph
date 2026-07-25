"""LLM トークン使用量推計（U層）— UsageEvent dataclass と語彙定数の正本。

正本ドキュメント: docs/features/llm_usage_metering_design.md (§3.1, §6.1)
FastAPI には依存しない（開発ルール2 / U4）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- operation 語彙（migration 043 の CHECK 制約と一致させる） ---
OPERATIONS = ("chat", "structured", "embedding", "vision", "transcribe", "tts")

# --- usage_source 語彙（U1: 実測と推計を混ぜて単一の数値にしない。分離集計の単位） ---
USAGE_SOURCES = ("reported", "estimated_tokenizer", "estimated_heuristic")

# 帰属（feature）未設定時の既定値。記録自体は fail-open（消費量を落とさない）。
UNATTRIBUTED = "unattributed"

# feature 語彙（設計書 §6.1 の「文脈セット地点と feature 語彙」表を定数リスト化したもの）。
# CHECK 制約はかけない（enforce しない）— 新機能追加時に計測が壊れる方が害が大きいため。
# あくまで参照用の正本であり、未知の feature を record() が拒否することはない。
KNOWN_FEATURES = (
    UNATTRIBUTED,
    # --- 解析パイプライン（core/document_pipeline/orchestrator.py がステージ単位でセット） ---
    # "pipeline"（無印）は run_id 確定直後の bind_usage_context 直後、最初の
    # report_start("pipeline:{stage}") が呼ばれるまでの一瞬だけ存在しうる暫定値。
    "pipeline",
    "pipeline:grobid_parse",
    "pipeline:document_structure",
    "pipeline:figure_image_extraction",
    "pipeline:source_chunking",
    "pipeline:source_embedding",
    "pipeline:evidence_registry",
    "pipeline:paper_skeleton",
    "pipeline:rhetorical_role",
    "pipeline:claim_qualification",
    "pipeline:claim_object_builder",
    "pipeline:equation_semantics",
    "pipeline:symbol_registry",
    "pipeline:derivation_chain",
    "pipeline:figure_table_semantics",
    "pipeline:apparatus_semantics",
    "pipeline:thesis_reconstruction",
    "pipeline:dsl_linking",
    "pipeline:dsl_embedding",
    "pipeline:component_assembly",
    "pipeline:component_graph",
    "pipeline:narrative_annotator",
    "pipeline:contextual_explanation",
    "pipeline:course_mapping",
    "pipeline:blueprint",
    "pipeline:export_validation",
    "pipeline:persist_claims_components_graph",
    "pipeline:extractor",
    # --- 学習（core/chat.py・音声） ---
    "learning:chat",
    "learning:chat_casual",
    "learning:voice_stt",
    "learning:voice_tts",
    "learning:tension",
    "learning:structure_anchor",
    "learning:understanding_check",
    "learning:help_usage",
    # --- 管理画面（コースビルダー・原稿スタジオ・Admin Copilot・Field Atlas） ---
    "admin:course_builder",
    "admin:lecture_rewrite",
    "admin:lecture_generate",
    "admin:lecture_tts",
    "admin:component_candidates",
    "admin:assistant",
    "admin:reconstruction_authoring",
    "admin:atlas_skeleton",
    "admin:atlas_assist",
    # --- D層（Doubt Layer） ---
    "doubt:scope_candidates",
    "doubt:assumption_normalize",
    # --- W層（Element Deliberation Workspace）。cross_corpus は §4.2 chunk-proxy レンズの
    # embedding 生成（Phase 1）。chat/vision は面③対話（Phase 2、設計書 §0 W9）の
    # 1ターン=1構造化出力コール（core.llm.generate_conversation_turn）。standardization は
    # Phase S 標準化判定 worker（知識ネットワークビジョン §6・三角測量の証拠①LLM 事前知識）。---
    "deliberation:cross_corpus",
    "deliberation:chat",
    "deliberation:vision",
    "deliberation:figure_reanalysis",
    "deliberation:standardization",
    # --- 埋め込み ---
    "embedding:chunks",
    "embedding:library_search",
    # help_kb ベクトル補助層（Phase 3 ①、正本 docs/features/manual_help_kb_design.md
    # §5）。core/help_kb/vector.py::sync_manual_vectors が凍結節を埋め込む際に使う。
    "admin:help_kb_embed",
)


@dataclass
class UsageEvent:
    """1回の LLM / TTS / STT 呼び出しの使用量記録（migration 043 の1行に対応する）。

    id 系の帰属フィールド（user_id / document_id / run_id）はすべて文字列のまま保持する
    （DB 側で CAST するのは recorder の責務。FK は張らない設計 — §5）。
    """

    provider: str
    model: str
    operation: str
    feature: str
    usage_source: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None
    input_characters: int | None = None
    output_characters: int | None = None
    image_count: int = 0
    audio_bytes: int | None = None
    tts_characters: int | None = None
    duration_ms: int | None = None
    success: bool = True
    error_type: str | None = None
    user_id: str | None = None
    document_id: str | None = None
    course_id: str | None = None
    run_id: str | None = None
    metadata: dict = field(default_factory=dict)
