-- ============================================================
-- Migration 003: つまづきデータ蓄積テーブル
-- ============================================================

CREATE TABLE IF NOT EXISTS unanswered_query_logs (
    id          TEXT PRIMARY KEY,
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    course_id   TEXT NOT NULL,
    topic_id    TEXT NOT NULL,
    question    TEXT NOT NULL,
    asked_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_unanswered_course ON unanswered_query_logs(course_id);
CREATE INDEX IF NOT EXISTS idx_unanswered_user   ON unanswered_query_logs(user_id);
