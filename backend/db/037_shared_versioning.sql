-- ============================================================
-- Migration 037: 共有物のバージョン管理 + 更新通知 + 削除猶予（V層）
-- ============================================================
--
-- 背景:
--   パイプライン生成物（教材の解析成果: theory_components / theory_claims /
--   theory_component_graphs + document_analysis_runs）とコース（learning_courses）は、
--   グループ権限（document_group_permissions / course_group_permissions, viewer|editor）で
--   他の教員と共有される。しかし現状は共有先が見ている内容が所有者の更新・削除で
--   一方的に変わる/消える。その上に「版発行 → 更新通知 → 同意して取り込み →
--   削除は猶予表示のうえ期限後に全ユーザーから物理削除」の閉ループ（V層）を積む。
--
-- 設計:
--   - 版 = Release（不変スナップショット）。course は {title,description,data} を保存。
--     document は analysis_run_id（既に不変）+ 表示用 manifest だけをピンする（成果物を複製しない）。
--   - 消費側は Release にピン（shared_version_subscriptions）。所有者が発行しても既存ピンは
--     動かない → 消費側は adopt でピンを最新へ進めるまで旧版を見続ける。students / 未ピンの
--     viewer は active_release_id に追従。
--   - ポリモーフィック（course=TEXT id, document=UUID）のため object_id TEXT で FK なし。
--     削除は purge_object の明示クリーンアップに一本化する（既存の手動削除も合流）。
--     document は必ず _resolve_document で documents.id::text に正規化してから版キーに使う。
--   - 発行・削除予約は所有者のみ。editor は working copy を編集できるが発行/削除はできず、
--     常に HEAD を読む（ピンしない）。監査は theory_review_events（entity_type は open vocab）。
--
-- 実際の適用は backend/api/main.py の _run_migrations()（Migration 037）で行う。
-- このファイルは正本スキーマのリファレンス。全文 IF NOT EXISTS で冪等。
-- ============================================================

-- ------------------------------------------------------------
-- 1) shared_versions — 不変 Release（発行済みスナップショット）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS shared_versions (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    object_type  TEXT NOT NULL CHECK (object_type IN ('course', 'document')),
    object_id    TEXT NOT NULL,
    version_no   INT  NOT NULL,
    snapshot     JSONB NOT NULL DEFAULT '{}'::jsonb,   -- course: {title,description,data} / document: {analysis_run_id,manifest,cartridge_id}
    note         TEXT NOT NULL DEFAULT '',
    published_by UUID REFERENCES users(id),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (object_type, object_id, version_no)
);
CREATE INDEX IF NOT EXISTS idx_shared_versions_object
    ON shared_versions(object_type, object_id, version_no DESC);

-- ------------------------------------------------------------
-- 2) shared_version_state — オブジェクト単位のライフサイクル
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS shared_version_state (
    object_type         TEXT NOT NULL CHECK (object_type IN ('course', 'document')),
    object_id           TEXT NOT NULL,
    active_release_id   UUID REFERENCES shared_versions(id),
    latest_version_no   INT  NOT NULL DEFAULT 0,
    lifecycle           TEXT NOT NULL DEFAULT 'active'
                            CHECK (lifecycle IN ('active', 'pending_deletion', 'purged')),
    delete_scheduled_at TIMESTAMPTZ,
    delete_purge_after  TIMESTAMPTZ,
    delete_scheduled_by UUID REFERENCES users(id),
    delete_reason       TEXT NOT NULL DEFAULT '',
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (object_type, object_id)
);
CREATE INDEX IF NOT EXISTS idx_svs_pending_purge
    ON shared_version_state(lifecycle, delete_purge_after)
    WHERE lifecycle = 'pending_deletion';

-- ------------------------------------------------------------
-- 3) shared_version_subscriptions — 消費者のピン
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS shared_version_subscriptions (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    object_type       TEXT NOT NULL CHECK (object_type IN ('course', 'document')),
    object_id         TEXT NOT NULL,
    subscriber_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    pinned_release_id UUID REFERENCES shared_versions(id),
    status            TEXT NOT NULL DEFAULT 'active'
                          CHECK (status IN ('active', 'unsubscribed')),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (object_type, object_id, subscriber_id)
);
CREATE INDEX IF NOT EXISTS idx_svsub_object
    ON shared_version_subscriptions(object_type, object_id);
CREATE INDEX IF NOT EXISTS idx_svsub_subscriber
    ON shared_version_subscriptions(subscriber_id, status);

-- ------------------------------------------------------------
-- 4) share_notifications — 通知インボックス
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS share_notifications (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recipient_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    object_type  TEXT NOT NULL,
    object_id    TEXT NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN
                     ('version_published', 'deletion_scheduled', 'deletion_cancelled', 'deleted')),
    release_id   UUID REFERENCES shared_versions(id),
    payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    read_at      TIMESTAMPTZ,
    acted_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_share_notif_recipient
    ON share_notifications(recipient_id, read_at, created_at DESC);

-- ------------------------------------------------------------
-- 5) component_citations（migration 021）に引用の版固定カラムを追加
--    他教員の説明を引用/再利用したとき、その時点の Release を刻んで表示を固定する。
-- ------------------------------------------------------------
ALTER TABLE component_citations
    ADD COLUMN IF NOT EXISTS source_object_type TEXT,
    ADD COLUMN IF NOT EXISTS source_object_id   TEXT,
    ADD COLUMN IF NOT EXISTS source_release_id  UUID,
    ADD COLUMN IF NOT EXISTS source_version_no  INT;
