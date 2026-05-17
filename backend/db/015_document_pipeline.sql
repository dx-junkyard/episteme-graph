-- Migration 015: Document-first analysis pipeline (issue #226)
--
-- 変更概要:
--   1. chunks に section_id / block_ids / source_metadata カラムを追加
--      （DocumentStructureAgent ベースの section-aware chunking 用）
--   2. theory_components / theory_component_links / theory_component_graphs の
--      course_id を nullable 化し、document-first な永続化を許す
--   3. theory_components / theory_component_graphs に document_id を追加し、
--      course に依存しない document scope での集計を可能にする
--   4. document_embeddings テーブル新設（DSL graph などの derived embedding 用）
--   5. document_analysis_runs テーブル新設（agent pipeline の実行履歴と
--      stage 単位の状況・raw output を保持）
--
-- すべて IF NOT EXISTS / DROP CONSTRAINT IF EXISTS で冪等。

-- ----------------------------------------------------------------------------
-- chunks: section-aware metadata
-- ----------------------------------------------------------------------------
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS section_id      TEXT;
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS block_ids       JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS source_metadata JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_chunks_section ON chunks(section_id);

-- ----------------------------------------------------------------------------
-- theory_* : course_id を nullable に。document_id を追加。
-- ----------------------------------------------------------------------------
ALTER TABLE theory_components ALTER COLUMN course_id DROP NOT NULL;
ALTER TABLE theory_components ADD COLUMN IF NOT EXISTS document_id TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_theory_components_document ON theory_components(document_id);

ALTER TABLE theory_component_links ALTER COLUMN course_id DROP NOT NULL;
ALTER TABLE theory_component_links ADD COLUMN IF NOT EXISTS document_id TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_theory_component_links_document ON theory_component_links(document_id);

ALTER TABLE theory_component_graphs ALTER COLUMN course_id DROP NOT NULL;
-- 既存の UNIQUE(course_id, document_id) は course_id が NULL になる行で
-- 重複保存できなくなるため、document_id 単独の UNIQUE 制約を追加する。
-- 既存環境には同一 document_id の graph が複数残っている場合があるため、
-- 制約追加前に最新の updated_at / created_at を持つ 1 行へ畳む。
DELETE FROM theory_component_graphs t
USING (
    SELECT
        id,
        row_number() OVER (
            PARTITION BY document_id
            ORDER BY updated_at DESC, created_at DESC, id DESC
        ) AS rn
    FROM theory_component_graphs
) d
WHERE t.id = d.id
  AND d.rn > 1;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'theory_component_graphs_document_uq'
    ) THEN
        ALTER TABLE theory_component_graphs
            ADD CONSTRAINT theory_component_graphs_document_uq UNIQUE (document_id);
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- document_embeddings: derived embedding 保存口
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS document_embeddings (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id     TEXT NOT NULL,
    material_id     TEXT,
    embedding_type  TEXT NOT NULL,
    source_version  TEXT NOT NULL DEFAULT 'v1',
    text            TEXT NOT NULL,
    embedding       vector(3072),
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(document_id, embedding_type, source_version)
);

CREATE INDEX IF NOT EXISTS idx_document_embeddings_document ON document_embeddings(document_id);
CREATE INDEX IF NOT EXISTS idx_document_embeddings_type     ON document_embeddings(embedding_type);

-- ----------------------------------------------------------------------------
-- document_analysis_runs: agent pipeline の実行履歴
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS document_analysis_runs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id     TEXT NOT NULL,
    material_id     TEXT,
    cartridge_id    TEXT,
    status          TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    current_stage   TEXT,
    error_message   TEXT NOT NULL DEFAULT '',
    stage_outputs   JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_document_analysis_runs_document ON document_analysis_runs(document_id);
CREATE INDEX IF NOT EXISTS idx_document_analysis_runs_status   ON document_analysis_runs(status);
