"""Episteme Graph — Pydantic リクエスト/レスポンスモデル定義。

main.py から分離した API 固有のスキーマを集約する。
"""

from __future__ import annotations

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str


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
    filename: str
    title: str
    status: str  # uploaded | processing | completed | failed
    uploaded_at: str
    knowledge_graph: dict | None = None
    visibility: str = "private"  # public | group | private
    group_id: str | None = None


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
    prerequisites: list[LearningPrerequisite] = []
    misconceptions: list[LearningMisconception] = []
    target_chunk_ids: list[str] = []


class ChunkContent(BaseModel):
    id: str
    text: str
    chunk_index: int
    chapter: str | None = None
    section: str | None = None


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
    mastered_concepts: int = 0
    learning_concepts: int = 0
    misconceptions: int = 0
    streak_days: int = 0
    sessions: list[LearningSession] = []


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

class LearningChatRequest(BaseModel):
    message: str
    history: list[dict] = []


class LearningChatResponse(BaseModel):
    answer: str
    course_update: dict | None = None


class LearningChatHistoryResponse(BaseModel):
    history: list[dict]


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


class CourseBuilderChatResponse(BaseModel):
    """コース構築AIチャットレスポンス。"""
    answer: str
    course_draft: dict | None = None


class CourseBuilderSessionOut(BaseModel):
    """コース構築セッション情報。"""
    session_id: str
    title: str
    created_at: str
    updated_at: str


class CourseBuilderSessionCreate(BaseModel):
    title: str = "新しいセッション"


class CourseBuilderSessionUpdate(BaseModel):
    title: str | None = None
    history: list[dict] | None = None
    course_draft: dict | None = None


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


class LectureSegment(BaseModel):
    """レクチャーモードの1セグメント（チャンク単位）。"""
    chunk_id: str
    chunk_index: int
    text: str
    spoken_text: str
    formulas: list[LectureFormulaItem] = []
    has_audio: bool = False
    duration_ms: int = 0
    segment_mode: str = "full"  # full | summary | skip


class LectureSequenceResponse(BaseModel):
    """レクチャーシーケンス API レスポンス。"""
    course_id: str
    topic_id: str
    segments: list[LectureSegment] = []
    total_segments: int = 0
    total_duration_ms: int = 0
    skipped_segments: int = 0  # 習得済みスキップ数
    summary_segments: int = 0  # 簡易版変換数


class LectureTTSRequest(BaseModel):
    """TTS 音声生成リクエスト。"""
    chunk_id: str
    voice: str = "alloy"


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


class LectureInterruptResponse(BaseModel):
    """レクチャー中断チャットレスポンス。"""
    answer: str
    resume_chunk_id: str
    resume_position_ms: int = 0
    course_update: dict | None = None


# ---------------------------------------------------------------------------
# Lecture Script Studio (Issue #70)
# ---------------------------------------------------------------------------

class LectureScriptChunkOut(BaseModel):
    """チャンク単位のレクチャースクリプト情報。"""
    chunk_id: str
    chunk_index: int
    text: str
    spoken_text: str = ""
    formulas: list[LectureFormulaItem] = []
    status: str = "ungenerated"  # ungenerated | generated | edited | audio_ready


class LectureScriptGenerateRequest(BaseModel):
    """バッチスクリプト生成リクエスト。"""
    override: bool = False  # 既存スクリプトを上書きするか
    auto_audio: bool = False  # スクリプト生成完了後、自動で音声生成タスクを起動するか (Issue #139)


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
    formulas: list[dict] = []


class LectureScriptSaveResponse(BaseModel):
    """手動スクリプト保存レスポンス。"""
    chunk_id: str
    status: str = "edited"


class LectureScriptRewriteRequest(BaseModel):
    """AI スクリプト書き換えリクエスト。"""
    prompt: str


class LectureScriptRewriteResponse(BaseModel):
    """AI スクリプト書き換えレスポンス。"""
    chunk_id: str
    spoken_text: str
    formulas: list[LectureFormulaItem] = []


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
