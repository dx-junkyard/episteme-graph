-- ============================================================
-- Migration 045: 通知テーブルの統合（アーキテクチャ整理 Tier 3-15）
-- ============================================================
--
-- 背景:
--   share_notifications（migration 037、V層）と user_notifications（migration 038、
--   状態管理・通知基盤）は、id UUID PK / recipient_id / payload / created_at / read_at が
--   完全に同型のコピペ実装だった（ORM モデルは無く、両方とも生SQLのみで参照）。
--   差分は V層側の object_type/object_id/kind CHECK(4値)/release_id FK/acted_at
--   （書込ゼロではないが実運用上は死列。#既知の限界、本 migration では acted_at の
--   意味論自体は直さない）と、status層側の entity_type/entity_id/kind CHECK なし
--   （Python 語彙のみ）/event_id FK/dismissed_at。
--
-- 設計（object_group_permissions 統合＝migration 044 と同じ「最終状態への巻き戻し」
-- パターンを踏襲）:
--   - user_notifications を存続テーブルとし、V層固有の列（source/release_id/acted_at）
--     を ALTER で追加する。
--   - source TEXT で由来を区別する（'status' = 状態管理・通知基盤由来 / 'shared' = V層由来）。
--     entity_type/entity_id 列は両層で共有する（V層由来行は entity_type に旧 object_type の
--     値 'course'/'document' がそのまま入る。status層の 'material'/'course' と混在するが、
--     source で区別できるため実害はない。値のリネームは意図的に行わない — 既存フロント
--     （versioning.js の entity_type 依存）との互換を壊さないため）。
--   - kind に CHECK 制約は付けない。状態管理・通知基盤（migration 038）はもともと
--     open-vocab 設計（Python 側 core/status/schema.py の NOTIFICATION_KINDS がゲート）で、
--     V層（migration 037）は DB CHECK(4値) を持っていた。本統合では open-vocab 側に
--     合わせる。V層の4値ゲートは core/versioning/schema.py の NOTIFICATION_KINDS が
--     引き続き Python 側で担う（core/versioning/notifications.notify_users が
--     kind not in NOTIFICATION_KINDS を弾く）。DB 制約を先に緩めておくことで、将来
--     状態層・V層以外の通知 source が増えても ALTER 不要にする。
--
-- 移行:
--   share_notifications が存在する環境でのみ INSERT ... SELECT で複製 → DROP TABLE。
--   ON CONFLICT (id) DO NOTHING なので再実行しても重複しない。旧テーブルが既に無い
--   （移行済み）環境での再実行は完全 no-op。
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う
-- （冪等・毎起動再実行）。
-- ============================================================

-- ------------------------------------------------------------
-- 1) user_notifications の拡張（V層列の追加）
-- ------------------------------------------------------------
ALTER TABLE user_notifications
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'status'
        CHECK (source IN ('status', 'shared'));

ALTER TABLE user_notifications
    ADD COLUMN IF NOT EXISTS release_id UUID REFERENCES shared_versions(id);

ALTER TABLE user_notifications
    ADD COLUMN IF NOT EXISTS acted_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_user_notif_source
    ON user_notifications(recipient_id, source, read_at, created_at DESC);

-- ------------------------------------------------------------
-- 2) share_notifications からのデータ移行 + DROP
-- ------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'share_notifications'
    ) THEN
        INSERT INTO user_notifications
            (id, recipient_id, kind, entity_type, entity_id, release_id,
             payload, created_at, read_at, acted_at, source)
        SELECT id, recipient_id, kind, object_type, object_id, release_id,
               payload, created_at, read_at, acted_at, 'shared'
        FROM share_notifications
        ON CONFLICT (id) DO NOTHING;

        DROP TABLE share_notifications;
    END IF;
END $$;
