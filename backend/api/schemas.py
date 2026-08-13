"""Episteme Graph — Pydantic リクエスト/レスポンスモデル定義。

main.py から分離した API 固有のスキーマを集約する。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: str
    username: str
    email: str
    role: str = "STUDENT"


class CreateUserRequest(BaseModel):
    """学生または教員アカウント作成リクエスト。"""
    username: str
    email: str
    password: str


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------

class MaterialOut(BaseModel):
    material_id: str
    document_id: str | None = None
    filename: str
    title: str
    status: str  # uploaded | processing | completed | failed
    uploaded_at: str
    chunk_count: int | None = None
    knowledge_graph: dict | None = None
    visibility: str = "private"  # public | group | private
    group_id: str | None = None
    has_pdf: bool = False
    analysis_stage: str | None = None
    analysis_progress: int | None = None
    analysis_processed: int | None = None
    analysis_total: int | None = None
    analysis_error: str | None = None
    # 最新 document_analysis_runs.options の JSONB（例: {"analyze_images": true}）。
    # run が無ければ None。フロントが再解析モーダルで前回選択を復元するための契約
    # フィールド（フィールド名 analysis_options は admin.js との確定契約）。
    analysis_options: dict | None = None
    # --- メタデータ（教材選択UIの情報提示用。documents 列から常時付与）---
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    doc_type: str | None = None
    analyzed_at: str | None = None  # パイプライン解析の完了時刻（直近の生成の並び替え用）
    # --- サマリ（?include=summary 指定時のみ集約。既存テーブルからの読み取りのみ）---
    component_count: int | None = None
    top_components: list[str] = Field(default_factory=list)  # 主要 theory component 名
    top_concepts: list[str] = Field(default_factory=list)  # legacy knowledge_graph 概念名
    section_outline: list[str] = Field(default_factory=list)  # 文書構造のセクション見出し
    has_course: bool = False  # 自分がこの教材からコースを作成済みか（コース未作成リスト用）


class VisibilityUpdateRequest(BaseModel):
    """教材/コースの開示範囲を変更するリクエスト。"""
    visibility: str  # public | group | private
    group_id: str | None = None


# ---------------------------------------------------------------------------
# Learning Course
# ---------------------------------------------------------------------------

class LearningPrerequisite(BaseModel):
    name: str
    status: str = "not_started"  # mastered | partial | not_started


class LearningMisconception(BaseModel):
    label: str = ""
    wrong: str
    correct: str


class LearningTopic(BaseModel):
    id: str
    title: str
    chapter_index: int
    status: str = "locked"  # completed | in_progress | locked
    prerequisites: list[LearningPrerequisite] = Field(default_factory=list)
    misconceptions: list[LearningMisconception] = Field(default_factory=list)
    summary: str = ""
    content: str = ""
    content_blocks: list[dict] = Field(default_factory=list)
    learning_objectives: list[str] = Field(default_factory=list)
    prerequisite_concepts: list[str] = Field(default_factory=list)
    blackbox_policy: dict | None = Field(default_factory=dict)
    assessment_prompts: list[str] = Field(default_factory=list)
    expected_misconceptions: list[str] = Field(default_factory=list)
    linked_component_ids: list[str] = Field(default_factory=list)
    linked_equation_ids: list[str] = Field(default_factory=list)
    source_evidence_ids: list[str] = Field(default_factory=list)
    teaching_takeaways: list[str] = Field(default_factory=list)
    material_chunk_ids: list[str] = Field(default_factory=list)
    source_excerpt: str = ""
    key_concepts: list[str] | None = Field(default_factory=list)
    student_material: dict | None = Field(default_factory=dict)
    spoken_script: str | None = ""
    cautions: list[str] | None = Field(default_factory=list)
    check_questions: list[dict | str] | None = Field(default_factory=list)


class GraphMention(BaseModel):
    element_id: str
    label: str
    element_type: str = "concept"  # concept | relationship | formula | keyword | reference | citation
    surface_text: str = ""
    importance_score: float = 0.5


class ChunkContent(BaseModel):
    id: str
    text: str
    chunk_index: int
    formulas: list[dict] = []
    figures: list[dict] = []  # Phase 4: 図のコース流通 §7.2（figure_id/caption/explanation/image_url）
    # 学習画面向けの読み取り専用 evidence DTO（build_topic_evidence_items）。
    # ``![[component:id]]`` / ``![[claim:id]]`` / ``![[source:id]]`` / ``![[equation:id]]`` /
    # ``![[figure:id]]`` を、そのトピックで公開済みの参照だけから解決するための材料。
    # 管理画面の lsTopicEvidenceItems と同一規則で組み立てる（両画面の解決結果を揃える）。
    evidence_items: list[dict] = []
    chapter: str | None = None
    section: str | None = None
    material_id: str | None = None
    graph_mentions: list[GraphMention] = []


class TopicMaterialResponse(BaseModel):
    topic_id: str
    chunks: list[ChunkContent]


class LearningChapter(BaseModel):
    title: str
    status: str = "locked"  # completed | in_progress | locked
    progress_pct: int = 0


class LearningConcept(BaseModel):
    name: str
    status: str = "future"  # mastered | learning | future
    children: list[str] = []
    expanded: bool = False


class LearningSource(BaseModel):
    title: str
    subtitle: str = ""
    license: str = ""
    used_section: str = ""
    material_id: str = ""  # アップロード教材と紐付ける場合


class LearningReferencedSection(BaseModel):
    source: str
    section: str
    title: str
    note: str = ""


class CourseCreateRequest(BaseModel):
    """コース新規作成リクエスト。"""

    title: str
    chapters: list[LearningChapter] = []
    topics: list[LearningTopic] = []
    concepts: list[LearningConcept] = []
    sources: list[LearningSource] = []
    is_template: bool = False  # Trueの場合、教員作成テンプレートとして扱う
    visibility: str = "private"  # public | group | private
    group_id: str | None = None
    description: str = ""


class CourseUpdateRequest(BaseModel):
    """コース更新リクエスト。部分更新に対応。"""

    title: str | None = None
    chapters: list[LearningChapter] | None = None
    topics: list[LearningTopic] | None = None
    concepts: list[LearningConcept] | None = None
    sources: list[LearningSource] | None = None
    visibility: str | None = None
    group_id: str | None = None
    description: str | None = None
    # M層 Phase 3（llm_model_selection_design.md §6.4）: 場面別コース単位のモデル上書き。
    # キーは scene_key（現状 "learning_chat" のみ対応）。値が null/空文字のキーは
    # 「設定解除」（学習チャットのモデルをシステム既定へ戻す）を意味する。
    # 未指定（None）は「変更しない」。サーバ側でカタログ検証（fail-closed, 422）する。
    llm_models: dict[str, str | None] | None = None
    # discuss 開幕画面の「このコースで議論したいこと」（discuss_opening_authoring_design.md
    # §2 最下段 / Phase 0b）。教員の任意入力で AI 生成は関与しない。空文字は「設定解除」、
    # 未指定（None）は「変更しない」。
    course_focus: str | None = None


class LearningCourseOut(BaseModel):
    id: str
    title: str
    is_template: bool = False
    is_published: bool = False
    is_enrollable: bool = False  # True: 自分未登録の公開テンプレート
    visibility: str = "private"
    group_id: str | None = None
    description: str = ""


class LearningCourseDetail(BaseModel):
    id: str
    title: str
    chapters: list[LearningChapter] = []
    topics: list[LearningTopic] = []
    concepts: list[LearningConcept] = []
    sources: list[LearningSource] = []
    referenced_sections: list[LearningReferencedSection] = []
    course_content_status: dict = Field(default_factory=dict)
    progress: dict | None = None


class PersonalLayer(BaseModel):
    """ユーザー固有の学習レイヤー（Issue #145）。

    マスターコースとは分離して管理される個人データ。
    """
    misconceptions_by_topic: dict = {}
    chat_anchors: dict = {}


class LearningCourseLayeredResponse(BaseModel):
    """レイヤー型コース詳細レスポンス（Issue #145）。

    マスター教材と個人レイヤーを分離して返すことで、
    教材の純粋性を保ちながら個人の学習コンテキストをフロントエンドで管理できる。
    """
    master_course: LearningCourseDetail
    personal_layer: PersonalLayer


class LearningSession(BaseModel):
    date: str
    topic: str
    duration: str


class LearningProgress(BaseModel):
    learning_concepts: int = 0
    misconceptions: int = 0
    streak_days: int = 0
    sessions: list[LearningSession] = []
    # コース完了判定のサーバー正本化: 保存済みの合格トピックと、それから毎回導出する完了状態
    # (services.get_course_completion / calculate_progress)。
    completed_topic_ids: list[str] = Field(default_factory=list)
    course_completed: bool = False


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

class LearningChatRequest(BaseModel):
    message: str
    history: list[dict] = []
    action: str | None = None
    support_action: str | None = None
    support_context: dict | None = None
    position_anchor: dict | None = None  # L2: クライアントの現在位置 {segment_id, scroll_offset}
    message_id: str | None = None  # クライアント生成のメッセージ id。永続履歴・関心痕跡へ焼き込み「この問いに戻る」で該当往復へジャンプさせる
    # 書き直し（機能3）: このメッセージ id 以降（当該 user メッセージ・その回答・以降の往復）を
    # サーバ正本の履歴から削除し、派生 interest_traces を status='superseded' にしてから、
    # message を新しいターンとして同じ位置から再処理する。指定なしなら通常の追記。
    replace_message_id: str | None = None
    # "on_path"(本筋維持) | "explore"(寄り道) | "casual"(気軽に話せる先生) | "discuss"(論文と話す) — 送信時の意図
    intent_mode: str | None = None
    # discuss（論文と話す）専用: 検索スコープ。"course_sources"(既定=このコースのソース論文) |
    # "all_visible"(本人が閲覧可能な周辺資料まで)。該当チャンクが無くても他スコープへ
    # 無断フォールバックしない（DM1）。
    discuss_scope: str | None = None
    # 理解サイクル Phase 2（docs/features/understanding_cycle_design.md §8）: AI モード。
    # "elicit"（解を出さず予測を引き出す問いを返す）| "diff"（予想と骨格の差分観点の候補）。
    # 不正値は 422。未指定は従来どおり。既存 learning_chat の1コール地点に相乗りする
    # （新エンドポイントを作らない・UC10）。
    cycle_mode: str | None = None
    # 分野の地図 (Issue C-2): ↗ アクション由来の構造化ペイロード
    # {node_id, level, skeleton_version, action, node_label, node_status, node_pill,
    #  related?, juxtapose?} — 自由文のみに依存しない
    atlas_context: dict | None = None
    chunk_id: str | None = None
    element_id: str | None = None
    element_type: str | None = None
    element_label: str | None = None
    # 構造帰属（方法A）: 教材区画のテキスト選択→「ここについて質問」の明示アンカー。
    # 選択テキストの逐語と、選択があったセグメント番号（position_anchor とは独立に保持）。
    selection_text: str | None = None
    selection_segment_id: int | None = None
    # UI内コンテキストヘルプ（設計 §4-3）: 「？」ボタン押下時の画面文脈。
    # "lecture" | "chat" | "voice"。HELP ルートの search_manual に screen ヒントとして渡し、
    # front-matter screen: 一致節を検索の第一候補にする。未指定は従来挙動（screen=None）。
    screen_mode: str | None = None
    # インスペクト・モード（設計 docs/features/learning_ui_inspect_hover_design.md §9-1）:
    # トップバー「？」ON 中にホバー（ラッチ）していた UI 論理アンカーID
    # （core.help_kb.ui_anchors.UI_ANCHORS / KNOWN_UI_ANCHOR_IDS のキー）。
    # support_action="usage_help" と併せて送られ、_usage_help_response がマップ済みなら
    # 対応マニュアル節を検索より優先して直接解決する（無ければ従来の search_manual に
    # フォールバック）。痕跡の anchor 値は常に "ui:<ui_anchor>" として記録される。
    ui_anchor: str | None = None


class LearningSupportNextAction(BaseModel):
    type: str
    label: str
    message: str = ""
    target_topic_id: str | None = None


class LearningSupportOriginOut(BaseModel):
    course_id: str
    topic_id: str
    topic_title: str
    chapter_title: str = ""
    segment_id: int = 0      # L2 位置・復帰: 復帰先セグメント
    scroll_offset: int = 0   # L2 位置・復帰: 復帰先スクロール量


class SourceTierItem(BaseModel):
    """回答の根拠1件の格付け（L1 信頼性レイヤー）。"""
    index: int = 0    # 連番出典 [出典N]（本文マーカーと対応）
    chunk_id: str = ""  # 該当チャンク（ポップアップで全文取得）
    source_title: str = ""
    tier: str = "out_of_source"  # approved | source | out_of_source
    score: float = 0.0
    quote: str = ""   # 根拠本文の抜粋（出典カードの引用）
    meta: str = ""    # 出典メタ（ファイル名/節など）
    origin: str = "other_material"  # course_material（このコースの教材）| other_material（別の資料）


class LearningChatResponse(BaseModel):
    answer: str
    course_update: dict | None = None
    support_mode: str | None = None
    status_label: str | None = None
    origin: LearningSupportOriginOut | None = None
    next_actions: list[LearningSupportNextAction] = []
    # --- 学習者体験レイヤー(B層) Stage M ---
    sources: list[SourceTierItem] = []          # L1: 各根拠の tier
    overall_tier: str | None = None             # L1: 回答全体の格（最弱根拠に集約）
    # 回答内容が「教材(このコース)」「別の資料」「出典を追えないモデル生成」のどれに
    # 基づくか。tier（教員承認状況）とは別軸の分類。RAG応答のみ設定（意図分類・前提知識
    # 確認等のシステム応答では None のまま）。
    content_grounding: str | None = None        # course_material | other_material | model_generated
    position_anchor: dict | None = None         # L2: 復帰位置アンカー
    # 分野の地図 (Issue C-3): 「ここから学ぶ」への学習パス提案カード
    # (core.atlas_path.build_learning_path_card の出力。通常チャットでは None)
    atlas_path_card: dict | None = None
    # 構造帰属: この往復で同期記録した明示アンカー（方法A）。学習者バブルのチップ表示用。
    structure_anchor: dict | None = None
    # 構造帰属（方法C）: 回答末尾の1タップ確認プロンプト。tension_hint 等でゲートされた
    # ときのみ設定される（毎回は出さない。P7）。{trace_id, question, options:[{doubt_type,label}]}
    anchor_confirm: dict | None = None
    # 学生 HELP ルート（設計 docs/features/manual_help_kb_design.md §1-3）: docs/manual の
    # 出典（ヒット時のみ設定）。各要素は {file, anchor, title}。既存 sources/tier には
    # 相乗りしない（_TIER_STRENGTH が未知 tier を out_of_source=0 に落とすため）。
    manual_citations: list[dict] | None = None
    mock: bool = False                          # 🚧 mock 由来データを含むか（UI バッジ用）
    # チャット型AI支援の共通基盤整理 §4: LLM 例外時に固定文へ縮退したターンかどうか
    # （I3 会話は死なせない。degraded=true でも 200 を返し、履歴には保存済み）。
    degraded: bool = False


class LearningChatHistoryResponse(BaseModel):
    history: list[dict]


class LearningCheckQuestionRequest(BaseModel):
    """次セクションへ進む前の確認問題への回答。"""
    answer: str
    question: str = ""
    check_question: dict | None = None


class LearningCheckQuestionResponse(BaseModel):
    passed: bool
    feedback: str
    model_answer: str = ""
    answer_requirements: list[str] = Field(default_factory=list)
    explanation: str = ""
    # コース完了判定のサーバー正本化: 合格時は services.record_topic_check_pass の永続化結果、
    # 不合格時は services.get_course_completion の現況（topic_completed=False のまま）。
    topic_completed: bool = False
    course_completed: bool = False
    completed_topic_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Course Builder
# ---------------------------------------------------------------------------

class DeleteConfirmRequest(BaseModel):
    """名前入力による削除確認リクエスト。"""
    confirm_name: str  # 削除対象の名前（一致しなければ拒否）


class CourseBuilderChatRequest(BaseModel):
    """コース構築AIチャットリクエスト。"""
    message: str
    history: list[dict] = []
    session_id: str | None = None
    selected_material_ids: list[str] = []
    # M層 Phase 3（llm_model_selection_design.md §6.2）: この実行だけのモデル上書き
    # （scene "course_builder"）。未指定は従来どおり（user/system policy → env → tier既定）。
    # カタログ外の値は 422（fail-closed）。
    model: str | None = None


class CourseBuilderChatResponse(BaseModel):
    """コース構築AIチャットレスポンス。"""
    answer: str
    course_draft: dict | None = None


class CourseBuilderSessionOut(BaseModel):
    """コース構築セッション情報。"""
    session_id: str
    title: str
    display_name: str | None = None
    source_file_name: str | None = None
    status: str = "draft"
    published_course_id: str | None = None
    created_at: str
    updated_at: str


class CourseBuilderSessionCreate(BaseModel):
    title: str = ""
    source_file_name: str | None = None
    display_name: str | None = None


class CourseBuilderSessionUpdate(BaseModel):
    title: str | None = None
    history: list[dict] | None = None
    course_draft: dict | None = None
    source_file_name: str | None = None
    display_name: str | None = None
    status: str | None = None
    published_course_id: str | None = None


# ---------------------------------------------------------------------------
# Schema Evolution (Issue #36)
# ---------------------------------------------------------------------------

class SchemaTypeOut(BaseModel):
    """OntologyType / Predicate の情報。"""
    id: str
    label: str
    description: str = ""
    is_builtin: bool = False


class SchemaProposalItemOut(BaseModel):
    """スキーマ拡張提案の個別アイテム。"""
    id: str
    item_type: str  # ontology_type | predicate
    key: str
    label: str
    description: str = ""


class SchemaProposalOut(BaseModel):
    """スキーマ拡張提案。"""
    proposal_id: str
    status: str = "pending"  # pending | approved | rejected
    summary: str = ""
    reasoning: str = ""
    source_query_count: int = 0
    items: list[SchemaProposalItemOut] = []
    created_at: str = ""
    reviewed_at: str = ""


class ReextractionJobOut(BaseModel):
    """再抽出ジョブ情報。"""
    job_id: str
    proposal_id: str = ""
    status: str = "pending"  # pending | running | completed | failed
    total_docs: int = 0
    processed_docs: int = 0
    error_message: str | None = None
    started_at: str = ""
    completed_at: str = ""
    created_at: str = ""


class SchemaTypeCreateRequest(BaseModel):
    """OntologyType / Predicate の追加リクエスト。"""
    id: str
    label: str
    description: str = ""


# ---------------------------------------------------------------------------
# Shadow Testing / Simulation (Issue #45)
# ---------------------------------------------------------------------------

class SimulationDocResult(BaseModel):
    """シミュレーション結果（ドキュメント単位）。"""
    doc_id: str
    title: str = ""
    added_concepts: list[dict] = []
    removed_concepts: list[dict] = []
    added_relations: list[dict] = []
    removed_relations: list[dict] = []
    reclassified_nodes: list[dict] = []
    summary: str = ""


class SimulationStats(BaseModel):
    """シミュレーション統計。"""
    target_doc_count: int = 0
    similar_doc_count: int = 0
    control_doc_count: int = 0
    total_added_concepts: int = 0
    total_removed_concepts: int = 0
    total_reclassified_nodes: int = 0


class SimulationResults(BaseModel):
    """シミュレーション結果（カテゴリ別）。"""
    target: list[SimulationDocResult] = []
    similar: list[SimulationDocResult] = []
    control: list[SimulationDocResult] = []


class SimulationResponse(BaseModel):
    """シミュレーションAPIレスポンス。"""
    proposal_id: str
    proposal_summary: str = ""
    proposal_items: list[SchemaProposalItemOut] = []
    results: SimulationResults = SimulationResults()
    stats: SimulationStats = SimulationStats()


class ApproveWithScopeRequest(BaseModel):
    """スコープ付き承認リクエスト。"""
    scope: str = "full"  # "full" or "canary"
    course_ids: list[str] = []


# ---------------------------------------------------------------------------
# Background Tasks (Issue #63)
# ---------------------------------------------------------------------------

class BackgroundTaskOut(BaseModel):
    """バックグラウンドタスクのステータス情報。"""
    task_id: str
    task_type: str = "material_processing"
    status: str = "pending"  # pending | processing | completed | failed
    result_data: dict | None = None
    error_message: str | None = None
    created_at: str = ""
    updated_at: str = ""


# ---------------------------------------------------------------------------
# Interactive Lecture Mode (Issue #66)
# ---------------------------------------------------------------------------

class LectureFormulaItem(BaseModel):
    """チャンク内の数式メタデータ。"""
    id: str  # [[FORMULA_0]], [[FORMULA_1]], ...
    latex: str
    spoken: str  # 音声読み上げ用テキスト
    is_display: bool = False  # True: ブロック数式（独立行）, False: インライン数式
    label: str | None = None  # 論文中の式番号（例: 1.1）
    block_id: str | None = None
    source_image: dict | None = None  # EquationSemanticAgent の bbox crop 表示用
    source_location: dict | None = None
    needs_math_review: bool = False
    review_reason: list[str] = Field(default_factory=list)


class LectureFigureItem(BaseModel):
    """スライド/セグメント内の図メタデータ（Phase 4: 図のコース流通 §7.2）。

    ``![[figure:<figure_id>]]`` 埋め込みが ``core.lecture.resolve_figure_embeds`` で
    ``[[FIGURE_N]]`` プレースホルダーに解決された際に生成される（DB には保存しない）。
    """
    id: str  # [[FIGURE_1]], [[FIGURE_2]], ...
    figure_id: str  # document_figures.id (UUID)
    caption: str | None = None
    explanation: str | None = None  # v1 は常に None（後続エージェントが配線する）
    image_url: str | None = None


class LectureSlide(BaseModel):
    """レクチャーセグメント内の1スライド（migration 040: レクチャースライド同期 Phase 1）。

    表示 (display_text) と読み上げ (spoken_text) の同期最小単位。既定では
    1セグメント（チャンク）= 1スライドだが、``===`` マーカーで複数スライドに分割できる
    （``core.lecture.split_slides`` が導出。DB には保存しない）。
    """
    slide_index: int
    display_text: str
    spoken_text: str | None = None
    formulas: list[LectureFormulaItem] = []
    figures: list[LectureFigureItem] = []
    has_audio: bool = False
    duration_ms: int = 0


class LectureSegment(BaseModel):
    """レクチャーモードの1セグメント（チャンク単位）。"""
    chunk_id: str
    chunk_index: int
    text: str
    spoken_text: str
    formulas: list[LectureFormulaItem] = []
    figures: list[LectureFigureItem] = []
    has_audio: bool = False
    duration_ms: int = 0
    segment_mode: str = "full"  # full | summary | skip
    slides: list[LectureSlide] = []
    language: str = "ja"  # このセグメントの spoken_language（無指定は "ja"）


class LectureSequenceResponse(BaseModel):
    """レクチャーシーケンス API レスポンス。"""
    course_id: str
    topic_id: str
    segments: list[LectureSegment] = []
    total_segments: int = 0
    total_duration_ms: int = 0
    skipped_segments: int = 0  # 習得済みスキップ数
    summary_segments: int = 0  # 簡易版変換数
    total_slides: int = 0  # 全セグメントのスライド数合計


class LectureTTSRequest(BaseModel):
    """TTS 音声生成リクエスト。"""
    chunk_id: str
    voice: str = "alloy"
    slide_index: int = 0


class LectureTTSResponse(BaseModel):
    """TTS 音声生成レスポンス。"""
    chunk_id: str
    audio_base64: str
    duration_ms: int = 0
    word_timestamps: list[dict] = []
    content_type: str = "audio/mp3"


class LectureInterruptRequest(BaseModel):
    """レクチャー中断チャットリクエスト。"""
    message: str
    current_chunk_id: str
    pause_position_ms: int = 0
    history: list[dict] = []
    current_slide_index: int | None = None  # 記録用。挙動には影響しない


class LectureInterruptResponse(BaseModel):
    """レクチャー中断チャットレスポンス。"""
    answer: str
    resume_chunk_id: str
    resume_position_ms: int = 0
    course_update: dict | None = None
    next_actions: list[LearningSupportNextAction] = []


# ---------------------------------------------------------------------------
# Lecture Script Studio (Issue #70)
# ---------------------------------------------------------------------------

class LectureScriptChunkOut(BaseModel):
    """チャンク単位のレクチャースクリプト情報。"""
    chunk_id: str
    chunk_index: int
    text: str
    raw_text: str = ""
    display_text: str = ""
    spoken_text: str = ""
    formulas: list[LectureFormulaItem] = []
    status: str = "ungenerated"  # ungenerated | generated | edited | audio_ready
    material_id: str = ""
    document_id: str = ""
    page_start: int | None = None
    page_end: int | None = None
    section_id: str = ""
    section_title: str = ""
    section_level: int = 0
    section_order: int = 0
    pdf_url: str | None = None
    smiles_dsl: str = ""
    variables: dict | list | None = None
    ancestors: list | None = None
    graph_elements: list[dict] = []
    # レクチャースライド同期 + 音声言語切替 (migration 040 Phase 4)
    spoken_language: str | None = None  # 原稿の生成言語。None/未生成時は "ja" とみなす
    slide_count: int = 1  # split_slides() でのスライド枚数
    slide_mismatch: bool = False  # 表示/読み上げの区切り数不一致で1枚に縮退したか
    audio_ready_slides: int = 0  # 音声キャッシュ済みスライド数


class LectureScriptGenerateRequest(BaseModel):
    """バッチスクリプト生成リクエスト。"""
    override: bool = False  # 既存スクリプトを上書きするか
    auto_audio: bool = False  # スクリプト生成完了後、自動で音声生成タスクを起動するか (Issue #139)
    language: Literal["ja", "en"] | None = None  # 省略時はコース設定 (lecture_language) を使う
    # M層 Phase 3（llm_model_selection_design.md §6.3）: この実行だけのモデル上書き
    # （scene "lecture_studio"）。未指定は従来どおり fast tier。カタログ外は 422。
    model: str | None = None


class LectureScriptGenerateStartResponse(BaseModel):
    """バッチスクリプト生成開始レスポンス（非同期）。"""
    task_id: str
    course_id: str
    total_chunks: int = 0
    status: str = "pending"


class LectureScriptGenerateResponse(BaseModel):
    """バッチスクリプト生成レスポンス（後方互換）。"""
    course_id: str
    total_chunks: int = 0
    generated: int = 0
    skipped: int = 0
    chunks: list[LectureScriptChunkOut] = []


class LectureScriptSaveRequest(BaseModel):
    """手動スクリプト保存リクエスト。"""
    spoken_text: str
    display_text: str | None = None
    formulas: list[dict] = []


class LectureScriptSaveResponse(BaseModel):
    """手動スクリプト保存レスポンス。"""
    chunk_id: str
    status: str = "edited"


class LecturePreviewSplitRequest(BaseModel):
    """原稿スタジオのプレビュー用スライド分割リクエスト（DB 非変更, Tier2-11）。

    ``core.lecture.split_slides`` をそのまま呼ぶための入力。未保存の編集途中テキストも
    そのまま渡せる（保存済みチャンクである必要はない）。
    """
    display_text: str = ""
    spoken_text: str | None = None
    formulas: list[dict] = []


class LecturePreviewSplitResponse(BaseModel):
    """原稿スタジオのプレビュー用スライド分割レスポンス。

    ``core.lecture.split_slides`` の結果をそのまま返す。プレビュー（本レスポンス）と
    配信（``get_lecture_sequence`` 等）が同一の分割ロジックを共有するための唯一の経路
    （docs/features/lecture_slide_sync_design.md「プレビューと配信で分割ロジックを
    共有すること」）。``display_segment_count``/``spoken_segment_count`` は
    整合インジケータ表示専用の補助情報（``mismatch=True`` の場合 ``slides`` は1件に
    縮退するため、縮退前のセグメント数をクライアントに渡す）。
    """
    slides: list[LectureSlide] = []
    mismatch: bool = False
    display_segment_count: int = 0
    spoken_segment_count: int = 0


class LectureScriptRewriteRequest(BaseModel):
    """AI スクリプト書き換えリクエスト。"""
    prompt: str
    narration_persona: str | None = None
    studio_view: str = "edit"
    theory_components: list[dict] = Field(default_factory=list)
    # M層 Phase 3（llm_model_selection_design.md §6.3）: この実行だけのモデル上書き
    # （scene "lecture_studio"）。未指定は従来どおり fast tier。カタログ外は 422。
    model: str | None = None


class LectureScriptRewriteResponse(BaseModel):
    """AI スクリプト書き換えレスポンス。"""
    chunk_id: str
    display_text: str = ""
    spoken_text: str
    formulas: list[LectureFormulaItem] = []
    theory_components: list[dict] = Field(default_factory=list)


class LectureAudioGenerateRequest(BaseModel):
    """バッチ音声生成リクエスト (migration 040 Phase 4: 言語切替)。"""
    language: Literal["ja", "en"] | None = None  # 省略時はコース設定 (lecture_language) を使う


class LectureAudioGenerateResponse(BaseModel):
    """バッチ音声生成レスポンス（後方互換）。"""
    course_id: str
    total_chunks: int = 0
    generated: int = 0
    skipped: int = 0
    errors: int = 0


class LectureAudioGenerateStartResponse(BaseModel):
    """バッチ音声生成開始レスポンス（非同期）。"""
    task_id: str
    course_id: str
    total_chunks: int = 0
    status: str = "pending"


# ---------------------------------------------------------------------------
# Theory Components for Lecture Studio
# ---------------------------------------------------------------------------

class TheorySourceRef(BaseModel):
    chunk_id: str
    page_start: int | None = None
    page_end: int | None = None
    quote: str = ""


class TheorySourceScope(BaseModel):
    level: str = "chunk"  # chunk | section | paper
    document_id: str = ""
    section_id: str = ""
    section_title: str = ""
    section_level: int = 0
    section_order: int = 0
    chunk_id: str = ""
    chunks: list[str] = Field(default_factory=list)
    pages: list[int] = Field(default_factory=list)
    equations: list[str] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)


class TheoryConceptItem(BaseModel):
    name: str
    concept_type: str = "Concept"
    raw: str = ""
    normalized: str = ""
    canonical: str = ""
    normalization_source: str = ""


class ClaimOut(BaseModel):
    claim_id: str
    document_id: str = ""
    source_scope: TheorySourceScope = Field(default_factory=TheorySourceScope)
    claim_type: str = "diagnostic_claim"
    text: str
    normalized_text: str = ""
    concepts: list[TheoryConceptItem] = Field(default_factory=list)
    equation: dict = Field(default_factory=dict)
    support_status: str = "source_backed"
    evidence_text: str = ""
    review_status: str = "teacher_review_required"
    created_by: str | None = None
    created_at: str = ""
    updated_at: str = ""


class ClaimUpsertRequest(BaseModel):
    claim_type: str = "diagnostic_claim"
    text: str
    normalized_text: str = ""
    concepts: list[TheoryConceptItem] = Field(default_factory=list)
    equation: dict = Field(default_factory=dict)
    support_status: str = "source_backed"
    evidence_text: str = ""
    review_status: str = "teacher_review_required"
    source_scope: TheorySourceScope | None = None


class ClaimExtractResponse(BaseModel):
    chunk_id: str
    claims: list[ClaimOut] = Field(default_factory=list)
    chunk_role: str = "unknown"
    skip_reason: str = ""


class TheoryIOItem(BaseModel):
    label: str
    name: str = ""
    type: str = "Concept"
    concept_type: str = ""
    required: bool = True
    description: str = ""
    support_status: str = "source_backed"
    evidence_claims: list[str] = Field(default_factory=list)
    source_refs: list[TheorySourceRef] = Field(default_factory=list)
    needs_source: bool = False


class TheoryConditionItem(BaseModel):
    label: str
    condition: str = ""
    description: str = ""
    support_status: str = "source_backed"
    evidence_claims: list[str] = Field(default_factory=list)
    source_refs: list[TheorySourceRef] = Field(default_factory=list)
    needs_source: bool = False


class TheoryBlackboxPolicy(BaseModel):
    default_level: str = "summary"
    expand_if_unlearned: bool = True
    io_summary: str = ""
    requires_source_display: bool = True


class TheoryComponentOut(BaseModel):
    id: str
    course_id: str
    primary_chunk_id: str | None = None
    name: str
    component_type: str = "theory"
    domain: str = "particle_physics"
    origin: str = "paper"
    summary: str = ""
    status: str = "candidate"
    source_scope: TheorySourceScope = Field(default_factory=TheorySourceScope)
    evidence_claims: list[str] = Field(default_factory=list)
    maturity_level: str = "paper_claim"
    maturity_source: str = "llm_proposed"
    review_status: str = "teacher_review_required"
    source_chunks: list[TheorySourceRef] = Field(default_factory=list)
    inputs: list[TheoryIOItem] = Field(default_factory=list)
    outputs: list[TheoryIOItem] = Field(default_factory=list)
    preconditions: list[TheoryConditionItem] = Field(default_factory=list)
    cautions: list[TheoryConditionItem] = Field(default_factory=list)
    constraints: list[TheoryConditionItem] = Field(default_factory=list)
    invalid_conditions: list[TheoryConditionItem] = Field(default_factory=list)
    dependencies: list[TheoryConditionItem] = Field(default_factory=list)
    connectors: dict = Field(default_factory=dict)
    component_type_text: str = ""
    internal_flow: list[dict] = Field(default_factory=list)
    linked_equation_ids: list[str] = Field(default_factory=list)
    input_equation_ids: list[str] = Field(default_factory=list)
    intermediate_equation_ids: list[str] = Field(default_factory=list)
    output_equation_ids: list[str] = Field(default_factory=list)
    constraint_equation_ids: list[str] = Field(default_factory=list)
    definition_equation_ids: list[str] = Field(default_factory=list)
    review_required_equation_ids: list[str] = Field(default_factory=list)
    eliminated_symbols: list[str] = Field(default_factory=list)
    retained_symbols: list[str] = Field(default_factory=list)
    equation_confidence_summary: dict = Field(default_factory=dict)
    blackbox_policy: TheoryBlackboxPolicy = Field(default_factory=TheoryBlackboxPolicy)
    validation_warnings: list[dict] = Field(default_factory=list)
    duplicate_candidates: list[dict] = Field(default_factory=list)
    teacher_notes: str = ""
    created_at: str = ""
    updated_at: str = ""


class TheoryComponentUpsertRequest(BaseModel):
    name: str
    component_type: str = "theory"
    domain: str = "particle_physics"
    origin: str = "paper"
    summary: str = ""
    status: str = "candidate"
    source_scope: TheorySourceScope = Field(default_factory=TheorySourceScope)
    evidence_claims: list[str] = Field(default_factory=list)
    maturity_level: str = "paper_claim"
    maturity_source: str = "llm_proposed"
    review_status: str = "teacher_review_required"
    source_chunks: list[TheorySourceRef] = Field(default_factory=list)
    inputs: list[TheoryIOItem] = Field(default_factory=list)
    outputs: list[TheoryIOItem] = Field(default_factory=list)
    preconditions: list[TheoryConditionItem] = Field(default_factory=list)
    cautions: list[TheoryConditionItem] = Field(default_factory=list)
    constraints: list[TheoryConditionItem] = Field(default_factory=list)
    invalid_conditions: list[TheoryConditionItem] = Field(default_factory=list)
    dependencies: list[TheoryConditionItem] = Field(default_factory=list)
    connectors: dict = Field(default_factory=dict)
    internal_flow: list[dict] = Field(default_factory=list)
    duplicate_candidates: list[dict] = Field(default_factory=list)
    blackbox_policy: TheoryBlackboxPolicy = Field(default_factory=TheoryBlackboxPolicy)
    teacher_notes: str = ""


class TheoryComponentExtractRequest(BaseModel):
    force: bool = False
    use_llm: bool = True


class TheoryComponentExtractResponse(BaseModel):
    chunk_id: str
    components: list[TheoryComponentOut] = Field(default_factory=list)


class TheoryConnectionValidateRequest(BaseModel):
    source_component_id: str
    target_component_id: str


class ComponentAssembleRequest(BaseModel):
    force: bool = False


class ComponentAssembleResponse(BaseModel):
    section_id: str
    components: list[TheoryComponentOut] = Field(default_factory=list)


class ComponentGraphNode(BaseModel):
    component_id: str
    label: str
    review_status: str = "teacher_review_required"
    display_order: int = 0
    origin: str = "paper"
    component_type: str = ""
    component_type_text: str = ""
    summary: str = ""
    operation: str = ""
    theory_object: str = ""
    display_label: str = ""
    representative_component_id: str = ""
    linked_component_ids: list[str] = Field(default_factory=list)
    detail_node_ids: list[str] = Field(default_factory=list)
    supporting_derivation_ids: list[str] = Field(default_factory=list)
    # Longer, human-readable explanation (atomic-claim text / step reason). The
    # ``label`` stays a short theory-stage name for main nodes; the descriptive
    # sentence lives here and is shown in the UI detail pane (issue #308/#319).
    description: str = ""
    graph_layer: str = "main"
    maturity_source: str = ""
    publish_ready: bool = False
    input_equation_ids: list[str] = Field(default_factory=list)
    intermediate_equation_ids: list[str] = Field(default_factory=list)
    output_equation_ids: list[str] = Field(default_factory=list)
    definition_equation_ids: list[str] = Field(default_factory=list)
    constraint_equation_ids: list[str] = Field(default_factory=list)
    review_required_equation_ids: list[str] = Field(default_factory=list)
    eliminated_symbols: list[str] = Field(default_factory=list)
    retained_symbols: list[str] = Field(default_factory=list)
    derivation_operations: list[str] = Field(default_factory=list)
    # Source-backing links and status (issue #302).
    linked_equation_ids: list[str] = Field(default_factory=list)
    linked_derivation_ids: list[str] = Field(default_factory=list)
    linked_claim_ids: list[str] = Field(default_factory=list)
    linked_evidence_ids: list[str] = Field(default_factory=list)
    source_backing_status: str = ""
    review_reasons: list[str] = Field(default_factory=list)
    # Layer linkage between main TheoryOperationNode and equation_detail nodes (issue #306).
    parent_component_id: str = ""
    member_component_ids: list[str] = Field(default_factory=list)
    # Short graph-display label (issue #337).
    visual_label: str = ""
    # Claim I/O from derivation step (issue #337).
    input_claim_ids: list[str] = Field(default_factory=list)
    output_claim_ids: list[str] = Field(default_factory=list)
    # Precondition claims required but not consumed (issue #337).
    required_claim_ids: list[str] = Field(default_factory=list)
    # Extraction/review note text (issue #337).
    review_reason: str = ""
    # Issue #449: true when this node is a thesis traversal anchor (the goal of
    # the argument). Mirrors DSLNode.is_thesis_anchor; the UI emphasises anchors
    # via this flag rather than ID string matching (#443 is separate).
    is_thesis_anchor: bool = False


class ComponentGraphEdge(BaseModel):
    source_component_id: str
    target_component_id: str
    relation: str = "RELATED_TO"
    edge_type: str = "explicit_connector"
    confidence: float = 0.5
    support_status: str = "design_inferred"
    # Same backing vocabulary as nodes (issue #311 criterion 6).
    source_backing_status: str = ""
    review_status: str = "review_required"
    review_reasons: list[str] = Field(default_factory=list)
    evidence: dict = Field(default_factory=dict)
    # Issue #451: relation polarity ("+" / "-" / "" non-polar). Mirrors
    # DSLEdge.polarity; the UI visualises polarity from this field, not the label.
    polarity: str = ""


class ComponentGraphResponse(BaseModel):
    graph_id: str
    document_id: str
    scope: dict = Field(default_factory=lambda: {"level": "paper"})
    nodes: list[ComponentGraphNode] = Field(default_factory=list)
    edges: list[ComponentGraphEdge] = Field(default_factory=list)
    validation_results: list[dict] = Field(default_factory=list)
    # NarrativeAnnotator reading layer (issue #360): {graph_summary,
    # maturity_source, node_narratives: {component_id: {...}},
    # edge_narratives: {edge_id: {...}}}. Annotation only — never part of
    # nodes / edges.
    narrative: dict = Field(default_factory=dict)
    # Reference index for evidence-link resolution: node/edge payloads carry
    # pipeline-internal IDs (atomic claim IDs, ev_NNNN evidence IDs,
    # derivation/step IDs) that have no DB table of their own and cannot be
    # resolved by the frontend on its own. This maps only the referenced IDs
    # (never the whole stage_outputs) to a short human-readable snippet:
    # {"claims": {id: {"claim_id", "text"}}, "evidence": {id: {"text",
    # "block_id"}}, "derivations": {id: {"label", "kind", "operation"}}}.
    reference_index: dict = Field(default_factory=dict)


class LectureStudioSettings(BaseModel):
    """原稿スタジオのコース単位設定。"""
    narration_persona: str = ""
    response_persona: str = ""
    # レクチャースライド同期 + 音声言語切替 (migration 040 Phase 4): コース単位の読み上げ言語。
    # 不正値は pydantic のバリデーションで 422 になる。
    # 省略時は None（PUT では「変更しない」を意味し、前回保存済みの言語を保持する —
    # 旧 default="ja" だと省略が明示的な "ja" 指定と区別できず、口調のみの設定保存で
    # 既存の言語設定を無警告で ja に巻き戻していた）。GET レスポンスは常に解決済みの
    # ja/en を返す（routes 側で _normalize_lecture_language により埋める）。
    lecture_language: Literal["ja", "en"] | None = None


# ---------------------------------------------------------------------------
# Groups & Visibility (Issue #121)
# ---------------------------------------------------------------------------

class GroupCreateRequest(BaseModel):
    """グループ作成リクエスト。"""
    name: str
    description: str = ""


class GroupUpdateRequest(BaseModel):
    """グループ更新リクエスト。"""
    name: str | None = None
    description: str | None = None


class GroupMemberOut(BaseModel):
    """グループ所属メンバー情報。"""
    user_id: str
    username: str
    email: str = ""
    role: str = "member"  # admin | member
    joined_at: str = ""


class GroupOut(BaseModel):
    """グループ情報。"""
    id: str
    name: str
    description: str = ""
    invite_code: str | None = None  # admin のみ閲覧可能
    created_by: str
    my_role: str = "member"  # 現在ユーザーのロール
    member_count: int = 0
    created_at: str = ""
    updated_at: str = ""


class GroupDetailOut(GroupOut):
    """グループ詳細（メンバーリスト含む）。"""
    members: list[GroupMemberOut] = []


class GroupInviteByUserRequest(BaseModel):
    """特定ユーザーに対する直接招待リクエスト。"""
    # username または email のいずれかで識別
    username: str | None = None
    email: str | None = None


class GroupInviteByCodeRequest(BaseModel):
    """招待コードによる参加リクエスト。"""
    invite_code: str


class GroupInvitationOut(BaseModel):
    """招待情報。"""
    id: str
    group_id: str
    group_name: str = ""
    invitee_user_id: str
    invitee_username: str = ""
    inviter_user_id: str
    inviter_username: str = ""
    status: str = "pending"  # pending | accepted | declined | revoked
    created_at: str = ""
    responded_at: str = ""


# ---------------------------------------------------------------------------
# Course-Group Permissions (Issue #125)
# ---------------------------------------------------------------------------

class CourseGroupPermissionOut(BaseModel):
    """コースに紐づくグループ権限。"""
    course_id: str
    group_id: str
    group_name: str = ""
    permission: str = "viewer"  # viewer | editor
    created_at: str = ""
    updated_at: str = ""


class CourseGroupPermissionUpsertRequest(BaseModel):
    """コースにグループ権限マッピングを追加/更新するリクエスト。"""
    group_id: str
    permission: str = "viewer"  # viewer | editor


class DocumentGroupPermissionOut(BaseModel):
    """ドキュメント（教材・パイプライン成果）に紐づくグループ権限（migration 035）。"""
    document_id: str
    group_id: str
    group_name: str = ""
    permission: str = "viewer"  # viewer | editor
    created_at: str = ""
    updated_at: str = ""


class DocumentGroupPermissionUpsertRequest(BaseModel):
    """ドキュメントにグループ権限マッピングを追加/更新するリクエスト。"""
    group_id: str
    permission: str = "viewer"  # viewer | editor


# ---------------------------------------------------------------------------
# 横断ユーティリティ層（Admin Copilot, migration 034）
# ---------------------------------------------------------------------------

class AssistantScreenContext(BaseModel):
    """フロントの collectScreenContext() が渡す構造化状態（DOM スクレイプではない）。"""
    tab: str = ""
    selection: dict = Field(default_factory=dict)
    visible_entities: list = Field(default_factory=list)


class AssistantChatRequest(BaseModel):
    """POST /api/admin/assistant/chat のリクエスト。"""
    message: str
    history: list[dict] = Field(default_factory=list)
    session_id: str | None = None
    screen_context: AssistantScreenContext = Field(default_factory=AssistantScreenContext)
    # 管理画面インスペクト・モード（❓ 使い方）から送られる usage_help 分岐用（後方互換の
    # 追加フィールド。省略時は既存の意図分類チャットとして扱う）。
    support_action: str | None = None
    # インスペクト・モードでラッチされていた UI 論理アンカー（core/help_kb/admin_ui_anchors.py
    # のキー）。support_action="usage_help" のときのみ参照する。
    ui_anchor: str | None = None


class AssistantActionPlan(BaseModel):
    """intent=action のとき返す実行計画（未実行。フロントが §8.2 を叩く）。"""
    capability_id: str
    title: str = ""
    target: dict = Field(default_factory=dict)
    args: dict = Field(default_factory=dict)
    reversible: bool = True
    confirm_required: bool = False
    supported: bool = True  # 代行 handler が存在するか（P1 段階登録）


class AssistantLocateStep(BaseModel):
    screen: str
    anchor_id: str
    hint: str = ""
    precondition: str | None = None


class AssistantLocatePlan(BaseModel):
    """intent=locate のとき返す点灯手順（§5.3）。DB 非変更・監査対象外。"""
    capability_id: str
    steps: list[AssistantLocateStep] = Field(default_factory=list)


class AssistantChatResponse(BaseModel):
    """POST /api/admin/assistant/chat のレスポンス。"""
    answer: str
    intent: str                                  # guidance | locate | action | clarify | status_query
    action_plan: AssistantActionPlan | None = None
    locate_plan: AssistantLocatePlan | None = None
    next_actions: list[dict] = Field(default_factory=list)
    citations: list[dict] = Field(default_factory=list)
    source: str = "heuristic"                     # 分類の由来（llm | heuristic）


class AssistantActionRequest(BaseModel):
    """POST /api/admin/assistant/actions のリクエスト（代行実行）。"""
    capability_id: str
    target: dict = Field(default_factory=dict)    # {"type": "...", "id": "..."}
    args: dict = Field(default_factory=dict)
    session_id: str | None = None
    confirm: bool = False                          # reversible=False は True 必須（P2）


class AssistantActionResponse(BaseModel):
    action_id: str
    status: str                                    # applied | confirm_pending | failed
    capability_id: str
    reversible: bool
    after: dict | None = None
    message: str = ""


class AssistantRevertResponse(BaseModel):
    action_id: str
    status: str                                    # reverted
    restored: dict | None = None
    message: str = ""


class AssistantActionSummary(BaseModel):
    """GET /api/admin/assistant/actions の 1 行（戻す履歴の復元用）。"""
    action_id: str
    capability_id: str
    screen: str = ""
    target_type: str = ""
    target_id: str | None = None
    reversible: bool = True
    status: str = "applied"
    created_at: str = ""
    reverted_at: str | None = None


class AssistantCapabilityOut(BaseModel):
    """GET /api/admin/assistant/capabilities の 1 行（実行可否の事前開示, N12）。

    `executable` = kind=action かつ代行ハンドラ実装済み。False の action は
    「道案内のみ対応」としてフロントが明示する。
    """
    id: str
    screen: str
    title: str
    required_role: str
    kind: str                                      # guidance_only | action
    reversible: bool = True
    confirm: bool = False
    description: str = ""
    executable: bool = False


class NextStepOut(BaseModel):
    """G層 Next Steps の 1 項目（core.admin_assistant.next_steps.NextStep の JSON 化）。"""
    step_key: str
    rule_id: str
    severity: str                                  # required | recommended | optional
    title: str
    reason: str
    capability_id: str
    locate_plan: dict = Field(default_factory=dict)
    target: dict = Field(default_factory=dict)
    dismissible: bool = True


class NextStepsResponse(BaseModel):
    """GET /api/admin/assistant/next-steps のレスポンス（設計 §4）。"""
    steps: list[NextStepOut] = Field(default_factory=list)
    hidden: list[NextStepOut] = Field(default_factory=list)
    truncated: bool = False
    assistant_cue_pending: bool = False


class NextStepDismissResponse(BaseModel):
    """POST /next-steps/{step_key}/dismiss|restore の共通レスポンス。"""
    status: str                                     # dismissed | restored
    step_key: str


class HelpKbRefreshResponse(BaseModel):
    """POST /api/admin/help-kb/refresh のレスポンス（設計 §2-2）。

    数値は節数・違反数などの事実のみ（confidence 等の生値は含めない）。
    """
    status: str = "refreshed"
    audience_section_counts: dict[str, int] = Field(default_factory=dict)
    validator_violations: int = 0
    excluded_sections: int = 0


# ---------------------------------------------------------------------------
# help_kb Phase 3: DB draft/freeze（設計 §7-2、atlas/library の draft/freeze 踏襲）
# ---------------------------------------------------------------------------


class HelpKbDraftOut(BaseModel):
    """``manual_kb_drafts`` の1行（``audience`` + ``file`` で一意）。"""
    audience: str
    file: str
    content: str
    revision: int
    updated_at: str = ""
    updated_by: Optional[str] = None


class HelpKbStateOut(BaseModel):
    """``manual_kb_state``（単一行）。配信ソースの既定は ``files``。"""
    serving_source: str = "files"
    active_version_no: Optional[int] = None
    updated_at: str = ""


class HelpKbDraftsResponse(BaseModel):
    drafts: list[HelpKbDraftOut] = Field(default_factory=list)
    state: HelpKbStateOut = Field(default_factory=HelpKbStateOut)


class HelpKbDraftUpdateRequest(BaseModel):
    content: str
    expected_revision: int


class HelpKbSeedResponse(BaseModel):
    """draft シード結果（``core/revision_store.py::idempotent_seed_import`` の戻り値）。"""
    imported: int = 0
    skipped: int = 0
    errors: list[str] = Field(default_factory=list)


class HelpKbFreezeResponse(BaseModel):
    """凍結成功時のレスポンス。凍結検証ゲート不通過時は 422 + violations（HTTPException detail）。"""
    version_no: int
    audience_file_counts: dict[str, int] = Field(default_factory=dict)
    serving_source: str = "db"


class HelpKbServingSourceRequest(BaseModel):
    source: str  # "files" | "db"


class HelpKbVersionOut(BaseModel):
    """版メタデータ（内容は含めない — 数値は事実の集計のみ）。"""
    version_no: int
    created_at: str = ""
    created_by: Optional[str] = None
    audience_file_counts: dict[str, int] = Field(default_factory=dict)


class HelpKbVersionsResponse(BaseModel):
    versions: list[HelpKbVersionOut] = Field(default_factory=list)
