-- ============================================================
-- Migration 018: コース構築セッション ステータス・命名改善 (Issue #310)
-- ============================================================

ALTER TABLE course_builder_sessions
    ADD COLUMN IF NOT EXISTS source_file_name  TEXT,
    ADD COLUMN IF NOT EXISTS display_name      TEXT,
    ADD COLUMN IF NOT EXISTS status            TEXT NOT NULL DEFAULT 'draft',
    ADD COLUMN IF NOT EXISTS published_course_id TEXT;
