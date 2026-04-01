-- Migration 005: background_tasks (Issue #63)
-- バックグラウンドタスクの状態管理テーブル

CREATE TABLE IF NOT EXISTS background_tasks (
    id            TEXT PRIMARY KEY,
    task_type     TEXT NOT NULL DEFAULT 'material_processing',
    status        TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    created_by    UUID REFERENCES users(id) ON DELETE SET NULL,
    result_data   JSONB,
    error_message TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_bg_tasks_status ON background_tasks(status);
CREATE INDEX IF NOT EXISTS idx_bg_tasks_created_by ON background_tasks(created_by);
