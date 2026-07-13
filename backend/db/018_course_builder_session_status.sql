-- ============================================================
-- Migration 018: コース構築セッション ステータス・命名改善 (Issue #310)
-- ============================================================
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う（冪等・毎起動再実行）。

ALTER TABLE course_builder_sessions
    ADD COLUMN IF NOT EXISTS source_file_name  TEXT,
    ADD COLUMN IF NOT EXISTS display_name      TEXT,
    ADD COLUMN IF NOT EXISTS status            TEXT NOT NULL DEFAULT 'draft',
    ADD COLUMN IF NOT EXISTS published_course_id TEXT;
