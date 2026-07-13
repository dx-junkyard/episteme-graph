-- ============================================================
-- Migration 038: 状態管理・通知基盤 — 遷移イベント + 統合通知インボックス
-- ============================================================
--
-- 背景:
--   教材（documents / document_analysis_runs）・コース（learning_courses /
--   background_tasks / lecture_audio_cache）の「現在状態」は既存テーブルからの
--   決定論的な投影で得られる（core/status/projector.py, 保存しない）。本 migration が
--   追加するのは「遷移が起きた事実」（status_events, append-only）と、それを
--   ユーザーに知らせるための汎用インボックス（user_notifications, share_notifications
--   と同形・V層非改変）のみ。
--
-- 設計:
--   - status_events: watcher（watermark scan）が document_analysis_runs /
--     background_tasks の status 遷移（completed/failed）を検知して書く事実ストリーム。
--     UNIQUE(source_table, source_id, event_kind) で冪等（再スキャンしても二重発火しない）。
--   - user_notifications: v1 は完了・失敗の6種（教材解析・コース原稿・コース音声）に限定
--     （段階登録。通知過多は通知が無いのと同じ）。既読/却下は行削除せず read_at/dismissed_at
--     で状態遷移する（S4）。
--
-- 正本設計書: docs/features/status_notification_design.md
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う（冪等・毎起動再実行）。
-- 全文 IF NOT EXISTS で冪等。
-- ============================================================

-- ------------------------------------------------------------
-- 1) status_events — 遷移イベント（append-only の事実ストリーム）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS status_events (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_kind   TEXT NOT NULL,             -- 'material.analysis_completed' 等
    entity_type  TEXT NOT NULL,             -- 'material' | 'course'
    entity_id    TEXT NOT NULL,
    from_state   TEXT NOT NULL DEFAULT '',
    to_state     TEXT NOT NULL,
    source_table TEXT NOT NULL,             -- 遷移の証跡テーブル
    source_id    TEXT NOT NULL,             -- 証跡行の ID
    payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at  TIMESTAMPTZ NOT NULL,      -- 証跡側のタイムスタンプ
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_table, source_id, event_kind)
);
CREATE INDEX IF NOT EXISTS idx_status_events_entity ON status_events(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_status_events_watermark ON status_events(source_table, occurred_at DESC);

-- ------------------------------------------------------------
-- 2) user_notifications — 汎用通知インボックス（share_notifications と同形・V層非改変）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_notifications (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recipient_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,
    entity_type  TEXT NOT NULL,
    entity_id    TEXT NOT NULL,
    event_id     UUID REFERENCES status_events(id),
    payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    read_at      TIMESTAMPTZ,
    dismissed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_user_notif_recipient
    ON user_notifications(recipient_id, read_at, created_at DESC);
