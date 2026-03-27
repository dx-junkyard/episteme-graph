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
    arxiv_id: str = ""  # 論文IDと紐付ける場合
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


class CourseUpdateRequest(BaseModel):
    """コース更新リクエスト。部分更新に対応。"""

    title: str | None = None
    chapters: list[LearningChapter] | None = None
    topics: list[LearningTopic] | None = None
    concepts: list[LearningConcept] | None = None
    sources: list[LearningSource] | None = None


class LearningCourseOut(BaseModel):
    id: str
    title: str
    is_template: bool = False
    is_published: bool = False
    is_enrollable: bool = False  # True: 自分未登録の公開テンプレート


class LearningCourseDetail(BaseModel):
    id: str
    title: str
    chapters: list[LearningChapter] = []
    topics: list[LearningTopic] = []
    concepts: list[LearningConcept] = []
    sources: list[LearningSource] = []
    referenced_sections: list[LearningReferencedSection] = []
    progress: dict | None = None


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

class CourseBuilderChatRequest(BaseModel):
    """コース構築AIチャットリクエスト。"""
    message: str
    history: list[dict] = []
    session_id: str | None = None


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
