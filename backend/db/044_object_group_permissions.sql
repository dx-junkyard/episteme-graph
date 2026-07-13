-- ============================================================
-- Migration 044: グループ権限テーブルの統合（アーキテクチャ整理 Tier 3-14）
-- ============================================================
--
-- 背景:
--   course_group_permissions（migration 010）と document_group_permissions
--   （migration 035）は、permission 語彙（viewer/editor）・PK 形・インデックス3種まで
--   完全に相似の構造を持つコピペ実装だった（ORM モデルは両テーブルとも存在せず
--   生SQLのみで参照されている）。2枚を object_type ポリモーフィックな1枚
--   （object_group_permissions）に統合する。
--
-- 設計（V層 migration 037 の shared_versions 系と同じポリモーフィック方式を踏襲）:
--   - object_type ∈ {'course', 'document'} + object_id TEXT。
--   - course: learning_courses.id（TEXT）をそのまま使う。
--   - document: documents.id（UUID）を正規化した小文字の canonical text
--     （`CAST(... AS uuid)::text`）を使う。大文字小文字の揺れによる不一致を防ぐため、
--     書き込み・読み取りの両方でこの正規化を通すこと。
--   - object_id には FK を張らない（ポリモーフィックな参照先を単一の REFERENCES で
--     表現できないため）。旧テーブルは course_id/document_id にそれぞれ
--     ON DELETE CASCADE の FK を持っていたが、本テーブルにはその代替が無い。
--     そのため、course / document の削除経路（core/versioning/deletion.py の
--     _purge_course / _purge_document、backend/api/routes/admin.py の
--     delete_material / delete_course、backend/api/services.py の
--     delete_course_data）は、削除処理内で明示的に
--     `DELETE FROM object_group_permissions WHERE object_type = ... AND object_id = ...`
--     を行うこと（孤児行を残さないための必須作業。CASCADE には頼れない）。
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う
-- （冪等・毎起動再実行）。
-- ============================================================

CREATE TABLE IF NOT EXISTS object_group_permissions (
    object_type TEXT NOT NULL CHECK (object_type IN ('course', 'document')),
    object_id   TEXT NOT NULL,
    group_id    UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    permission  TEXT NOT NULL DEFAULT 'viewer'
                    CHECK (permission IN ('viewer', 'editor')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (object_type, object_id, group_id)
);

CREATE INDEX IF NOT EXISTS idx_ogp_object
    ON object_group_permissions(object_type, object_id);
CREATE INDEX IF NOT EXISTS idx_ogp_group
    ON object_group_permissions(group_id);
CREATE INDEX IF NOT EXISTS idx_ogp_group_permission
    ON object_group_permissions(group_id, permission);

-- ------------------------------------------------------------
-- 旧テーブルからのデータ移行 + DROP
-- ------------------------------------------------------------
-- 旧テーブルの存在確認ガード付き（information_schema.tables）。移行済み環境
-- （旧テーブルが既に無い）での再実行は完全 no-op になる。course/document を
-- それぞれ独立した DO $$ ブロックにして、片方が無くてももう片方の移行が
-- 行われるようにする。

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'course_group_permissions'
    ) THEN
        INSERT INTO object_group_permissions
            (object_type, object_id, group_id, permission, created_at, updated_at)
        SELECT 'course', course_id, group_id, permission, created_at, updated_at
        FROM course_group_permissions
        ON CONFLICT (object_type, object_id, group_id) DO NOTHING;

        DROP TABLE course_group_permissions;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'document_group_permissions'
    ) THEN
        INSERT INTO object_group_permissions
            (object_type, object_id, group_id, permission, created_at, updated_at)
        SELECT 'document', document_id::text, group_id, permission, created_at, updated_at
        FROM document_group_permissions
        ON CONFLICT (object_type, object_id, group_id) DO NOTHING;

        DROP TABLE document_group_permissions;
    END IF;
END $$;
