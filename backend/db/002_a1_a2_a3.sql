-- ============================================================
-- Migration 002: A1/A2/A3 機能追加
-- ============================================================

-- ============================================================
-- A1: コース構築チャット履歴の永続化
-- ============================================================

CREATE TABLE IF NOT EXISTS course_builder_sessions (
    id          TEXT PRIMARY KEY,
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       TEXT NOT NULL DEFAULT '新しいセッション',
    history     JSONB NOT NULL DEFAULT '[]',
    course_draft JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cb_sessions_user ON course_builder_sessions(user_id);

-- ============================================================
-- A2: 教員作成コースの学生共有機能
-- ============================================================

ALTER TABLE learning_courses
    ADD COLUMN IF NOT EXISTS is_template  BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS is_published BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS owner_id     UUID REFERENCES users(id),
    ADD COLUMN IF NOT EXISTS cloned_from  TEXT REFERENCES learning_courses(id);

-- 既存レコードの owner_id を user_id で補完
UPDATE learning_courses SET owner_id = user_id WHERE owner_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_courses_published ON learning_courses(is_published, is_template)
    WHERE is_published = true AND is_template = true;
