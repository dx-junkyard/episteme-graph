-- ============================================================
-- Episteme Graph — PostgreSQL schema (pgvector enabled)
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- 認証・セッション管理
-- ============================================================

CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email           TEXT UNIQUE NOT NULL,
    display_name    TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'learner'
                        CHECK (role IN ('learner', 'instructor', 'admin')),
    password_hash   TEXT,
    auth_provider   TEXT DEFAULT 'local',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash      TEXT NOT NULL UNIQUE,
    expires_at      TIMESTAMPTZ NOT NULL,
    ip_address      INET,
    user_agent      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_sessions_user_id ON sessions(user_id);
CREATE INDEX idx_sessions_expires ON sessions(expires_at);

-- ============================================================
-- 教材メタデータ・書誌情報
-- ============================================================

CREATE TABLE documents (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title           TEXT NOT NULL,
    authors         TEXT[] NOT NULL DEFAULT '{}',
    year            INTEGER,
    doc_type        TEXT NOT NULL DEFAULT 'textbook'
                        CHECK (doc_type IN ('textbook', 'lecture_note', 'paper', 'report')),
    trust_level     INTEGER NOT NULL DEFAULT 3
                        CHECK (trust_level BETWEEN 1 AND 5),
    license         TEXT,
    isbn            TEXT,
    doi             TEXT,
    publisher       TEXT,
    language        TEXT DEFAULT 'en',
    source_path     TEXT,
    neo4j_node_id   TEXT,
    -- Material processing fields (migrated from Neo4j Material node)
    filename        TEXT,
    status          TEXT DEFAULT 'uploaded'
                        CHECK (status IN ('uploaded', 'processing', 'completed', 'failed')),
    knowledge_graph JSONB,
    text_length     INTEGER,
    chunk_count     INTEGER,
    uploaded_by     UUID REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_documents_uploaded_by ON documents(uploaded_by);

-- ============================================================
-- チャンク（テキスト＋埋め込み）
-- ============================================================

CREATE TABLE chunks (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index     INTEGER NOT NULL,
    text            TEXT NOT NULL,
    chapter         TEXT,
    section         TEXT,
    page_start      INTEGER,
    page_end        INTEGER,
    chunk_type      TEXT DEFAULT 'prose'
                        CHECK (chunk_type IN ('prose', 'equation', 'figure_caption',
                                              'table', 'definition', 'theorem', 'example')),
    latex_formulas  TEXT[] DEFAULT '{}',
    embedding       vector(768),
    material_id     TEXT,
    smiles_dsl      TEXT,
    variables       JSONB,
    ancestors       JSONB,
    neo4j_node_id   TEXT,
    display_text    TEXT,
    spoken_text     TEXT,
    formulas        JSONB DEFAULT '[]'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_chunks_document ON chunks(document_id);
CREATE INDEX idx_chunks_material ON chunks(material_id);

-- HNSW index on halfvec cast (pgvector limits HNSW/IVFFlat to 2000 dims;
-- casting to halfvec allows indexing 768-dim vectors)
CREATE INDEX idx_chunks_embedding ON chunks
    USING hnsw ((embedding::halfvec(768)) halfvec_cosine_ops);

-- ============================================================
-- 学習者状態
-- ============================================================

CREATE TABLE learner_profiles (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    current_level   INTEGER NOT NULL DEFAULT 1
                        CHECK (current_level BETWEEN 1 AND 5),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE learner_mastered_concepts (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    learner_id      UUID NOT NULL REFERENCES learner_profiles(id) ON DELETE CASCADE,
    concept_id      TEXT NOT NULL,
    mastered_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    confidence      REAL DEFAULT 1.0
                        CHECK (confidence BETWEEN 0.0 AND 1.0),
    UNIQUE (learner_id, concept_id)
);

CREATE INDEX idx_mastered_learner ON learner_mastered_concepts(learner_id);

CREATE TABLE learner_struggling_concepts (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    learner_id      UUID NOT NULL REFERENCES learner_profiles(id) ON DELETE CASCADE,
    concept_id      TEXT NOT NULL,
    since           TIMESTAMPTZ NOT NULL DEFAULT now(),
    attempts        INTEGER NOT NULL DEFAULT 1,
    last_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (learner_id, concept_id)
);

CREATE INDEX idx_struggling_learner ON learner_struggling_concepts(learner_id);

CREATE TABLE learner_misconception_corrections (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    learner_id      UUID NOT NULL REFERENCES learner_profiles(id) ON DELETE CASCADE,
    concept_id      TEXT NOT NULL,
    wrong_statement TEXT NOT NULL,
    correct_statement TEXT NOT NULL,
    correction_reason TEXT,
    corrected_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_corrections_learner ON learner_misconception_corrections(learner_id);

-- ============================================================
-- コース管理 (migrated from Neo4j LearningCourse node)
-- ============================================================

CREATE TABLE learning_courses (
    id              TEXT PRIMARY KEY,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    data            JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_courses_user ON learning_courses(user_id);

-- ============================================================
-- 対話・セッション履歴
-- ============================================================

CREATE TABLE chat_sessions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at        TIMESTAMPTZ,
    topic_summary   TEXT,
    concepts_touched TEXT[] DEFAULT '{}'
);

CREATE INDEX idx_chat_sessions_user ON chat_sessions(user_id);

CREATE TABLE chat_messages (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id      UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content         TEXT NOT NULL,
    citations       JSONB DEFAULT '[]',
    is_correction   BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_chat_messages_session ON chat_messages(session_id);

-- ============================================================
-- 学習チャット履歴 (migrated from Neo4j LEARNING_CHAT relationship)
-- ============================================================

CREATE TABLE learning_chat_history (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id       TEXT NOT NULL,
    topic_id        TEXT NOT NULL,
    history         JSONB NOT NULL DEFAULT '[]',
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, course_id, topic_id)
);

CREATE INDEX idx_learning_chat_user ON learning_chat_history(user_id);
CREATE INDEX idx_learning_chat_course ON learning_chat_history(course_id);
