-- Migration 041: 画像読み取りパイプライン（figure_image_extraction / apparatus_semantics）
-- 正本: docs/features/image_pipeline_knowledge_library_design.md (§3-2, §4-3, §5-5, §8)
--
-- 変更概要:
--   1. document_analysis_runs に options（アップロード時オプションのスナップショット）を追加
--   2. document_figures テーブル新設（PDF から抽出した図画像のレジストリ）
--   3. theory_components.component_type の CHECK 制約に apparatus / instrument / part を追加
--      （装置・実験機器コンポーネントを候補として組み立てられるようにする）
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う（冪等・毎起動再実行）。
-- すべて IF NOT EXISTS / DO ブロックの存在チェックで冪等。

-- ----------------------------------------------------------------------------
-- document_analysis_runs: アップロード時オプションのスナップショット（§3-2）
-- ----------------------------------------------------------------------------
-- stage_outputs（resume 用 artifact）とは意味が異なるため相乗りしない。
-- run ごとに「このときは画像解析あり/なし」が残り、再現性・監査に使える。
ALTER TABLE document_analysis_runs ADD COLUMN IF NOT EXISTS options JSONB NOT NULL DEFAULT '{}'::jsonb;

-- ----------------------------------------------------------------------------
-- document_figures: PDF から抽出した図画像のレジストリ（§4-3）
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS document_figures (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id TEXT NOT NULL,
    run_id UUID,                        -- 抽出した解析 run
    figure_key TEXT NOT NULL,           -- 正規化ラベル（例 'fig_3'）または 'p{page}_i{n}'
    figure_label TEXT,                  -- caption 由来の表示ラベル（'Figure 3' 等）
    page INT,
    bbox JSONB,
    caption_block_id TEXT,
    caption_text TEXT,
    minio_key TEXT NOT NULL,
    extraction_method TEXT NOT NULL CHECK (extraction_method IN ('embedded','region_render')),
    region_confidence REAL,
    status TEXT NOT NULL DEFAULT 'extracted' CHECK (status IN ('extracted','failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(document_id, figure_key)     -- 再解析は upsert（画像は最新 run で置換）
);

CREATE INDEX IF NOT EXISTS idx_document_figures_doc ON document_figures(document_id);

-- ----------------------------------------------------------------------------
-- theory_components.component_type: apparatus / instrument / part を追加（§5-5）
-- ----------------------------------------------------------------------------
-- 旧定義（backend/db/013_theory_components.sql）:
--   CHECK (component_type IN ('theory', 'concept', 'law', 'mechanism', 'operator', 'observation'))
--   カラム定義に直付けされた無名 CHECK のため、自動命名規則により
--   制約名は theory_components_component_type_check になる
--   （同テーブルの claim_type 系で実際に確認済みの命名規則と同型）。
-- 新定義: 上記 6 語彙 + apparatus / instrument / part。
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'theory_components_component_type_check'
          AND pg_get_constraintdef(oid) NOT LIKE '%%apparatus%%'
    ) THEN
        ALTER TABLE theory_components DROP CONSTRAINT theory_components_component_type_check;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'theory_components_component_type_check'
    ) THEN
        ALTER TABLE theory_components
            ADD CONSTRAINT theory_components_component_type_check
            CHECK (component_type IN (
                'theory', 'concept', 'law', 'mechanism', 'operator', 'observation',
                'apparatus', 'instrument', 'part'
            ));
    END IF;
END $$;
