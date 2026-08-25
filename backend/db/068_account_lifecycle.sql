-- Migration 068: アカウントライフサイクル管理 — users 状態列 + auth_events（Phase 0 + 1）
--
-- 正本: docs/features/account_lifecycle_management_design.md §3.1（users 列追加）/
-- §3.2（auth_events 新設）。
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う
-- （冪等・毎起動・番号順に再実行）。列追加は ADD COLUMN IF NOT EXISTS、CHECK 制約は
-- DO ブロックの pg_constraint 存在確認、テーブル・インデックスは IF NOT EXISTS で冪等。
--
-- 設計上の要点:
--   1. AL1（users 行を物理 DELETE しない）: 削除は状態遷移（pending_deletion → deleted）+
--      墓標化で表現するため、状態語彙を users に持たせる。`status` の語彙は V層
--      shared_version_state.lifecycle（active / pending_deletion / purged）と対応させ、
--      本層固有の `suspended` を加えた4値。
--   2. AL3（失効はトークン世代で即時化）: `token_generation` を JWT の `gen` クレームと
--      照合する（既定 0 = `gen` クレームの無い旧トークンと一致するため後方互換）。
--      停止・パスワードリセットで ++ することで発行済みトークンが即時失効する。
--   3. `status_changed_by` に FK を張らない — 状態を変えた管理者自身が後に墓標化されうる
--      ため（設計書 §3.1）。表示は LEFT JOIN users で NULL 安全に解決する。
--   4. auth_events は migration 060（discuss_metric_events）型: **FK なし・append-only・
--      語彙はサーバ側ホワイトリスト**。FK を張らないのは「認証記録がユーザー行の状態
--      （墓標化）と独立に残る」ためで、AL5（削除・改変 API を作らない）/ AL8（情報を
--      落とさない）と対になる。event 語彙の正本は backend/core/auth_events.py。
--   5. auth_events に平文パスワード・ハッシュ値を入れない（AL4）。payload は
--      ホワイトリスト方式でアプリ側が組み立てる。

-- ----------------------------------------------------------------------------
-- 1. users への状態列追加（設計書 §3.1）
-- ----------------------------------------------------------------------------

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS status_changed_at TIMESTAMPTZ;
-- FK なし（墓標参照を許容 — 設計書 §3.1）
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS status_changed_by UUID;
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS status_reason TEXT;
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS token_generation INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS password_updated_at TIMESTAMPTZ;
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;
-- pending_deletion のときのみ非 NULL（purge 予定時刻）
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS purge_after TIMESTAMPTZ;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'users_status_check'
    ) THEN
        ALTER TABLE users
            ADD CONSTRAINT users_status_check
            CHECK (status IN ('active', 'suspended', 'pending_deletion', 'deleted'));
    END IF;
END $$;

-- 一覧 API の status フィルタ（`GET /api/admin/users?status=`）用。
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);

-- ----------------------------------------------------------------------------
-- 2. auth_events 新設（設計書 §3.2）
-- ----------------------------------------------------------------------------
-- FK は意図的に張らない（AL5 / AL8。テレメトリ行がユーザー行の状態遷移・墓標化と
-- 独立に残る）。行単位の削除・改変 API は作らない（append-only）。

CREATE TABLE IF NOT EXISTS auth_events (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id            UUID,                     -- 失敗時に未特定なら NULL。FK なし
    username_attempted TEXT NOT NULL DEFAULT '', -- login_failed 時の入力名（存在照合前）
    event              TEXT NOT NULL,            -- 語彙は core/auth_events.py のホワイトリスト
    ip_address         TEXT,
    user_agent         TEXT,
    payload            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_auth_events_user  ON auth_events(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_auth_events_event ON auth_events(event, created_at);
