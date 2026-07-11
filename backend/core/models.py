"""SQLAlchemy ORM models for Episteme Graph PostgreSQL tables."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> uuid.UUID:
    return uuid.uuid4()


class Base(DeclarativeBase):
    pass


# ============================================================
# 認証・セッション管理
# ============================================================

class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    email = Column(Text, unique=True, nullable=False)
    display_name = Column(Text, nullable=False)
    role = Column(Text, nullable=False, default="learner")
    password_hash = Column(Text, nullable=True)
    auth_provider = Column(Text, default="local")
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)

    sessions = relationship("SessionToken", back_populates="user", cascade="all, delete-orphan")
    learner_profile = relationship("LearnerProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    courses = relationship("LearningCourse", back_populates="user", cascade="all, delete-orphan")


class SessionToken(Base):
    __tablename__ = "sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(Text, unique=True, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    ip_address = Column(INET, nullable=True)
    user_agent = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    user = relationship("User", back_populates="sessions")


# ============================================================
# 教材メタデータ
# ============================================================

class Document(Base):
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    title = Column(Text, nullable=False)
    authors = Column(ARRAY(Text), nullable=False, default=list)
    year = Column(Integer, nullable=True)
    doc_type = Column(Text, nullable=False, default="textbook")
    trust_level = Column(Integer, nullable=False, default=3)
    license = Column(Text, nullable=True)
    isbn = Column(Text, nullable=True)
    doi = Column(Text, nullable=True)
    publisher = Column(Text, nullable=True)
    language = Column(Text, default="en")
    source_path = Column(Text, nullable=True)
    neo4j_node_id = Column(Text, nullable=True)
    # Material processing fields
    filename = Column(Text, nullable=True)
    status = Column(Text, default="uploaded")
    knowledge_graph = Column(JSONB, nullable=True)
    text_length = Column(Integer, nullable=True)
    chunk_count = Column(Integer, nullable=True)
    uploaded_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    # Visibility / access control (Issue #121)
    visibility = Column(Text, nullable=False, default="private")  # public | group | private
    group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)

    chunks = relationship("Chunk", back_populates="document", cascade="all, delete-orphan")


# ============================================================
# チャンク（テキスト＋埋め込み）
# ============================================================

class Chunk(Base):
    __tablename__ = "chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    chapter = Column(Text, nullable=True)
    section = Column(Text, nullable=True)
    page_start = Column(Integer, nullable=True)
    page_end = Column(Integer, nullable=True)
    chunk_type = Column(Text, default="prose")
    latex_formulas = Column(ARRAY(Text), default=list)
    embedding = Column(Vector(3072), nullable=True)
    material_id = Column(Text, nullable=True)
    smiles_dsl = Column(Text, nullable=True)
    variables = Column(JSONB, nullable=True)
    ancestors = Column(JSONB, nullable=True)
    neo4j_node_id = Column(Text, nullable=True)
    # Interactive Lecture Mode (Issue #66)
    display_text = Column(Text, nullable=True)
    spoken_text = Column(Text, nullable=True)
    formulas = Column(JSONB, default=list)
    # レクチャースライド同期 + 音声言語切替 (migration 040): 原稿の生成言語。
    # NULL は既存データ（日本語前提で生成済み）とみなす。
    spoken_language = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    document = relationship("Document", back_populates="chunks")


# ============================================================
# 学習者状態
# ============================================================

class LectureAudioCache(Base):
    """TTS 音声キャッシュ (Issue #66)。

    migration 040 でスライド単位 (``slide_index``) + 言語 (``language``) に拡張。
    ``UNIQUE(chunk_id, slide_index, voice)``。既存行は ``slide_index=0`` のまま有効
    （マーカーなしチャンク = 1スライドとして後方互換）。
    """
    __tablename__ = "lecture_audio_cache"
    __table_args__ = (UniqueConstraint("chunk_id", "slide_index", "voice"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    chunk_id = Column(UUID(as_uuid=True), ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False)
    slide_index = Column(Integer, nullable=False, default=0)
    voice = Column(Text, nullable=False, default="alloy")
    language = Column(Text, nullable=False, default="ja")
    # 実体は BYTEA（生SQL経由でバイナリ格納。ORM 自体は音声IOに未使用のため型不整合は挙動に影響しない）。
    audio_data = Column(Text, nullable=False)
    duration_ms = Column(Integer, nullable=False, default=0)
    word_timestamps = Column(JSONB, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)


class LearnerProfile(Base):
    __tablename__ = "learner_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    current_level = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)

    user = relationship("User", back_populates="learner_profile")
    mastered_concepts = relationship("LearnerMasteredConcept", back_populates="learner", cascade="all, delete-orphan")
    struggling_concepts = relationship("LearnerStrugglingConcept", back_populates="learner", cascade="all, delete-orphan")
    misconception_corrections = relationship("LearnerMisconceptionCorrection", back_populates="learner", cascade="all, delete-orphan")


class LearnerMasteredConcept(Base):
    __tablename__ = "learner_mastered_concepts"
    __table_args__ = (UniqueConstraint("learner_id", "concept_id"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    learner_id = Column(UUID(as_uuid=True), ForeignKey("learner_profiles.id", ondelete="CASCADE"), nullable=False)
    concept_id = Column(Text, nullable=False)
    mastered_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    confidence = Column(Integer, default=1)

    learner = relationship("LearnerProfile", back_populates="mastered_concepts")


class LearnerStrugglingConcept(Base):
    __tablename__ = "learner_struggling_concepts"
    __table_args__ = (UniqueConstraint("learner_id", "concept_id"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    learner_id = Column(UUID(as_uuid=True), ForeignKey("learner_profiles.id", ondelete="CASCADE"), nullable=False)
    concept_id = Column(Text, nullable=False)
    since = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    attempts = Column(Integer, nullable=False, default=1)
    last_attempt_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    learner = relationship("LearnerProfile", back_populates="struggling_concepts")


class LearnerMisconceptionCorrection(Base):
    __tablename__ = "learner_misconception_corrections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    learner_id = Column(UUID(as_uuid=True), ForeignKey("learner_profiles.id", ondelete="CASCADE"), nullable=False)
    concept_id = Column(Text, nullable=False)
    wrong_statement = Column(Text, nullable=False)
    correct_statement = Column(Text, nullable=False)
    correction_reason = Column(Text, nullable=True)
    corrected_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    learner = relationship("LearnerProfile", back_populates="misconception_corrections")


# ============================================================
# コース管理
# ============================================================

class BackgroundTask(Base):
    __tablename__ = "background_tasks"

    id = Column(Text, primary_key=True)
    task_type = Column(Text, nullable=False, default="material_processing")
    status = Column(Text, nullable=False, default="pending")  # pending | processing | completed | failed
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    result_data = Column(JSONB, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)


class LearningCourse(Base):
    __tablename__ = "learning_courses"

    id = Column(Text, primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = Column(Text, nullable=False)
    data = Column(JSONB, nullable=False, default=dict)
    # Visibility / access control (Issue #121)
    visibility = Column(Text, nullable=False, default="private")  # public | group | private
    group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id", ondelete="SET NULL"), nullable=True)
    description = Column(Text, nullable=False, default="")
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)

    user = relationship("User", back_populates="courses")


# ============================================================
# 対話履歴
# ============================================================

class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    topic_summary = Column(Text, nullable=True)
    concepts_touched = Column(ARRAY(Text), default=list)

    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    session_id = Column(UUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False)
    role = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    citations = Column(JSONB, default=list)
    is_correction = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    session = relationship("ChatSession", back_populates="messages")


# ============================================================
# 学習チャット履歴
# ============================================================

class LearningChatHistory(Base):
    __tablename__ = "learning_chat_history"
    __table_args__ = (UniqueConstraint("user_id", "course_id", "topic_id"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    course_id = Column(Text, nullable=False)
    topic_id = Column(Text, nullable=False)
    history = Column(JSONB, nullable=False, default=list)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)


# ============================================================
# グループ / 開示範囲（Issue #121）
# ============================================================

class Group(Base):
    __tablename__ = "groups"

    id = Column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    name = Column(Text, nullable=False)
    description = Column(Text, nullable=False, default="")
    invite_code = Column(Text, unique=True, nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)


class GroupMember(Base):
    __tablename__ = "group_members"
    __table_args__ = (UniqueConstraint("group_id", "user_id"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role = Column(Text, nullable=False, default="member")  # admin | member
    joined_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)


class GroupInvitation(Base):
    __tablename__ = "group_invitations"
    __table_args__ = (
        UniqueConstraint("group_id", "invitee_user_id", "status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    invitee_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    inviter_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status = Column(Text, nullable=False, default="pending")  # pending | accepted | declined | revoked
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)


# ============================================================
# ガイダンス層（G層）— Next Steps 却下台帳（migration 039）
# ============================================================

class AssistantStepDismissal(Base):
    """Next Steps の却下台帳（G5/P4: 復元は行削除でなく revoked への状態遷移）。

    `step_key='cue:first_login'` は操作アシスタント初回ログイン cue の
    一度きりフラグとしても流用する（設計 §8。専用テーブルを増やさない）。
    """
    __tablename__ = "assistant_step_dismissals"
    __table_args__ = (UniqueConstraint("user_id", "step_key"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    step_key = Column(Text, nullable=False)
    dismissed_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    revoked = Column(Boolean, nullable=False, default=False)
    responded_at = Column(DateTime(timezone=True), nullable=True)


# ============================================================
# U層（LLM トークン使用量推計）— llm_usage_events（migration 043）
# ============================================================

class LlmUsageEvent(Base):
    """LLM/TTS/STT 呼び出し1回分の使用量記録（append-only。行削除 API は作らない, U6）。

    FK を意図的に張らない（テレメトリ行が本体の削除を妨げたり、本体削除でカスケード
    消失したりしないため。帰属 ID は文字列/UUID として残すのみ。設計書 §5）。
    """
    __tablename__ = "llm_usage_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    occurred_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    provider = Column(Text, nullable=False)
    model = Column(Text, nullable=False)
    operation = Column(Text, nullable=False)  # chat|structured|embedding|vision|transcribe|tts
    feature = Column(Text, nullable=False, default="unattributed")
    usage_source = Column(Text, nullable=False)  # reported|estimated_tokenizer|estimated_heuristic
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    cached_tokens = Column(Integer, nullable=True)
    reasoning_tokens = Column(Integer, nullable=True)
    input_characters = Column(Integer, nullable=True)
    output_characters = Column(Integer, nullable=True)
    image_count = Column(Integer, nullable=False, default=0)
    audio_bytes = Column(BigInteger, nullable=True)
    tts_characters = Column(Integer, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    success = Column(Boolean, nullable=False, default=True)
    error_type = Column(Text, nullable=True)
    user_id = Column(UUID(as_uuid=True), nullable=True)
    document_id = Column(UUID(as_uuid=True), nullable=True)
    course_id = Column(Text, nullable=True)
    run_id = Column(UUID(as_uuid=True), nullable=True)
    metadata_json = Column("metadata", JSONB, nullable=False, default=dict)
