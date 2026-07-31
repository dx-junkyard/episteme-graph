-- Migration 062: discuss 開幕素材の格納庫拡張（element_explanations の document スコープ + role）
-- 正本: docs/features/discuss_opening_authoring_design.md §5（設計書は「061 想定」と書くが、
-- 061 は M層 llm_model_policies が消費済みのため実番号は 062）。
--
-- 変更概要:
--   1. element_explanations.element_type の CHECK に 'document' を追加
--      （element_id は document_id と同値。係留先が要素ではなく document 全体という
--        一点だけが二層説明との違いなので、新テーブルを作らずレビュー UI・一括承認 API・
--        監査語彙・権限ゲートを共有する — 設計書 §5）。
--   2. role TEXT 列を追加（NULL 許容。既存行は NULL ＝ 二層説明の説明本文）。
--      CHECK は role IS NULL OR role IN ('discussion_seed')。
--      ※ 設計書 §4.2 の 'thesis_restatement' は v1 のスコープ外（着手時判断・
--        「使わない語彙を残さない」方針）。必要になった時点で新しい番号の
--        migration で CHECK を差し替える。
--   3. kind は既存 CHECK のまま 'contextual' を使う（語彙を汚さない・設計書 §5-3）。
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う
-- （冪等・毎起動再実行）。すべて IF NOT EXISTS / DO ブロックの存在チェックで冪等。

-- ----------------------------------------------------------------------------
-- 1. element_type の CHECK 差し替え（'document' を追加）
-- ----------------------------------------------------------------------------
-- 旧定義（backend/db/056_element_explanations.sql）:
--   element_type TEXT NOT NULL
--       CHECK (element_type IN ('figure','theory_component','theory_claim','equation'))
--   カラム定義に直付けされた無名 CHECK のため、自動命名規則により制約名は
--   element_explanations_element_type_check になる（migration 041 の
--   theory_components_component_type_check 差し替えと同型の手順）。
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'element_explanations_element_type_check'
          AND pg_get_constraintdef(oid) NOT LIKE '%%document%%'
    ) THEN
        ALTER TABLE element_explanations
            DROP CONSTRAINT element_explanations_element_type_check;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'element_explanations_element_type_check'
    ) THEN
        ALTER TABLE element_explanations
            ADD CONSTRAINT element_explanations_element_type_check
            CHECK (element_type IN (
                'figure', 'theory_component', 'theory_claim', 'equation', 'document'
            ));
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- 2. role 列（生成物の役割。NULL = 二層説明の説明本文）
-- ----------------------------------------------------------------------------
ALTER TABLE element_explanations ADD COLUMN IF NOT EXISTS role TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'element_explanations_role_check'
    ) THEN
        ALTER TABLE element_explanations
            ADD CONSTRAINT element_explanations_role_check
            CHECK (role IS NULL OR role IN ('discussion_seed'));
    END IF;
END $$;

-- レビューキュー（document グループ）と配信（build_opening）が引く経路。
CREATE INDEX IF NOT EXISTS idx_element_explanations_role
    ON element_explanations(document_id, role, status)
    WHERE role IS NOT NULL;
