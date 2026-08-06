-- Migration 064: W層の要素語彙に evidence / derivation を追加
-- 正本: docs/features/element_deliberation_workspace_design.md §16
--       （evidence / derivation の第一級要素化。実施決定は
--         docs/architecture/admin_ux_issues_2026-08-01.md §3.4(d) / §3.3 Phase 5）
--
-- 変更概要:
--   1. deliberation_sessions.element_type の CHECK に 'evidence' / 'derivation' を追加
--   2. element_annotations.element_type の CHECK に 'evidence' / 'derivation' を追加
--
--   両型は equation と同じく独立テーブルを持たず、run の stage_outputs
--   （evidence_registry / derivation_chain artifact）で存在確認する document-scoped 要素。
--   element_id は evidence_id / derivation_id（artifact 内 ID）で、行はこれまでどおり
--   ポリモーフィック（document_id への FK なし）。孤児掃除は既存の document 削除経路
--   （core/versioning/deletion.py::_purge_document / api/routes/admin.py::delete_material）が
--   element_type を問わず document_id で消すため、追加の掃除経路は不要。
--
--   ※ element_identity_links（migration 048）の instance_element_type CHECK は **変更しない**。
--     evidence（原文の引用そのもの）/ derivation（この論文の導出手順）は共通部品化の単位では
--     ないため、同一性リンク・標準化判定の対象にしない（§16 の「できないこと」。コード側の
--     正本は core/deliberation/schema.py の IDENTITY_LINKABLE_ELEMENT_TYPES で、API は 422）。
--   ※ element_explanations（migration 056/062）の element_type CHECK も変更しない
--     （二層説明の生成対象は A層パイプラインが決めるため。読み取りは 0 件へ縮退する）。
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う
-- （冪等・毎起動再実行）。DO ブロックの存在チェックで冪等。

-- ----------------------------------------------------------------------------
-- 1. deliberation_sessions.element_type の CHECK 差し替え
-- ----------------------------------------------------------------------------
-- 旧定義（backend/db/049_deliberation_sessions_annotations.sql）はカラム定義に直付けされた
-- 無名 CHECK のため、自動命名規則により制約名は
-- deliberation_sessions_element_type_check になる（migration 062 と同型の手順）。
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'deliberation_sessions_element_type_check'
          AND pg_get_constraintdef(oid) NOT LIKE '%%derivation%%'
    ) THEN
        ALTER TABLE deliberation_sessions
            DROP CONSTRAINT deliberation_sessions_element_type_check;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'deliberation_sessions_element_type_check'
    ) THEN
        ALTER TABLE deliberation_sessions
            ADD CONSTRAINT deliberation_sessions_element_type_check
            CHECK (element_type IN (
                'figure', 'theory_component', 'theory_claim', 'equation',
                'evidence', 'derivation', 'shared_part'
            ));
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- 2. element_annotations.element_type の CHECK 差し替え
-- ----------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'element_annotations_element_type_check'
          AND pg_get_constraintdef(oid) NOT LIKE '%%derivation%%'
    ) THEN
        ALTER TABLE element_annotations
            DROP CONSTRAINT element_annotations_element_type_check;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'element_annotations_element_type_check'
    ) THEN
        ALTER TABLE element_annotations
            ADD CONSTRAINT element_annotations_element_type_check
            CHECK (element_type IN (
                'figure', 'theory_component', 'theory_claim', 'equation',
                'evidence', 'derivation', 'shared_part'
            ));
    END IF;
END $$;
